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
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT, Settings
from .diagnostics import diagnose
from .product_catalog import ProductManifestError, discover_products
from .providers import command_for, observe_database, provider_for
from .scene import (
    emit_action,
    emit_configured_scene,
    emit_observation,
    emit_pgcluster_status,
    endpoint_id,
)
from .filestore import FileStore

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"}

# These lifecycle operations belong to the platform database deployment engine.
DEPLOYMENT_ACTIONS = frozenset({
    "deployment.validate", "deployment.status", "deployment.health",
    "deployment.doctor", "deployment.heal", "deployment.create",
    "deployment.start", "deployment.stop", "deployment.restart",
    "deployment.clean", "deployment.failover", "deployment.rejoin",
    "deployment.lag", "deployment.verify", "deployment.switchover",
    "deployment.reset", "deployment.restore",
})


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
        Action("deployment.doctor", "环境体检", "deployment"),
        Action("deployment.heal", "检查并恢复集群", "deployment", True),
        Action("deployment.create", "创建集群", "deployment", True),
        Action("deployment.start", "启动集群", "deployment", True),
        Action("deployment.stop", "停止集群", "deployment", True),
        Action("deployment.restart", "重启集群", "deployment", True),
        Action("deployment.clean", "清理集群", "deployment", True),
        Action("deployment.reset", "重置环境（杀残留进程并重启）", "deployment", True),
        Action("deployment.failover", "主备故障切换", "deployment", True),
        Action("deployment.switchover", "主备计划内切换", "deployment", True),
        Action("deployment.rejoin", "重建旧主节点", "deployment", True),
        Action("deployment.restore", "恢复配置声明的主备角色", "deployment", True),
        Action("deployment.lag", "检查复制延迟", "deployment"),
        Action("deployment.verify", "校验集群复制", "deployment"),
        Action("diagnostics.analyze", "AI 分析当前测试结果", "diagnostics"),
    )
}


def actions_for_environment(environment: dict, settings: Settings) -> list[dict]:
    """Return actions declared by the installed product package.

    Deployment lifecycle actions are platform capabilities and are added when
    the installed product declares deployment and has a configuration.
    """
    try:
        manifest = discover_products(settings.products_root).get(environment["product_id"])
    except ProductManifestError as exc:
        raise RuntimeError(f"产品目录校验失败: {exc}") from exc
    if manifest is None:
        return []
    declared = {item.id: item for item in manifest.actions}
    if environment.get("deployment_config") and "deployment" in manifest.capabilities:
        declared.update({item.id: item for item in ACTIONS.values() if item.id in DEPLOYMENT_ACTIONS})
    return [
        {"id": item.id, "title": item.title, "capability": item.capability,
         "changes_environment": item.changes_environment}
        for item in (declared[action_id] for action_id in sorted(declared))
    ]


def action_for_environment(settings: Settings, environment: dict, action_id: str) -> Action | None:
    """Resolve current action metadata from the installed product declaration."""
    allowed = next(
        (item for item in actions_for_environment(environment, settings) if item["id"] == action_id),
        None,
    )
    if allowed is None:
        return None
    if action_id in ACTIONS:
        return ACTIONS[action_id]
    return Action(
        allowed["id"], allowed["title"], allowed["capability"],
        allowed["changes_environment"],
    )


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
    spec = command_for(settings, environment, action, target, parameters)
    return spec.command, spec.cwd


def _check_database(store: FileStore, settings: Settings, task_id: str, environment: dict) -> None:
    host, port = environment["host"], environment["port"]
    store.add_event(
        task_id, "step.started", {"title": "探测数据库连接", "host": host, "port": port}
    )
    with socket.create_connection((host, port), timeout=3):
        pass
    emit_observation(store, task_id, endpoint_id(environment), "reachable", "tcp.connect", {
        "host": host, "port": port,
    })
    store.add_event(
        task_id,
        "observation.captured",
        {"title": "TCP 端口可连接", "host": host, "port": port, "state": "reachable"},
    )

    for observation in observe_database(settings, environment):
        store.add_event(task_id, "observation.captured", {
            "title": "数据库 SQL 可用" if observation.kind == "sql.version" else observation.kind,
            **observation.details, "state": observation.state,
        })
        emit_observation(store, task_id, endpoint_id(environment), observation.state,
                         observation.kind, observation.details)


