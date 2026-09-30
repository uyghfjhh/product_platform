from __future__ import annotations

import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..engine import CaseContext


class EvidenceRecorder:
    """Own evidence operations for one case execution."""

    def __init__(self, context: CaseContext):
        self.context = context
        self._sequence = 0
        self._operation_sequence = 0

    def next_operation_key(self, prefix: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9-]*", prefix):
            raise ValueError("invalid evidence key prefix")
        self._operation_sequence += 1
        return f"{prefix}-{self._operation_sequence}"

    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        """Append one ordered fact before clients can observe the event."""
        self._sequence += 1
        event = {
            "schema_version": "1.0",
            "execution_id": self.context.execution_id,
            "operation_id": self.context.operation_id,
            "sequence": self._sequence,
            "recorded_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "target": self.context.target,
            "kind": kind,
            "payload": payload,
        }
        with (self.context.output_dir / "events.jsonl").open(
            "a", encoding="utf-8"
        ) as handle:
            handle.write(
                json.dumps(event, ensure_ascii=False, sort_keys=True, default=str)
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())

    def step(
        self,
        key: str,
        title: str,
        *,
        status: str = "PASS",
        details: dict[str, Any] | None = None,
    ) -> None:
        if not key or not title:
            raise ValueError("步骤标识和标题不能为空")
        if status not in {"PASS", "FAIL", "BLOCKED", "SKIPPED", "ERROR", "CANCELLED"}:
            raise ValueError("invalid step status")
        self.context.emit(
            "step.finished",
            {
                "step_key": key,
                "title": title,
                "status": status,
                "details": details or {},
            },
        )

    def attach_text(self, name: str, content: str) -> str:
        """Write a named evidence file without allowing path traversal."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
            raise ValueError("证据文件名无效")
        directory = self.context.output_dir / "artifacts" / self.context.execution_id
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / name
        temporary = directory / (name + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, destination)
        reference = "artifacts/" + self.context.execution_id + "/" + name
        if reference not in self.context._evidence:
            self.context._evidence.append(reference)
        self.context.emit("evidence.attached", {"ref": reference})
        return reference

    def attach_bytes(self, name: str, content: bytes) -> str:
        """Write binary evidence (e.g. collected server logs) under a safe name."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
            raise ValueError("证据文件名无效")
        directory = self.context.output_dir / "artifacts" / self.context.execution_id
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / name
        temporary = directory / (name + ".tmp")
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        reference = "artifacts/" + self.context.execution_id + "/" + name
        if reference not in self.context._evidence:
            self.context._evidence.append(reference)
        self.context.emit("evidence.attached", {"ref": reference})
        return reference

    def attach_file(self, name: str, source: Path) -> str:
        """Keep a run's source artifact after its product package is removed."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
            raise ValueError("证据文件名无效")
        directory = self.context.output_dir / "artifacts" / self.context.execution_id
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / name
        temporary = directory / (name + ".tmp")
        with source.open("rb") as incoming, temporary.open("wb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        os.replace(temporary, destination)
        reference = "artifacts/" + self.context.execution_id + "/" + name
        if reference not in self.context._evidence:
            self.context._evidence.append(reference)
        self.context.emit("evidence.attached", {"ref": reference})
        return reference
