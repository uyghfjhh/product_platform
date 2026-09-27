"""Subprocess execution with streamed log capture and hard timeouts."""

import os
import queue
import shlex
import signal
import subprocess
import sys
import threading
import time


class CommandResult(object):
    def __init__(self, command, returncode, output, timed_out=False):
        self.command = command
        self.returncode = returncode
        self.output = output
        self.timed_out = timed_out


def command_display(command):
    if isinstance(command, (list, tuple)):
        return " ".join(shlex.quote(str(part)) for part in command)
    return str(command)


def run_logged_command(command, logfile, cwd=None, env=None, echo=False, timeout=None):
    """Run a command, stream merged output to a UTF-8 logfile, and return it."""
    display = command_display(command)
    with logfile.open("w", encoding="utf-8") as out:
        process = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            shell=isinstance(command, str),
            start_new_session=True,
            bufsize=1,
        )
        output_lines, timed_out = _stream_output(process, out, echo, timeout)
        returncode = process.wait()
        process.stdout.close()
    return CommandResult(display, returncode, "".join(output_lines), timed_out=timed_out)


def _stream_output(process, logfile, echo, timeout):
    lines = []
    received = queue.Queue()

    def reader():
        try:
            for line in process.stdout:
                received.put(line)
        finally:
            received.put(None)

    thread = threading.Thread(target=reader, name="logged-command-reader")
    thread.daemon = True
    thread.start()
    deadline = time.monotonic() + float(timeout) if timeout is not None else None
    timed_out = False
    while True:
        wait = 0.2 if deadline is None else max(0, min(0.2, deadline - time.monotonic()))
        try:
            line = received.get(timeout=wait)
        except queue.Empty:
            if deadline is not None and time.monotonic() >= deadline and not timed_out:
                timed_out = True
                _terminate_process_group(process)
            continue
        if line is None:
            break
        lines.append(line)
        if echo:
            sys.stdout.write(line)
            sys.stdout.flush()
        logfile.write(line)
        logfile.flush()
    thread.join(timeout=1)
    if timed_out:
        message = "\n[command timed out after %ss]\n" % timeout
        lines.append(message)
        logfile.write(message)
        logfile.flush()
    return lines, timed_out


def _terminate_process_group(process):
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except OSError:
            pass
