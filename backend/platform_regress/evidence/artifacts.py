"""Artifact inspection, retention previews and bounded derived log views."""

import json
import shutil
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CleanupReport:
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    pruned: list[dict] = field(default_factory=list)
    reclaimed_bytes: int = 0


def contained_path(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("artifact path escapes root")
    return path


def remove_artifact(path, result, *, root):
    path = Path(path)
    # Resolve ancestors (including .. and parent symlinks), but unlink a
    # symlink artifact itself without following its final component.
    root = Path(root).resolve()
    if not path.parent.resolve().is_relative_to(root):
        raise ValueError("cleanup path escapes root")
    if path.is_symlink():
        path.unlink()
    elif not path.exists():
        result.skipped.append(str(path))
        return
    elif path.is_dir():
        if not path.resolve().is_relative_to(Path(root).resolve()):
            raise ValueError("cleanup directory escapes root")
        shutil.rmtree(path)
    else:
        path.unlink()
    result.removed.append(str(path))


def prune_run_artifacts(
    runs_dir,
    max_file_size_mb=10,
    keep_head_lines=200,
    keep_tail_lines=1500,
    result=None,
):
    # Evidence originals are immutable. Large files get derived previews;
    # pruning never destroys logs that a CaseResult references.
    result = result or CleanupReport()
    root = Path(runs_dir).resolve()
    if not root.is_dir():
        return result
    if min(max_file_size_mb, keep_head_lines, keep_tail_lines) <= 0:
        raise ValueError("invalid retention limits")
    for path in list(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or ".previews" in path.parts:
            continue
        if (
            path.suffix not in (".log", ".out", ".txt")
            or path.stat().st_size <= max_file_size_mb * 1024**2
        ):
            continue
        head, tail, count = [], deque(maxlen=keep_tail_lines), 0
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if count < keep_head_lines:
                    head.append(line)
                tail.append(line)
                count += 1
        preview = path.parent / ".previews" / path.name
        preview.parent.mkdir(exist_ok=True)
        preview.write_text(
            "".join(head)
            + f"\n[... {count} lines; original retained ...]\n"
            + "".join(tail),
            encoding="utf-8",
        )
        result.pruned.append(
            {
                "path": str(path),
                "action": "preview_created",
                "preview": str(preview),
                "reclaimed_bytes": 0,
            }
        )
    return result


class ArtifactRepository:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()

    def describe(self, target, *, parsed=None):
        def read(name):
            path = self.directory / name
            if not path.is_file():
                return None
            return json.loads(path.read_text(encoding="utf-8"))

        summary, journal = read("summary.json"), read("steps.json")
        report_path = self.directory / "report.txt"
        logs = []
        if self.directory.is_dir():
            for path in self.directory.rglob("*.log"):
                if (
                    path.is_file()
                    and not path.is_symlink()
                    and len(path.relative_to(self.directory).parts) <= 3
                ):
                    logs.append(
                        {
                            "name": path.relative_to(self.directory).as_posix(),
                            "size": path.stat().st_size,
                        }
                    )
        return {
            "target": target,
            "available": self.directory.is_dir(),
            "summary": summary,
            "steps": journal.get("steps", []) if isinstance(journal, dict) else [],
            "report": report_path.read_text(encoding="utf-8", errors="replace")
            if report_path.is_file()
            else None,
            "parsed": parsed() if parsed and report_path.is_file() else None,
            "logs": sorted(logs, key=lambda item: item["name"]),
        }

    def log(self, filename, *, last_lines=500):
        if not filename or Path(filename).is_absolute():
            raise ValueError("日志文件名无效")
        path = contained_path(self.directory, filename)
        if path.suffix != ".log" or not path.is_file():
            raise FileNotFoundError("日志文件不存在")
        if not 1 <= last_lines <= 100000:
            raise ValueError("invalid tail limit")
        with path.open(encoding="utf-8", errors="replace") as stream:
            text = "".join(deque(stream, maxlen=last_lines))
        return {
            "filename": filename,
            "name": filename,
            "content": text,
            "lines": text.splitlines(),
            "size": path.stat().st_size,
        }


def scan_run_summaries(root):
    """Index structured report summaries; never infer verdict from prose."""
    root = Path(root)
    found = {}
    for path in root.glob("*/*/summary.json"):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            status = value.get("status")
            if status not in {
                "PASS",
                "FAIL",
                "RUNNING",
                "ERROR",
                "BLOCKED",
                "CANCELLED",
                "SKIPPED",
            }:
                continue
            duration = value.get("duration")
            found[path.parent.parent.name + "." + path.parent.name] = {
                "status": status,
                "duration": f"{float(duration):.2f}s" if duration is not None else "-",
                "has_report": (path.parent / "report.txt").exists(),
                "modified_at": path.stat().st_mtime,
            }
        except (OSError, ValueError, AttributeError, TypeError):
            continue
    return found
