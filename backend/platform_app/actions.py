"""已注册的业务动作及其可执行提供者。"""

try:
    import fcntl
except ImportError:
    fcntl = None
import hashlib
import json
import os
import select
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .cman_artifacts import CaseProgressObserver, sync_current_results
from .fbasecman_profile import legacy_root, profile_paths
from .storage import Store


TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"}


@dataclass(frozen=True)
class Action:
    id: str
    title: str
    capability: str
    changes_environment: bool = False


ACTIONS = {
    item.id: item
    for item in (
        Action("database.check", "检查数据库连接", "database"),
        Action("deployment.validate", "校验部署配置", "deployment"),
        Action("deployment.status", "查看部署状态", "deployment"),
        Action("deployment.health", "检查集群健康", "deployment"),
        Action("deployment.create", "创建集群", "deployment", True),
        Action("deployment.start", "启动集群", "deployment", True),
        Action("deployment.stop", "停止集群", "deployment", True),
        Action("deployment.restart", "重启集群", "deployment", True),
        Action("deployment.clean", "清理集群", "deployment", True),
        Action("deployment.failover", "主备故障切换", "deployment", True),
        Action("deployment.rejoin", "重建旧主节点", "deployment", True),
        Action("tests.fbasecman", "运行 fbasecman 用例", "tests", True),
        Action("tests.prepare_fbasecman", "准备 fbasecman 测试夹具", "tests", True),
        Action("tests.fbase", "运行 FBase 用例", "tests", True),
        Action("stability.fbasecman", "运行 fbasecman 常稳", "stability", True),
    )
}


def actions_for_environment(environment: dict) -> list[dict]:
    product = environment["product_id"]
    result = []
    for action in ACTIONS.values():
        if action.id.startswith("deployment.") and not environment.get(
            "deployment_config"
        ):
            continue
        if action.id == "tests.fbasecman" and product != "fbasecman":
            continue
        if action.id == "tests.prepare_fbasecman" and product != "fbasecman":
            continue
        if action.id == "tests.fbase" and product != "fbase-database":
            continue
        if action.id == "stability.fbasecman" and product != "fbasecman":
            continue
        result.append(
            {
                "id": action.id,
                "title": action.title,
                "capability": action.capability,
                "changes_environment": action.changes_environment,
            }
        )
    return result


@contextmanager
def environment_lock(data_dir: Path, environment_id: str):
    lock_dir = data_dir / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_name = hashlib.sha256(environment_id.encode("utf-8")).hexdigest()[:24]
    with (lock_dir / (lock_name + ".lock")).open("a+") as handle:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("该环境已有平台操作正在执行")
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def command_for_task(
    settings: Settings, environment: dict, action: str, target: str, parameters: dict
) -> tuple[list[str], Path]:
    if action.startswith("deployment."):
        config_file = Path(environment["deployment_config"] or "")
        if not config_file.is_file():
            raise FileNotFoundError("部署配置不存在: %s" % config_file)
        pgcluster = settings.pgcluster_root / "pgcluster"
        if not pgcluster.is_file():
            raise FileNotFoundError("pgcluster 入口不存在: %s" % pgcluster)
        cli_action = action.partition(".")[2]
        command = [
            sys.executable,
            str(pgcluster),
            "-f",
            str(config_file),
            cli_action,
            target,
        ]
        if cli_action in {"clean", "failover", "rejoin"}:
            command.append("--yes")
        return command, settings.pgcluster_root

    if action == "tests.fbasecman":
        profile, override = profile_paths(settings, environment["id"])
        if not profile.is_file() or not override.is_file():
            raise RuntimeError("请先生成 pgcluster 回归部署方案")
        context = legacy_root(settings, environment["id"]) / "output" / "env" / "test_context.yaml"
        if not context.is_file():
            raise RuntimeError("pgcluster 部署后仍需准备 fbasecman 测试夹具和 test_context.yaml")
        return [
            sys.executable, "-m", "platform_app.legacy_cman_runner",
            "--source", str(settings.fbasecman_regress_root),
            "--override", str(override), target,
        ], settings.fbasecman_regress_root

    if action == "tests.prepare_fbasecman":
        profile, override = profile_paths(settings, environment["id"])
        if not profile.is_file() or not override.is_file():
            raise RuntimeError("请先生成 pgcluster 测试部署方案")
        return [
            sys.executable, "-m", "platform_app.fbasecman_fixture",
            "--profile", str(profile), "--override", str(override),
        ], settings.data_dir

    if action == "tests.fbase":
        script = settings.fbase_regress_root / "run.sh"
        if not script.is_file():
            raise FileNotFoundError("FBase 测试入口不存在: %s" % script)
        cluster = parameters.get("cluster")
        if cluster not in {"mac", "mmr"}:
            raise ValueError("FBase 测试需要选择 mac 或 mmr 集群")
        command = [str(script), "run", cluster]
        if target != "all":
            command.append(target)
        return command, settings.fbase_regress_root

    if action == "stability.fbasecman":
        script = settings.fbasecman_regress_root / "stable.sh"
        if not script.is_file():
            raise FileNotFoundError("fbasecman 常稳入口不存在: %s" % script)
        command = [str(script), "run"]
        if target != "all":
            command.append(target)
        return command, settings.fbasecman_regress_root

    raise ValueError("该操作未注册执行器: %s" % action)


