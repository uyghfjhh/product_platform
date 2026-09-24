"""Polling for global-cache console state transitions."""

from framework.execution.polling import PollTimeout, poll_until
from suites.global_cache.errors import GlobalCacheFailure
from suites.global_cache.state import capture_global_cache_state


def _capture_without_report_steps(rt, prefix):
    def query(sql, stem):
        return rt.console_query(sql, stem, record=False)
    return capture_global_cache_state(query, prefix, include_server=False)


def _matching_entries(state, marker):
    return [
        row for row in state["global"]
        if len(row) >= 5 and marker in row[1]
    ]


def wait_target_entries_unref(rt, marker, expected_count, timeout, interval=1.0):
    def probe():
        state = _capture_without_report_steps(rt, "wait_unref")
        return state, _matching_entries(state, marker)

    def accepted(observation):
        _, matched = observation
        return len(matched) >= expected_count and all(
            (row[4] or "").strip() == "0" for row in matched[:expected_count]
        )

    try:
        return poll_until(probe, accepted, timeout, interval)
    except PollTimeout as exc:
        matched = exc.last_value[1] if exc.last_value else []
        details = "; ".join("|".join(row) for row in matched) or "<empty>"
        raise GlobalCacheFailure(
            "wait ref_count=0 timeout after %ss for %s, latest entries: %s"
            % (timeout, marker, details)
        )


def wait_target_entries_released(rt, marker, timeout, interval=1.0):
    """Wait until every surviving target entry is zero-ref or has been evicted."""
    def probe():
        state = _capture_without_report_steps(rt, "wait_released")
        return state, _matching_entries(state, marker)

    def accepted(observation):
        _, matched = observation
        return not matched or all((row[4] or "").strip() == "0" for row in matched)

    try:
        return poll_until(probe, accepted, timeout, interval)
    except PollTimeout as exc:
        matched = exc.last_value[1] if exc.last_value else []
        details = "; ".join("|".join(row) for row in matched) or "<empty>"
        raise GlobalCacheFailure(
            "wait released timeout after %ss for %s, latest entries: %s"
            % (timeout, marker, details)
        )


def wait_capacity_entries_unref(rt, prefix, expected_count, timeout, interval=1.0):
    state, matched = wait_target_entries_unref(
        rt, "%s_" % prefix, expected_count, timeout, interval=interval
    )
    rt.summary["capacity_unref_state"] = state
    rt.summary["capacity_unref_entries"] = ["|".join(row) for row in matched]
    return state


def wait_target_entry_absent(rt, marker, timeout, interval=1.0):
    def probe():
        state = _capture_without_report_steps(rt, "wait_absent")
        return state, _matching_entries(state, marker)

    try:
        state, _ = poll_until(probe, lambda item: not item[1], timeout, interval)
        return state
    except PollTimeout as exc:
        matched = exc.last_value[1] if exc.last_value else []
        details = "; ".join("|".join(row) for row in matched) or "<empty>"
        raise GlobalCacheFailure(
            "wait absent timeout after %ss for %s, latest entries: %s"
            % (timeout, marker, details)
        )
