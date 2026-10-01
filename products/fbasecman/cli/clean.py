"""fbasecman 回归产物清理（对齐旧 tools/clean.py 语义，根目录换成证据根）。"""

from pathlib import Path

from platform_regress.evidence.artifacts import (
    CleanupReport,
    prune_run_artifacts,
    remove_artifact,
)

REGRESSION_OUTPUT_ENTRIES = ("env", "runs", "handover.lock")




def _safe_remove_path(path: Path, result: CleanupReport):
    return remove_artifact(path, result, root=path.parent)


def _remove_source_artifacts(root: Path, result: CleanupReport):
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
              prune_logs: bool = False, max_log_size_mb: float = 10.0) -> CleanupReport:
    """source_root=产品回归树；output_root=环境证据根(output/ 所在)。"""
    result = CleanupReport()
    if not output_only:
        _remove_source_artifacts(source_root, result)
    if include_output or output_only:
        for name in REGRESSION_OUTPUT_ENTRIES:
            _safe_remove_path(output_root / name, result)
    elif prune_logs:
        prune_run_artifacts(output_root / "runs",
                            max_file_size_mb=max_log_size_mb, result=result)
    return result
