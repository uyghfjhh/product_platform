"""Operation (task) routes: submit, list, events, cancel, log."""

import asyncio
import json

from fastapi import HTTPException, Query
from fastapi.responses import StreamingResponse

from ..actions import TERMINAL
from ..config import Settings
from ..operations import OperationRequest
from .contracts import Event, Task
from .schemas import public_task


def register(app, settings: Settings, store) -> None:

    @app.post("/api/v1/operations", status_code=202, response_model=Task)
    def start_operation(item: OperationRequest):
        return public_task(app.state.operations.submit(item))

    @app.get("/api/v1/operations", response_model=list[Task])
    def operations(limit: int = Query(default=40, ge=1, le=200),
                   before: str | None = None, archived: bool = False):
        return [public_task(task) for task in store.tasks.list_tasks(
            limit, before=before, include_archived=archived)]

    @app.get("/api/v1/operations/{task_id}", response_model=Task)
    def operation(task_id: str):
        task = store.tasks.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return public_task(task)

    @app.get("/api/v1/operations/{task_id}/events", response_model=list[Event])
    def operation_events(task_id: str, after: int = Query(default=0, ge=0)):
        if store.tasks.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return store.tasks.list_events(task_id, after)

    @app.get("/api/v1/operations/{task_id}/events/stream")
    async def event_stream(task_id: str, after: int = Query(default=0, ge=0)):
        if store.tasks.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="任务不存在")

        async def stream():
            cursor = after
            while True:
                # Read terminal state before its event snapshot. If finish
                # races this iteration, the next iteration drains its event.
                task = store.tasks.get_task(task_id)
                if task is None:
                    return
                events = store.tasks.list_events(task_id, cursor)
                for event in events:
                    cursor = event["sequence"]
                    yield "id: %s\nevent: update\ndata: %s\n\n" % (
                        cursor,
                        json.dumps(event, ensure_ascii=False),
                    )
                if task["status"] in TERMINAL:
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/v1/operations/{task_id}/cancel")
    def cancel_operation(task_id: str):
        if store.tasks.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        if not store.tasks.request_cancel(task_id):
            raise HTTPException(status_code=409, detail="任务已经结束或正在取消")
        return public_task(store.tasks.get_task(task_id))

    @app.get("/api/v1/operations/{task_id}/log")
    def operation_log(
        task_id: str, last_lines: int = Query(default=500, ge=1, le=5000)
    ):
        task = store.tasks.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        path = settings.logs_dir / "operations" / (task_id + ".log")
        if not path.is_file():
            return {"lines": [], "path": str(path), "available": False}
        # 日志按后缀行数读取，前端保留原文与等级高亮的独立表示。
        from collections import deque

        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = list(deque(handle, maxlen=last_lines))
        return {
            "lines": [line.rstrip("\n") for line in lines],
            "path": str(path),
            "available": True,
        }
