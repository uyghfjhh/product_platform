"""Background stable-run supervision and terminal-state ownership."""

import argparse
import sys
import time
from pathlib import Path

from framework.execution.background import start_background
from suites.stable.config import StableConfig
from suites.stable.manifest import find_workload
from suites.stable.state import StateStore
from suites.stable.lifecycle import transition_status


TERMINAL_WORKLOAD_STATES = ("completed", "failed", "stopped")


def supervisor_command(root, state_file, config_files):
    command = [
        sys.executable, "-m", "suites.stable.supervisor",
        "--root", str(root), "--state-file", str(state_file),
    ]
    for path in config_files or ():
        command.extend(("--config", str(path)))
    return command


def launch_supervisor(root, state_file, config_files=None, output_path=None):
    command = supervisor_command(root, state_file, config_files)
    process = start_background(command, cwd=root, output_path=output_path)
    process.close_output()
    return process.pid, command


def _workload_result(name, item):
    from suites.stable.runtime import jdbc_result, pgbench_result

    log = Path(item.get("log", ""))
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    workload = find_workload(name)
    return (jdbc_result(text) if workload.kind == "jdbc" else
            pgbench_result(text, allow_config_lock_conflict=name.startswith("pgbench.ha_")))


def _observe_workloads(store):
    from suites.stable.runtime import managed_pid

    observed = store.load()
    if observed.get("status") not in ("running", "degraded"):
        return False
    completed = {}
    for name, item in observed.get("workloads", {}).items():
        if item.get("status") != "running":
            continue
        if not managed_pid(item.get("pid"), item.get("fingerprint", observed.get("run_dir", ""))):
            completed[name] = _workload_result(name, item)

    def update(state):
        if state.get("status") not in ("running", "degraded"):
            return
        for name, result in completed.items():
            item = state.get("workloads", {}).get(name)
            if not item or item.get("status") != "running":
                continue
            item.update({
                "returncode": result.get("returncode"),
                "result": result,
                "status": "completed" if result["ok"] else "failed",
            })
    state = store.update(update) if completed else observed
    values = list(state.get("workloads", {}).values())
    return bool(values) and all(
        item.get("status") in TERMINAL_WORKLOAD_STATES for item in values
    )


def _claim_finalization(store):
    claimed = {"value": False}

    def update(state):
        if state.get("status") in ("running", "degraded"):
            transition_status(state, "finalizing")
            claimed["value"] = True

    store.update(update)
    return claimed["value"]


def _cleanup_runtime(runtime, state):
    from suites.stable.runtime import is_alive, stop_managed

    workloads_stopped = True
    for item in state.get("workloads", {}).values():
        stopped = stop_managed(item.get("pid"), item.get("fingerprint", state.get("run_dir", "")))
        workloads_stopped = workloads_stopped and (stopped or not is_alive(item.get("pid")))
    monitor_stopped = stop_managed(state.get("monitor_pid"), "internal-monitor")
    product_stopped = stop_managed(state.get("fbasecman_pid"), state.get("run_dir", ""))
    return {
        "workloads_stopped": workloads_stopped,
        "monitor_stopped": monitor_stopped or not is_alive(state.get("monitor_pid")),
        "fbasecman_stopped": product_stopped or not is_alive(state.get("fbasecman_pid")),
        "environment_after_cleanup": runtime.health(),
    }


def _finalize(cfg, store):
    from suites.stable.runtime import runtime_for_state

    state = store.load()
    runtime = runtime_for_state(cfg, state)
    try:
        runtime.finalize_state(state, state.get("environment_before"))
    except Exception as exc:
        transition_status(state, "failed")
        state["finalization_error"] = "%s: %s" % (type(exc).__name__, exc)
    finally:
        try:
            state["cleanup"] = _cleanup_runtime(runtime, state)
        except Exception as exc:
            if state.get("status") == "finalizing":
                transition_status(state, "failed")
            state["cleanup_error"] = "%s: %s" % (type(exc).__name__, exc)
        state["supervisor_pid"] = 0
        def persist(current):
            # stop_runtime claims stopping before terminating this process. Do
            # not resurrect a stopped run with an in-flight finalizer snapshot.
            if current.get("status") in ("stopping", "stopped"):
                return
            current.clear()
            current.update(state)
        state = store.update(persist)
        try:
            runtime.write_reports(
                state, state.get("environment_before"), state.get("environment_after"),
            )
        except Exception as exc:
            state["report_error"] = "%s: %s" % (type(exc).__name__, exc)
            if state.get("status") == "completed":
                state["status"] = "failed"
            store.save(state)


def supervise(root, state_file, config_files=None, poll_interval=1):
    cfg = StableConfig(root, [Path(item) for item in config_files or ()])
    store = StateStore(state_file)
    while True:
        state = store.load()
        if state.get("status") not in ("running", "degraded"):
            return 0
        if _observe_workloads(store) and _claim_finalization(store):
            _finalize(cfg, store)
            return 0
        time.sleep(poll_interval)


def _parser():
    parser = argparse.ArgumentParser(description="stable background supervisor")
    parser.add_argument("--root", required=True)
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--config", action="append", default=[])
    return parser


def main():
    args = _parser().parse_args()
    return supervise(Path(args.root), Path(args.state_file), args.config)


if __name__ == "__main__":
    raise SystemExit(main())
