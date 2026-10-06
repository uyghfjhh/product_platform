"""Scoped Studio cancellation and bounded, redacted execution measurements."""

import hashlib
import re
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

_LOCK = threading.RLock()
_ACTIVE = {}
_CANCELLED = {}
_INSIGHTS = {}


def scope(environment):
    return (
        environment.get("id", ""),
        environment["host"],
        environment["port"],
        environment["database_name"],
        environment["database_user"],
    )


def token(value):
    return str(UUID(value))


@contextmanager
def track(environment, request_id, connection):
    if not request_id:
        yield
        return
    key = (scope(environment), token(request_id))
    with _LOCK:
        now = time.monotonic()
        for previous, stamp in list(_CANCELLED.items()):
            if now - stamp > 30:
                _CANCELLED.pop(previous, None)
        if key in _CANCELLED:
            raise ValueError("查询已取消")
        if key in _ACTIVE:
            raise ValueError("查询请求标识已使用")
        _ACTIVE[key] = connection
    try:
        yield
    finally:
        with _LOCK:
            _ACTIVE.pop(key, None)


def cancel(environment, request_id):
    key = (scope(environment), token(request_id))
    with _LOCK:
        now=time.monotonic()
        for previous,stamp in list(_CANCELLED.items()):
            if now-stamp>30:_CANCELLED.pop(previous,None)
        if len(_CANCELLED)>=2048:
            _CANCELLED.pop(next(iter(_CANCELLED)))
        _CANCELLED[key] = time.monotonic()
        connection = _ACTIVE.get(key)
    if connection is not None:
        connection.cancel()
    return {"cancel_requested": True, "active": connection is not None}


def record(environment, sql, duration_ms, rows):
    # Never retain parameter values, SQL comments or literal credentials.
    text = re.sub(r"/\*.*?\*/|--[^\n]*", " ", sql, flags=re.S)
    text = re.sub(r"'(?:''|[^'])*'", "?", text)
    text = re.sub(r"\$\w*\$.*?\$\w*\$", "?", text, flags=re.S)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "?", text)
    text = " ".join(text.split())[:2000]
    identity = hashlib.sha256(text.encode()).hexdigest()[:24]
    now = datetime.now(UTC).isoformat()
    with _LOCK:
        queries = _INSIGHTS.setdefault(scope(environment), {})
        item = queries.setdefault(
            identity,
            {
                "id": identity,
                "query": text,
                "count": 0,
                "duration": 0,
                "maxDurationMs": 0,
                "minDurationMs": duration_ms,
                "reads": 0,
                "rowsReturned": 0,
                "tables": [],
            },
        )
        item["count"] += 1
        item["duration"] += duration_ms
        item["rowsReturned"] += rows
        item["reads"] += int(text.upper().startswith("SELECT"))
        item["maxDurationMs"] = max(item["maxDurationMs"], duration_ms)
        item["minDurationMs"] = min(item["minDurationMs"], duration_ms)
        item["lastSeen"] = now
        if len(queries) > 200:
            queries.pop(next(iter(queries)))
        if len(_INSIGHTS) > 100:
            _INSIGHTS.pop(next(iter(_INSIGHTS)))


def snapshot(environment, limit=50):
    with _LOCK:
        queries = sorted(
            _INSIGHTS.get(scope(environment), {}).values(),
            key=lambda row: row["lastSeen"],
            reverse=True,
        )
        return {
            "generatedAt": datetime.now(UTC).isoformat(),
            "pollingIntervalMs": 5000,
            "queries": [dict(row) for row in queries[: max(1, min(int(limit), 200))]],
        }