def _run_command(
    store: FileStore, task_id: str, command: list[str], cwd: Path,
    changes_environment: bool, observer=None,
) -> tuple[bool, str]:
    # 子进程组用于终止整条命令链；stdout/stderr 原样追加到本次证据文件。
    log_dir = store.platform_dir / "operations"
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
        env = os.environ.copy()
        env["PRODUCT_PLATFORM_TASK_ID"] = task_id
        current_pp = env.get("PYTHONPATH", "")
        paths_to_add = [str(cwd), str(ROOT / "backend"), str(ROOT)]
        env["PYTHONPATH"] = ":".join(p for p in paths_to_add if Path(p).is_dir()) + ((":" + current_pp) if current_pp else "")
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
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
        "执行完成" if returncode == 0 else f"产品工具退出码 {returncode}；见原始日志",
    )


def run_task(store: FileStore, settings: Settings, task_id: str) -> None:
    task = store.get_task(task_id)
    if not task or not store.transition_task(task_id, ("QUEUED",), "RUNNING"):
        return
    environment = store.get_environment(task["environment_id"])
    if environment is None:
        store.finish_task(task_id, ("RUNNING",), "FAILED", "环境已不存在")
        return
    action = action_for_environment(settings, environment, task["action"])
    if action is None:
        store.finish_task(task_id, ("RUNNING",), "FAILED", "产品或动作已不可用")
        return
    parameters = json.loads(task["parameters"])
    execution_finished = False
    try:
        lock = nullcontext() if action.id == "diagnostics.analyze" else environment_lock(settings.platform_dir, environment["id"])
        with lock:
            if action.id == "diagnostics.analyze":
                result = store.get_result(environment["product_id"], environment["id"],
                                          task["target"], parameters.get("profile", "default"))
                if result is None:
                    raise ValueError("当前环境没有该测试目标的结果")
                store.add_event(task_id, "step.started", {"title": "收集证据并分析代码与提交"})
                content = diagnose(store, settings, result)
                store.add_event(task_id, "diagnosis.ready", {
                    "target": result["target"], "facts": len(content["analysis"]["facts"]),
                })
                success, reason = True, "AI 诊断已生成；请核对证据与候选提交"
            elif action.id == "database.check":
                emit_configured_scene(store, settings, task_id, environment)
                emit_action(store, task_id, environment, action.id, task["target"], "started")
                _check_database(store, settings, task_id, environment)
                emit_action(store, task_id, environment, action.id, task["target"], "finished", True)
                success, reason = True, "连接正常"
            else:
                emit_configured_scene(store, settings, task_id, environment)
                command, cwd = command_for_task(
                    settings, environment, task["action"], task["target"], parameters
                )
                provider = provider_for(settings, environment["product_id"])
                observer_factory = getattr(provider, "progress_observer", None)
                observer = (
                    observer_factory(settings, environment, action.id, task["target"], time.time())
                    if callable(observer_factory) else None
                )
                emit_action(store, task_id, environment, action.id, task["target"], "started")
                try:
                    success, reason = _run_command(
                        store, task_id, command, cwd, action.changes_environment,
                        observer=observer,
                    )
                except Exception:
                    emit_action(store, task_id, environment, action.id, task["target"], "finished")
                    raise
                emit_action(store, task_id, environment, action.id, task["target"], "finished", success)
                after_command = getattr(provider, "after_command", None)
                if callable(after_command):
                    after_command(store, settings, environment, task_id, action.id, success)
                if action.id.startswith("deployment.") and action.id != "deployment.validate":
                    emit_pgcluster_status(store, settings, task_id, environment)
        execution_finished = True
        current = store.get_task(task_id)
        if current and current["cancel_requested"]:
            terminal = (
                "RECOVERY_REQUIRED" if action.changes_environment else "CANCELLED"
            )
        else:
            terminal = "SUCCEEDED" if success else "FAILED"

        # Publish the current result before the task becomes terminal. Readers
        # should never observe "finished" while still seeing the prior result.
        if action.capability == "tests":
            publisher = getattr(provider_for(settings, environment["product_id"]), "publish_result", None)
            if callable(publisher):
                terminal, reason = publisher(store, settings, environment, current, terminal, reason)
    except Exception as exc:  # noqa: BLE001 - persist any worker failure as a terminal task
        terminal = (
            "RECOVERY_REQUIRED"
            if action.changes_environment and not execution_finished
            and (store.get_task(task_id) or {}).get("process_id")
            else "FAILED"
        )
        reason = str(exc)
        store.add_event(task_id, "operation.error", {"message": str(exc)})
    store.finish_task(task_id, ("RUNNING", "CANCELLING"), terminal, reason)
