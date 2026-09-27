"""Marker-driven subprocess control for live observations."""

import queue
import subprocess
import threading


class PhasedProcessError(RuntimeError):
    pass


class PhaseAction(object):
    """A driver pause point and the command that resumes it."""

    def __init__(self, name, marker, resume_command="continue", title=None, expected=None):
        self.name = name
        self.marker = marker
        self.resume_command = resume_command
        self.title = title
        self.expected = expected


class PhasedProcess(object):
    """A subprocess that pauses at stdout markers and resumes through stdin."""

    def __init__(self, command, logfile, cwd=None, env=None):
        self.command = list(command)
        self.logfile = logfile
        self.output_lines = []
        self._lines = queue.Queue()
        self._log = logfile.open("w", encoding="utf-8")
        try:
            self.process = subprocess.Popen(
                self.command,
                cwd=str(cwd) if cwd else None,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1,
            )
        except Exception:
            self._log.close()
            raise
        self._reader = threading.Thread(target=self._read_stdout, name="phased-process-reader")
        self._reader.daemon = True
        self._reader.start()

    def _read_stdout(self):
        try:
            for line in self.process.stdout:
                self.output_lines.append(line)
                self._log.write(line)
                self._log.flush()
                self._lines.put(line)
        finally:
            self._lines.put(None)

    @property
    def output(self):
        return "".join(self.output_lines)

    def wait_for(self, marker, timeout=30):
        while True:
            try:
                line = self._lines.get(timeout=timeout)
            except queue.Empty:
                break
            if line is None:
                break
            if marker in line:
                return line.rstrip("\n")
        raise PhasedProcessError(
            "driver did not reach phase marker %r within %ss; rc=%s; output=%s"
            % (marker, timeout, self.process.poll(), self.output[-2000:] or "<empty>")
        )

    def resume(self, command="continue"):
        if self.process.poll() is not None:
            raise PhasedProcessError(
                "cannot resume driver because it already exited with rc=%s" % self.process.returncode
            )
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()

    def finish(self, timeout=30):
        try:
            rc = self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self._reader.join(timeout=5)
            self.close()
            raise PhasedProcessError("driver did not finish within %ss" % timeout)
        self._reader.join(timeout=5)
        self.close()
        return rc, self.output

    def terminate(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self._reader.join(timeout=5)
        self.close()

    def close(self):
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        if self.process.stdout and not self.process.stdout.closed:
            self.process.stdout.close()
        if not self._log.closed:
            self._log.flush()
            self._log.close()


def observe_phases(process, actions, observe, timeout=30, finish_timeout=30):
    """Observe each live pause point before resuming the driver."""
    observations = {}
    try:
        for action in actions:
            marker = process.wait_for(action.marker, timeout=timeout)
            observations[action.name] = observe(action.name, marker)
            if action.resume_command is not None:
                process.resume(action.resume_command)
        rc, output = process.finish(timeout=finish_timeout)
        return observations, rc, output
    except Exception:
        process.terminate()
        raise
