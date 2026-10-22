"""任务执行引擎（业务动作目录与命令拼装见 :mod:`.action_catalog`）。"""

import json
import os
import re
import select
import signal
import socket
import subprocess
import time
from contextlib import nullcontext
from pathlib import Path

from .action_catalog import (
    ACTIONS,
    DEPLOYMENT_ACTIONS,
    TERMINAL,
    Action,
    action_for_environment,
    actions_for_environment,
    command_for_task,
)
from .config import ROOT, Settings
from .diagnostics import diagnose
from .filestore import FileStore
from .providers import observe_database, provider_extensions
from .resources import resource_lock
from .scene import (
    emit_action,
    emit_configured_scene,
    emit_observation,
    emit_pgcluster_status,
    endpoint_id,
)


def _check_database(store: FileStore, settings: Settings, task_id: str, environment: dict) -> None:
    host, port = environment["host"], environment["port"]
    store.tasks.add_event(
        task_id, "step.started", {"title": "探测数据库连接", "host": host, "port": port}
    )
    with socket.create_connection((host, port), timeout=3):
        pass
    emit_observation(store, task_id, endpoint_id(environment), "reachable", "tcp.connect", {
        "host": host, "port": port,
    })
    store.tasks.add_event(
        task_id,
        "observation.captured",
        {"title": "TCP 端口可连接", "host": host, "port": port, "state": "reachable"},
    )

    for observation in observe_database(settings, environment):
        store.tasks.add_event(task_id, "observation.captured", {
            "title": "数据库 SQL 可用" if observation.kind == "sql.version" else observation.kind,
            **observation.details, "state": observation.state,
        })
        emit_observation(store, task_id, endpoint_id(environment), observation.state,
                         observation.kind, observation.details)


_PROGRESS_RE = re.compile(r"\[(\d+)/(\d+)\]\s+(\S+)")


def _poll_observer(observer, store: FileStore, task_id: str) -> None:
    """Progress observation is advisory — a product observer bug must never
    kill the command it watches."""
    try:
        observer.poll(store, task_id)
    except Exception as exc:  # noqa: BLE001 - recorded for diagnosis, not fatal
        try:
            store.tasks.add_event(task_id, "observer.error", {"error": str(exc)})
        except Exception:  # noqa: BLE001 - store may itself be unavailable
            pass


