"""fbasecman 回归产物清理（对齐旧 tools/clean.py 语义，根目录换成证据根）。"""

from pathlib import Path
import shutil

REGRESSION_OUTPUT_ENTRIES = ("env", "runs", "handover.lock")


class CleanResult:
    def __init__(self):
        self.removed = []
        self.skipped = []
        self.pruned = []
        self.reclaimed_bytes = 0


def _safe_remove_path(path: Path, result: CleanResult):
    if not path.exists():
        result.skipped.append(str(path))
        return
    if path.is_dir():
        shutil.rmtree(str(path))
    else:
        path.unlink()
    result.removed.append(str(path))


def prune_run_artifacts(runs_dir: Path, max_file_size_mb: float = 10.0,
                        keep_head_lines: int = 200, keep_tail_lines: int = 1500,
                        result: CleanResult = None) -> CleanResult:
    """清过期日志备份并把超大日志截断为首 200 + 尾 1500 行。"""
    if result is None:
        result = CleanResult()
    if not runs_dir.exists() or not runs_dir.is_dir():
        return result

    max_bytes = int(max_file_size_mb * 1024 * 1024)
    for item in runs_dir.rglob("*"):
        if not item.is_file():
            continue
        name = item.name
        if ("log_bak" in name or name.endswith(".log.old")
                or name.endswith(".log.bak") or name.endswith(".tmp")):
            try:
                size = item.stat().st_size
                item.unlink()
                result.removed.append(str(item))
                result.reclaimed_bytes += size
                result.pruned.append({"path": str(item), "action": "deleted_backup",
                                      "reclaimed_bytes": size})
            except OSError:
                pass
            continue
        if name.endswith((".log", ".out", ".txt")):
            try:
                size = item.stat().st_size
                if size > max_bytes:
                    lines_head, lines_tail, total_lines = [], [], 0
                    with open(item, "r", encoding="utf-8", errors="replace") as f:
                        for idx, line in enumerate(f):
                            total_lines += 1
                            if idx < keep_head_lines:
                                lines_head.append(line)
                            if len(lines_tail) >= keep_tail_lines:
                                lines_tail.pop(0)
                            lines_tail.append(line)
                    marker = (
                        "\n\n%s\n[... TRUNCATED OVERSIZED LOG (%.2f MB, %d lines) ...]\n"
                        "[... Retained first %d lines and last %d lines ...]\n%s\n\n"
                        % ("=" * 80, size / (1024 * 1024), total_lines,
                           len(lines_head), len(lines_tail), "=" * 80))
                    with open(item, "w", encoding="utf-8") as f:
                        f.write("".join(lines_head) + marker + "".join(lines_tail))
                    new_size = item.stat().st_size
                    reclaimed = max(0, size - new_size)
                    result.reclaimed_bytes += reclaimed
                    result.pruned.append({"path": str(item), "action": "truncated",
                                          "reclaimed_bytes": reclaimed})
            except OSError:
                pass
    return result


def _remove_source_artifacts(root: Path, result: CleanResult):
    for target in (root / ".codex_write_probe", root / "result", root / "diff"):
        _safe_remove_path(target, result)
    for target in sorted(root.rglob("__pycache__")):
        _safe_remove_path(target, result)
    for target in sorted(root.rglob("*.bak")):
        _safe_remove_path(target, result)
    # *.class 不清：stable/assets/jdbc/build/ 下的预编译类是被跟踪资产，
    # 运行期 javac 会自行重建。


def run_clean(source_root: Path, output_root: Path,
              include_output: bool = False, output_only: bool = False,
              prune_logs: bool = False, max_log_size_mb: float = 10.0) -> CleanResult:
    """source_root=产品回归树；output_root=环境证据根(output/ 所在)。"""
    result = CleanResult()
    if not output_only:
        _remove_source_artifacts(source_root, result)
    if include_output or output_only:
        for name in REGRESSION_OUTPUT_ENTRIES:
            _safe_remove_path(output_root / "output" / name, result)
    elif prune_logs:
        prune_run_artifacts(output_root / "output" / "runs",
                            max_file_size_mb=max_log_size_mb, result=result)
    return result
