"""Collect original sanitizer reports from one isolated soak run."""

import os
import re
import shutil
import subprocess
from pathlib import Path

ERROR = re.compile(r"ERROR: AddressSanitizer: ([a-zA-Z0-9_-]+)|AddressSanitizer:DEADLYSIGNAL")
REPORT_FILE = re.compile(r"asan\.\d+")


def require_instrumented(binary):
    path = Path(binary)
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError("ASan 构建须是本机可执行文件的绝对路径")
    # Imported or locally defined access checks both prove instrumentation.
    symbols = subprocess.run(
        ["readelf", "-Ws", str(binary)], capture_output=True, text=True, check=True, timeout=10,
    ).stdout
    if not re.search(r"\b__asan_report_(?:load|store)[A-Za-z0-9_]*\b", symbols):
        raise ValueError("ASan 测试必须选择使用 -fsanitize=address 编译的 fbasecman")


def report_paths(directory):
    for path in sorted(directory.glob("asan.*")):
        if REPORT_FILE.fullmatch(path.name) and path.is_file() and not path.is_symlink():
            yield path
    startup = directory / "fbasecman.start.log"
    if startup.is_file() and not startup.is_symlink():
        data = startup.read_bytes()
        if b"AddressSanitizer" in data or b"libasan" in data:
            yield startup


def summarize(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    error = ERROR.search(text)
    access = re.search(r"\b(READ|WRITE) of size (\d+)", text)
    source = next((line.strip() for line in text.splitlines()
                   if re.match(r"\s*#\d+", line) and re.search(r"/sources/[^ ]+\.c:\d+", line)
                   and "/machinarium/" not in line), None)
    return {
        "name": path.name, "path": str(path), "pid": int(path.suffix[1:]) if REPORT_FILE.fullmatch(path.name) else None,
        "type": (error[1] or "DEADLYSIGNAL") if error else None,
        "access": access[1] if access else None, "size": int(access[2]) if access else None,
        "source": source, "complete": "SUMMARY: AddressSanitizer:" in text,
    }


class AsanRun:
    def __init__(self, directory):
        self.directory = directory
        directory.mkdir(mode=0o700, parents=True)
        self.env = {**os.environ, "ASAN_OPTIONS": (
            f"detect_leaks=0:halt_on_error=1:symbolize=1:log_path={directory / 'asan'}"
        )}
        self.env.pop("MALLOC_CONF", None)
        if "jemalloc" in self.env.get("LD_PRELOAD", "").lower():
            raise ValueError("ASan 进程环境含 jemalloc preload，请先清除")
        if symbolizer := shutil.which("llvm-symbolizer"):
            self.env["ASAN_SYMBOLIZER_PATH"] = symbolizer

    def check(self):
        for path in report_paths(self.directory):
            with path.open("rb") as handle:
                header = handle.read(8192).decode("utf-8", errors="replace")
            if match := ERROR.search(header):
                raise RuntimeError(f"AddressSanitizer: {match[1] or 'DEADLYSIGNAL'}，原始报告 {path}")

    def capture_startup(self, startup):
        if startup.is_file():
            shutil.copyfile(startup, self.directory / "fbasecman.start.log")