def _run_command(
    store: FileStore, task_id: str, command: list[str], cwd: Path,
    changes_environment: bool, observer=None,
) -> tuple[bool, str]:
    # 子进程组用于终止整条命令链；stdout/stderr 原样追加到本次证据文件。
    log_dir = store.logs_dir / "operations"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / (task_id + ".log")
    store.tasks.add_event(
        task_id,
        "step.started",
        {
            "title": "执行产品工具",
            "command": command,
            "cwd": str(cwd),
            "log": str(log_path),
        },
    )
    with log_path.open("a", encoding="utf-8") as log:
        log.write(
            "\n===== %s %s =====\n"
            % (time.strftime("%Y-%m-%d %H:%M:%S"), " ".join(command))
        )
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
        store.tasks.transition_task(task_id, ("RUNNING",), "RUNNING", process_id=process.pid)
        buffered = bytearray()
        eof = False
        stopped = False
        stopped_at = None
        try:
            while True:
                task = store.tasks.get_task(task_id)
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
                        if json.loads((task or {}).get("parameters", "{}")).get("stream_output"):
                            print(text, end="", flush=True)
                        log.flush()
                        buffered.extend(chunk)
                        while b"\n" in buffered:
                            raw_line, _, tail = buffered.partition(b"\n")
                            buffered = bytearray(tail)
                            line = raw_line.decode("utf-8", errors="replace")
                            match = _PROGRESS_RE.match(line)
                            if match:
                                store.tasks.set_progress(
                                    task_id, int(match.group(1)),
                                    int(match.group(2)), match.group(3))
                            store.tasks.add_event(
                                task_id,
                                "command.output",
                                {
                                    "line": line,
                                    "log": str(log_path),
                                },
                            )
                    else:
                        eof = True
                if process.poll() is not None and eof:
                    break
                if observer:
                    _poll_observer(observer, store, task_id)
                if eof:
                    time.sleep(0.1)
            if observer:
                _poll_observer(observer, store, task_id)
            if buffered:
                store.tasks.add_event(
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
    store.tasks.add_event(
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
    task = store.tasks.get_task(task_id)
    if not task or not store.tasks.transition_task(task_id, ("QUEUED",), "RUNNING"):
        return
    environment = store.environments.get_environment(task["environment_id"])
    if environment is None:
        store.tasks.finish_task(task_id, ("RUNNING",), "FAILED", "环境已不存在")
        return
    action = action_for_environment(settings, environment, task["action"])
    if action is None:
        store.tasks.finish_task(task_id, ("RUNNING",), "FAILED", "产品或动作已不可用")
        return
    parameters = json.loads(task["parameters"])
    execution_finished = False
    try:
        lock = nullcontext() if action.id == "diagnostics.analyze" else resource_lock(settings, environment)
        with lock:
            if parameters.get("_deployment_snapshot"):
                from .deployment.workbench import worker_environment
                environment = worker_environment(settings, store, environment, parameters["_deployment_snapshot"])
            if action.id == "diagnostics.analyze":
                result = store.results.get_result(environment["product_id"], environment["id"],
                                          task["target"], parameters.get("profile", "default"))
                if result is None:
                    raise ValueError("当前环境没有该测试目标的结果")
                store.tasks.add_event(task_id, "step.started", {"title": "收集证据并分析代码与提交"})
                content = diagnose(store, settings, result)
                store.tasks.add_event(task_id, "diagnosis.ready", {
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
                if action.capability == 'workload':
                    parameters['_workload_task_id'] = task_id
                command, cwd = command_for_task(
                    settings, environment, task["action"], task["target"], parameters
                )
                observer_factory = provider_extensions(settings, environment["product_id"]).progress_observer
                observer = (
                    observer_factory(settings, environment, action.id, task["target"], time.time())
                    if observer_factory else None
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
                if success and parameters.get("_deployment_snapshot") and action.id in {"deployment.create", "deployment.change"}:
                    store.tasks.add_event(task_id, "step.started", {"title": "部署后健康验收"})
                    health_command, health_cwd = command_for_task(settings, environment, "deployment.health", task["target"], {})
                    success, health_reason = _run_command(store, task_id, health_command, health_cwd, False)
                    reason = "部署及健康验收完成" if success else "部署命令完成，但健康验收失败：" + health_reason
                emit_action(store, task_id, environment, action.id, task["target"], "finished", success)
                after_command = provider_extensions(settings, environment["product_id"]).after_command
                if after_command:
                    after_command(store, settings, environment, task_id, action.id, success)
                if action.id.startswith("deployment.") and action.id != "deployment.validate":
                    emit_pgcluster_status(store, settings, task_id, environment)
                if action.capability == 'workload':
                    from .workloads import publish
                    publish(store,settings,environment,task_id,success)
        execution_finished = True
        current = store.tasks.get_task(task_id)
        if current and current["cancel_requested"]:
            terminal = (
                "RECOVERY_REQUIRED" if action.changes_environment else "CANCELLED"
            )
        else:
            terminal = "SUCCEEDED" if success else ("RECOVERY_REQUIRED" if action.id == "deployment.change" else "FAILED")

        # Publish the current result before the task becomes terminal. Readers
        # should never observe "finished" while still seeing the prior result.
        if action.capability == "tests":
            regression = provider_extensions(settings, environment["product_id"]).regression
            if regression:
                terminal, reason = regression.publish_result(store, settings, environment, current, terminal, reason)
    except Exception as exc:  # noqa: BLE001 - persist any worker failure as a terminal task
        terminal = (
            "RECOVERY_REQUIRED"
            if action.changes_environment and not execution_finished
            and (store.tasks.get_task(task_id) or {}).get("process_id")
            else "FAILED"
        )
        reason = str(exc)
        store.tasks.add_event(task_id, "operation.error", {"message": str(exc)})
    snapshot = parameters.get("_deployment_snapshot")
    if snapshot:
        store.environments.mark_deployment(environment["id"], snapshot["plan_id"],
                              "APPLIED" if terminal == "SUCCEEDED" else terminal)
    store.tasks.finish_task(task_id, ("RUNNING", "CANCELLING"), terminal, reason)
