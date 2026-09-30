"""Linux process-resource sampling with a product-selected identity."""

import csv
import subprocess
import time
from datetime import datetime
from pathlib import Path

from .processes import managed_pid

FIELDS = (
    "timestamp",
    "pid",
    "rss_kb",
    "vsz_kb",
    "cpu_percent",
    "threads",
    "fd_count",
    "read_bytes",
    "write_bytes",
)


def sample_process(pid):
    result = subprocess.run(
        ["ps", "-p", str(int(pid)), "-o", "rss=,vsz=,%cpu=,nlwp="],
        capture_output=True,
        text=True,
        timeout=5,
    )
    values = result.stdout.split()
    if len(values) != 4:
        raise ProcessLookupError(pid)
    proc = Path("/proc") / str(int(pid))
    io = {"read_bytes": 0, "write_bytes": 0}
    try:
        for line in (proc / "io").read_text().splitlines():
            key, _, value = line.partition(":")
            if key in io:
                io[key] = int(value.strip())
        fd_count = len(list((proc / "fd").iterdir()))
    except FileNotFoundError as exc:
        raise ProcessLookupError(pid) from exc
    return dict(
        zip(
            FIELDS,
            [
                datetime.now().isoformat(),
                pid,
                *values,
                fd_count,
                io["read_bytes"],
                io["write_bytes"],
            ],
        )
    )


def monitor_state(
    store,
    *,
    pid_key,
    output,
    fingerprint_key="run_dir",
    interval=1,
    active_statuses=("running",),
):
    if interval <= 0:
        raise ValueError("monitor interval must be positive")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if handle.tell() == 0:
            writer.writeheader()
        while True:
            state = store.load()
            pid = state.get(pid_key)
            if state.get("status") not in active_statuses or not managed_pid(
                pid, state.get(fingerprint_key, "")
            ):
                return
            try:
                writer.writerow(sample_process(pid))
            except ProcessLookupError:
                return
            handle.flush()
            time.sleep(interval)
