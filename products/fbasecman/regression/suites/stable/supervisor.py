"""Background stable-run supervision and terminal-state ownership."""

import argparse
import sys
from pathlib import Path

from platform_regress.execution.background import start_background
from suites.stable.config import StableConfig
from suites.stable.manifest import find_workload
from suites.stable.state import StateStore

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
    from platform_regress.execution.longrun import observe_workloads
    return observe_workloads(store, _workload_result)


def _claim_finalization(store):
    from platform_regress.execution.longrun import claim_finalization
    return claim_finalization(store)


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
    from platform_regress.execution.longrun import finalize_run
    from suites.stable.runtime import runtime_for_state
    runtime = runtime_for_state(cfg, store.load())
    return finalize_run(store,
        finalize=lambda state: runtime.finalize_state(state, state.get("environment_before")),
        cleanup=lambda state: _cleanup_runtime(runtime, state),
        report=lambda state: runtime.write_reports(state, state.get("environment_before"),
                                                   state.get("environment_after")))


def supervise(root, state_file, config_files=None, poll_interval=1):
    from platform_regress.execution.longrun import supervise as supervise_workloads
    cfg = StableConfig(root, [Path(item) for item in config_files or ()])
    store = StateStore(state_file)
    return supervise_workloads(store, _workload_result, lambda: _finalize(cfg, store),
                               poll_interval=poll_interval)


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
