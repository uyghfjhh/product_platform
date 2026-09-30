"""Product-neutral persistent workload observation and finalization claims."""

import time

from .processes import managed_pid

TERMINAL_STATES = ("completed", "failed", "stopped")
ALLOWED_TRANSITIONS = {
    "stopped": {"running"},
    "running": {"degraded", "finalizing", "stopping", "completed", "failed"},
    "degraded": {"running", "finalizing", "stopping", "failed"},
    "finalizing": {"completed", "failed", "stopping"},
    "stopping": {"stopped"},
    "completed": {"running"},
    "failed": {"running"},
}


def transition_status(state, target):
    current = state.get("status", "stopped")
    if current != target:
        if target not in ALLOWED_TRANSITIONS.get(current, set()):
            raise RuntimeError(f"invalid run state transition: {current} -> {target}")
        state["status"] = target
    return state


def observe_workloads(store, result_for):
    observed = store.load()
    if observed.get("status") not in ("running", "degraded"):
        return False
    completed = {}
    for name, item in observed.get("workloads", {}).items():
        if item.get("status") == "running" and not managed_pid(
            item.get("pid"), item.get("fingerprint", observed.get("run_dir", ""))
        ):
            completed[name] = (item.get("pid"), result_for(name, item))

    def update(state):
        if state.get("status") not in ("running", "degraded"):
            return
        for name, (pid, result) in completed.items():
            item = state.get("workloads", {}).get(name)
            if item and item.get("status") == "running" and item.get("pid") == pid:
                item.update(
                    returncode=result.get("returncode"),
                    result=result,
                    status="completed" if result["ok"] else "failed",
                )

    state = store.update(update) if completed else observed
    values = list(state.get("workloads", {}).values())
    return bool(values) and all(
        item.get("status") in TERMINAL_STATES for item in values
    )


def claim_finalization(store):
    claimed = False

    def update(state):
        nonlocal claimed
        if state.get("status") in ("running", "degraded"):
            transition_status(state, "finalizing")
            claimed = True

    store.update(update)
    return claimed


def supervise(store, result_for, finalize, *, poll_interval=1):
    if poll_interval <= 0:
        raise ValueError("supervisor interval must be positive")
    while store.load().get("status") in ("running", "degraded"):
        if observe_workloads(store, result_for) and claim_finalization(store):
            finalize()
            return 0
        time.sleep(poll_interval)
    return 0


def finalize_run(store, *, finalize, cleanup, report):
    """One owner finalizes a run, preserving a concurrent stop request."""
    state = store.load()
    try:
        finalize(state)
    except Exception as exc:
        transition_status(state, "failed")
        state["finalization_error"] = f"{type(exc).__name__}: {exc}"
    try:
        state["cleanup"] = cleanup(state)
    except Exception as exc:
        state["status"] = "failed"
        state["cleanup_error"] = f"{type(exc).__name__}: {exc}"
    else:
        cleanup_result = state.get("cleanup")
        if isinstance(cleanup_result, dict) and (
            cleanup_result.get("errors")
            or any(value is False for key, value in cleanup_result.items() if key != "errors")
        ):
            state["status"] = "failed"
            state["cleanup_error"] = "cleanup reported unreclaimed resources"
    state["supervisor_pid"] = 0
    current = store.load()
    if current.get("status") in ("stopping", "stopped") or current.get(
        "run_id"
    ) != state.get("run_id"):
        return current
    # Publish the terminal state only after report creation was attempted.
    try:
        report(state)
    except Exception as exc:
        state["report_error"] = f"{type(exc).__name__}: {exc}"
        if state.get("status") == "completed":
            state["status"] = "failed"

    def persist(current):
        if current.get("status") in ("stopping", "stopped") or current.get(
            "run_id"
        ) != state.get("run_id"):
            return
        current.clear()
        current.update(state)

    return store.update(persist)


class WorkloadGroup:
    """Launch, evaluate and reclaim a foreground workload group."""

    def __init__(self, store, state, *, launch, describe, evaluate):
        self.store, self.state = store, state
        self.launch, self.describe, self.evaluate = launch, describe, evaluate
        self.launched = {}

    def run(self, workloads):
        for workload in workloads:
            process, log, command = self.launch(workload)
            name = workload.name
            # Register before persistence so a storage failure still cleans up.
            self.launched[name] = (process, log, command)
            item = self.describe(workload)
            item.update(
                pid=process.pid,
                status="running",
                log=str(log),
                command=command,
                started_at=int(time.time()),
            )
            self.state["workloads"][name].update(item)
            self.state["commands"][str(process.pid)] = self.state["run_dir"]
            self.store.save(self.state)
        for name, (process, log, command) in self.launched.items():
            rc = process.wait()
            result = self.evaluate(
                name, log.read_text(encoding="utf-8", errors="replace"), rc
            )
            self.state["workloads"][name].update(
                returncode=rc,
                status="completed" if result["ok"] else "failed",
                result=result,
            )
            self.store.save(self.state)

    def close(self):
        from .processes import stop_managed

        errors = []
        for name, (process, _, _) in self.launched.items():
            try:
                if process.poll() is None:
                    stop_managed(
                        process.pid, self.state["workloads"][name]["fingerprint"]
                    )
            except Exception as exc:
                errors.append(str(exc))
            finally:
                process.close_output()
        if errors:
            raise RuntimeError("; ".join(errors))
        return all(
            process.poll() is not None for process, _, _ in self.launched.values()
        )


def cleanup_actions(actions):
    """Attempt every independent cleanup and return failures as evidence."""
    results, errors = {}, {}
    for name, action in actions.items():
        try:
            results[name] = action()
        except Exception as exc:
            results[name] = False
            errors[name] = f"{type(exc).__name__}: {exc}"
    if errors:
        results["errors"] = errors
    return results
