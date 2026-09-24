"""Stable run states and their allowed lifecycle transitions."""


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
    if current == target:
        return state
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise RuntimeError("invalid stable state transition: %s -> %s" % (current, target))
    state["status"] = target
    return state
