"""Crash forensics, core dump discovery, and GDB backtrace extraction for C proxies."""

import glob
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def find_core_files(search_paths: List[Path], binary_name: str = "fbasecman", since_time: Optional[float] = None) -> List[Path]:
    """Find core dump files created by a crash."""
    found: List[Path] = []
    candidates = []

    for base in search_paths:
        if not base.exists():
            continue
        # Patterns based on core_pattern or standard patterns: core, core.*, core.%e.%p.%t
        for pat in ("core*", f"core.{binary_name}.*", "*.core"):
            candidates.extend(base.glob(pat))

    seen = set()
    for p in candidates:
        res_p = p.resolve()
        if res_p in seen:
            continue
        seen.add(res_p)
        if p.is_file() and not p.is_symlink():
            try:
                mtime = p.stat().st_mtime
                if since_time is None or mtime >= (since_time - 5.0):
                    found.append(p)
            except OSError:
                pass

    def _safe_mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    found.sort(key=_safe_mtime, reverse=True)
    return found


def extract_gdb_backtrace(binary_path: Path, core_path: Path, gdb_path: str = "gdb", timeout: int = 20) -> Optional[str]:
    """Run GDB in batch mode on core dump and return full backtrace."""
    gdb_bin = shutil.which(gdb_path)
    if not gdb_bin or not binary_path.exists() or not core_path.exists():
        return None

    cmd = [
        gdb_bin,
        "--batch",
        "-ex", "set print thread-events off",
        "-ex", "echo \n=== 崩溃线程调用栈 (Thread Backtrace) ===\n",
        "-ex", "bt full",
        "-ex", "echo \n=== 所有线程堆栈摘要 (All Threads Backtrace) ===\n",
        "-ex", "thread apply all bt",
        str(binary_path.resolve()),
        str(core_path.resolve()),
    ]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        output = proc.stdout.decode("utf-8", errors="replace").strip()
        return output if output else None
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"GDB 堆栈解析异常: {exc}"


def signal_name(returncode: int) -> str:
    """Map negative returncode or 128+N exit code to signal name."""
    sig_num = None
    if returncode < 0:
        sig_num = -returncode
    elif returncode > 128:
        sig_num = returncode - 128

    if sig_num is not None:
        try:
            return signal.Signals(sig_num).name
        except (ValueError, AttributeError):
            return f"SIG_{sig_num}"
    return f"RC_{returncode}"


def diagnose_crash(
    binary_path: Path,
    workdirs: List[Path],
    returncode: Optional[int] = None,
    since_time: Optional[float] = None,
) -> Dict[str, Any]:
    """Comprehensive diagnostic on a potentially crashed C process."""
    is_crash = False
    sig = ""
    if returncode is not None:
        # Check standard crash return codes: negative or 134 (SIGABRT), 139 (SIGSEGV), 135 (SIGBUS)
        if returncode < 0 or returncode in (134, 139, 135, 136, 138):
            is_crash = True
            sig = signal_name(returncode)

    core_files = find_core_files(workdirs, binary_name=binary_path.name, since_time=since_time)
    if core_files:
        is_crash = True

    backtrace = None
    chosen_core = None
    if core_files:
        chosen_core = core_files[0]
        backtrace = extract_gdb_backtrace(binary_path, chosen_core)

    return {
        "is_crash": is_crash,
        "signal": sig,
        "core_file": str(chosen_core) if chosen_core else None,
        "all_cores": [str(c) for c in core_files],
        "backtrace": backtrace,
    }
