from pathlib import Path
import os
import shutil
from typing import Dict, Any, List


# `output/` is a shared namespace: stable owns `output/stable/`, while this
# CLI owns only the entries below.  Keep the cleanup allowlist explicit.
REGRESSION_OUTPUT_ENTRIES = ("env", "runs", "handover.lock")


class CleanResult:
    def __init__(self):
        self.removed: List[str] = []
        self.skipped: List[str] = []
        self.pruned: List[Dict[str, Any]] = []
        self.reclaimed_bytes: int = 0


def _safe_remove_path(path: Path, result: CleanResult):
    if not path.exists():
        result.skipped.append(str(path))
        return
    if path.is_dir():
        shutil.rmtree(str(path))
    else:
        path.unlink()
    result.removed.append(str(path))


def prune_run_artifacts(
    runs_dir: Path,
    max_file_size_mb: float = 10.0,
    keep_head_lines: int = 200,
    keep_tail_lines: int = 1500,
    result: CleanResult = None,
) -> CleanResult:
    """Scan output/runs/ and prune oversized logs and stale backup files.
    
    1. Removes obsolete backup logs (*.log_bak_*, *.log.old, *.log.bak, *.tmp).
    2. Truncates oversized .log / .out / .txt files that exceed max_file_size_mb,
       preserving critical head diagnostic lines and recent tail lines.
    """
    if result is None:
        result = CleanResult()

    if not runs_dir.exists() or not runs_dir.is_dir():
        return result

    max_bytes = int(max_file_size_mb * 1024 * 1024)

    for item in runs_dir.rglob("*"):
        if not item.is_file():
            continue

        name = item.name
        # 1. Check for stale backup logs
        if (
            "log_bak" in name
            or name.endswith(".log.old")
            or name.endswith(".log.bak")
            or name.endswith(".tmp")
        ):
            try:
                size = item.stat().st_size
                item.unlink()
                result.removed.append(str(item))
                result.reclaimed_bytes += size
                result.pruned.append({
                    "path": str(item),
                    "action": "deleted_backup",
                    "original_bytes": size,
                    "reclaimed_bytes": size,
                })
            except OSError:
                pass
            continue

        # 2. Check for oversized logs to truncate
        if name.endswith((".log", ".out", ".txt")):
            try:
                size = item.stat().st_size
                if size > max_bytes:
                    # Read head and tail
                    lines_head = []
                    lines_tail = []
                    total_lines = 0
                    with open(item, "r", encoding="utf-8", errors="replace") as f:
                        for idx, line in enumerate(f):
                            total_lines += 1
                            if idx < keep_head_lines:
                                lines_head.append(line)
                            if len(lines_tail) >= keep_tail_lines:
                                lines_tail.pop(0)
                            lines_tail.append(line)

                    marker = (
                        f"\n\n{'=' * 80}\n"
                        f"[... TRUNCATED OVERSIZED LOG ({size / (1024*1024):.2f} MB, {total_lines} lines) "
                        f"BY REGRESSION CLEANER ...]\n"
                        f"[... Retained first {len(lines_head)} lines and last {len(lines_tail)} lines ...]\n"
                        f"{'=' * 80}\n\n"
                    )

                    new_content = "".join(lines_head) + marker + "".join(lines_tail)
                    with open(item, "w", encoding="utf-8") as f:
                        f.write(new_content)

                    new_size = item.stat().st_size
                    reclaimed = max(0, size - new_size)
                    result.reclaimed_bytes += reclaimed
                    result.pruned.append({
                        "path": str(item),
                        "action": "truncated",
                        "original_bytes": size,
                        "reclaimed_bytes": reclaimed,
                        "new_bytes": new_size,
                    })
            except OSError:
                pass

    return result


def _remove_source_artifacts(root: Path, result: CleanResult):
    explicit_targets = [
        root / ".codex_write_probe",
        root / "result",
        root / "diff",
    ]
    for target in explicit_targets:
        _safe_remove_path(target, result)

    pycache_dirs = sorted(root.rglob("__pycache__"))
    for target in pycache_dirs:
        _safe_remove_path(target, result)

    bak_files = sorted(root.rglob("*.bak"))
    for target in bak_files:
        _safe_remove_path(target, result)

    class_files = sorted(root.rglob("*.class"))
    for target in class_files:
        _safe_remove_path(target, result)

    generated_files = [
        root / "tools" / "test_data.txt",
    ]
    for target in generated_files:
        _safe_remove_path(target, result)


def run_clean(
    root: Path,
    include_output: bool = False,
    output_only: bool = False,
    prune_logs: bool = False,
    max_log_size_mb: float = 10.0,
) -> CleanResult:
    result = CleanResult()

    if not output_only:
        _remove_source_artifacts(root, result)

    if include_output or output_only:
        for name in REGRESSION_OUTPUT_ENTRIES:
            _safe_remove_path(root / "output" / name, result)
    elif prune_logs:
        runs_dir = root / "output" / "runs"
        prune_run_artifacts(runs_dir, max_file_size_mb=max_log_size_mb, result=result)

    return result
