"""Managed background processes and small read-only command probes."""

import os
import subprocess


class BackgroundProcess(object):
    def __init__(self, process, output_handle=None):
        self._process = process
        self._output_handle = output_handle

    @property
    def pid(self):
        return self._process.pid

    def poll(self):
        return self._process.poll()

    def wait(self):
        result = self._process.wait()
        self.close_output()
        return result

    def close_output(self):
        if self._output_handle is not None and not self._output_handle.closed:
            self._output_handle.close()


def start_background(command, cwd=None, env=None, output_path=None):
    handle = open(str(output_path), "w", encoding="utf-8") if output_path else subprocess.DEVNULL
    process = subprocess.Popen(
        command, cwd=str(cwd) if cwd else None, env=env,
        stdout=handle, stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )
    return BackgroundProcess(process, handle if output_path else None)


def capture_command(command):
    return subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        universal_newlines=True,
    ).stdout