def _check_database(store: Store, task_id: str, environment: dict) -> None:
    host, port = environment["host"], environment["port"]
    store.add_event(
        task_id, "step.started", {"title": "探测数据库连接", "host": host, "port": port}
    )
    with socket.create_connection((host, port), timeout=3):
        pass
    store.add_event(
        task_id,
        "observation.captured",
        {"title": "TCP 端口可连接", "host": host, "port": port, "state": "reachable"},
    )

    if environment["product_id"] == "fbase-database":
        import psycopg

        with psycopg.connect(
            host=host,
            port=port,
            dbname=environment["database_name"],
            user=environment["database_user"],
            connect_timeout=4,
            options="-c statement_timeout=4000",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT version()")
                version = cursor.fetchone()[0]
        store.add_event(
            task_id,
            "observation.captured",
            {"title": "数据库 SQL 可用", "version": version, "state": "ready"},
        )


def _run_command(
    store: Store, task_id: str, command: list[str], cwd: Path,
    changes_environment: bool, observer: CaseProgressObserver | None = None,
) -> tuple[bool, str]:
    # 子进程组用于终止整条命令链；stdout/stderr 原样追加到本次证据文件。
    log_dir = store.path.parent / "operations"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / (task_id + ".log")
    store.add_event(
        task_id,
        "step.started",
        {
            "title": "执行产品工具",
            "command": command,
            "cwd": str(cwd),
            "log": str(log_path),
        },
    )
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            bufsize=0,
        )
        store.transition_task(task_id, ("RUNNING",), "RUNNING", process_id=process.pid)
        buffered = bytearray()
        eof = False
        stopped = False
        stopped_at = None
        try:
            while True:
                task = store.get_task(task_id)
                if task and task["cancel_requested"] and process.poll() is None:
                    if stopped_at is None:
                        os.killpg(process.pid, signal.SIGTERM)
                        stopped_at = time.monotonic()
                        stopped = True
                    elif time.monotonic() - stopped_at >= 5:
                        os.killpg(process.pid, signal.SIGKILL)
                ready, _, _ = (
                    select.select([process.stdout], [], [], 0.35)
                    if not eof
                    else ([], [], [])
                )
                if ready:
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if chunk:
                        text = chunk.decode("utf-8", errors="replace")
                        log.write(text)
                        log.flush()
                        buffered.extend(chunk)
                        while b"\n" in buffered:
                            raw_line, _, tail = buffered.partition(b"\n")
                            buffered = bytearray(tail)
                            store.add_event(
                                task_id,
                                "command.output",
                                {
                                    "line": raw_line.decode("utf-8", errors="replace"),
                                    "log": str(log_path),
                                },
                            )
                    else:
                        eof = True
                if process.poll() is not None and eof:
                    break
                if observer:
                    observer.poll(store, task_id)
                if eof:
                    time.sleep(0.1)
            if observer:
                observer.poll(store, task_id)
            if buffered:
                store.add_event(
                    task_id,
                    "command.output",
                    {
                        "line": buffered.decode("utf-8", errors="replace"),
                        "log": str(log_path),
                    },
                )
            returncode = process.wait()
        except Exception:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            raise
        finally:
            process.stdout.close()
    store.add_event(
        task_id,
        "command.finished",
        {"returncode": returncode, "log": str(log_path), "cancel_requested": stopped},
    )
    if stopped:
        return False, "已请求取消；%s" % (
            "环境操作状态需核对" if changes_environment else "任务已停止"
        )
    return (
        returncode == 0,
        "执行完成" if returncode == 0 else "产品工具退出码 %s；见原始日志" % returncode,
    )


