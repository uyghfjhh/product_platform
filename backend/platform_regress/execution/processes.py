"""Process identity and bounded termination for persistent workloads."""

import os
import signal
import time
from pathlib import Path


def is_alive(pid):
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        os.kill(pid, 0)
        stat = (Path("/proc") / str(pid) / "stat").read_text()
        return stat.split(")", 1)[1].split()[0] != "Z"
    except (OSError, ValueError, TypeError):
        return False


def command_line(pid):
    path = Path("/proc") / str(pid) / "cmdline"
    try:
        return path.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except OSError:
        return ""


def managed_pid(pid, expected):
    return bool(pid and expected and is_alive(pid) and expected in command_line(pid))


def stop_managed(pid, expected, timeout=8):
    if not managed_pid(pid, expected):
        return False
    pid = int(pid)

    def terminate(sig):
        # Do not signal our own process group for an attached non-leader PID.
        group = os.getpgid(pid)
        if group == pid and group != os.getpgrp():
            os.killpg(group, sig)
        else:
            os.kill(pid, sig)

    try:
        terminate(signal.SIGTERM)
    except OSError:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and managed_pid(pid, expected):
        time.sleep(0.2)
    if managed_pid(pid, expected):
        try:
            terminate(signal.SIGKILL)
        except OSError:
            pass
    return True