def run_task(store: Store, settings: Settings, task_id: str) -> None:
    task = store.get_task(task_id)
    if not task or not store.transition_task(task_id, ("QUEUED",), "RUNNING"):
        return
    environment = store.get_environment(task["environment_id"])
    if environment is None:
        store.transition_task(task_id, ("RUNNING",), "FAILED", reason="环境已不存在")
        return
    action = ACTIONS[task["action"]]
    parameters = json.loads(task["parameters"])
    try:
        with environment_lock(settings.data_dir, environment["id"]):
            if action.id == "database.check":
                _check_database(store, task_id, environment)
                success, reason = True, "连接正常"
            else:
                command, cwd = command_for_task(
                    settings, environment, task["action"], task["target"], parameters
                )
                observer = (CaseProgressObserver(settings, environment["id"],
                                                task["target"], time.time())
                            if action.id == "tests.fbasecman" else None)
                success, reason = _run_command(
                    store, task_id, command, cwd, action.changes_environment,
                    observer=observer,
                )
        current = store.get_task(task_id)
        if current and current["cancel_requested"]:
            terminal = (
                "RECOVERY_REQUIRED" if action.changes_environment else "CANCELLED"
            )
        else:
            terminal = "SUCCEEDED" if success else "FAILED"
        store.transition_task(
            task_id, ("RUNNING", "CANCELLING"), terminal, reason=reason
        )
    except Exception as exc:
        terminal = (
            "RECOVERY_REQUIRED"
            if action.changes_environment
            and (store.get_task(task_id) or {}).get("process_id")
            else "FAILED"
        )
        store.add_event(task_id, "operation.error", {"message": str(exc)})
        store.transition_task(
            task_id, ("RUNNING", "CANCELLING"), terminal, reason=str(exc)
        )
    final = store.get_task(task_id)
    store.add_event(
        task_id,
        "operation.finished",
        {"status": final["status"], "reason": final["reason"]},
    )
    if action.id == "tests.fbasecman":
        count = sync_current_results(store, settings, environment, task["target"],
                                     final["started_at"] or final["created_at"])
        if count == 0:
            store.put_result(environment["product_id"], environment["id"], task["target"],
                             parameters.get("profile", "default"),
                             "ERROR", "本次没有生成可核对的用例报告；见命令日志",
                             str(settings.data_dir / "operations" / (task_id + ".log")))
    elif action.id == "tests.fbase":
        store.put_result(
            environment["product_id"],
            environment["id"],
            task["target"],
            parameters.get("profile", "default"),
            "PASS" if final["status"] == "SUCCEEDED" else "ERROR",
            final["reason"],
            str(settings.data_dir / "operations" / (task_id + ".log")),
        )
