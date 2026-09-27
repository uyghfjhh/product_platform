import os
import signal
import subprocess
from pathlib import Path

from framework.errors import OperationError


class CommandRunner:
    def __init__(self, log_file=None, default_timeout=300):
        self.log_file = Path(log_file) if log_file else None
        self.default_timeout = default_timeout

    def run(self, argv, check=True, input_text=None, timeout=None):
        argv = [str(item) for item in argv]
        effective_timeout = self.default_timeout if timeout is None else timeout
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE if input_text is not None else None,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, start_new_session=True)
        try:
            output, unused_stderr = process.communicate(
                input=input_text, timeout=effective_timeout)
            completed = subprocess.CompletedProcess(argv, process.returncode,
                                                     stdout=output or "")
        except subprocess.TimeoutExpired as exc:
            self._terminate_group(process)
            output, unused_stderr = process.communicate()
            output = output or exc.stdout or ""
            if isinstance(output, bytes):
                output = output.decode("utf-8", "replace")
            output += "\n命令执行超时（%ss）" % effective_timeout
            completed = subprocess.CompletedProcess(argv, 124, stdout=output)
        self._log(argv, completed.returncode, completed.stdout)
        if check and completed.returncode != 0:
            raise OperationError(
                "命令执行失败(%s): %s\n%s" %
                (completed.returncode, " ".join(argv), completed.stdout.rstrip())
            )
        return completed

    def start(self, argv, input_text=None):
        """Start a bounded case-owned command whose output is collected later."""
        argv = [str(item) for item in argv]
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE if input_text is not None else None,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, start_new_session=True)
        if input_text is not None:
            process.stdin.write(input_text)
            process.stdin.close()
        self._log(argv, "background pid=%s" % process.pid, "")
        return process

    def finish(self, argv, process, timeout=None):
        argv = [str(item) for item in argv]
        effective_timeout = self.default_timeout if timeout is None else timeout
        try:
            output, unused_stderr = process.communicate(timeout=effective_timeout)
            returncode = process.returncode
        except subprocess.TimeoutExpired:
            self._terminate_group(process)
            output, unused_stderr = process.communicate()
            output = (output or "") + "\n命令执行超时（%ss），已终止" % effective_timeout
            returncode = 124
        completed = subprocess.CompletedProcess(argv, returncode, stdout=output or "")
        self._log(argv, completed.returncode, completed.stdout)
        return completed

    @staticmethod
    def _terminate_group(process):
        """Stop a shell and every case-owned child it launched."""
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass

    def _log(self, argv, returncode, output):
        if not self.log_file:
            return
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with self.log_file.open("a", encoding="utf-8") as stream:
            stream.write("$ %s\n" % " ".join(argv))
            stream.write(output or "")
            if output and not output.endswith("\n"):
                stream.write("\n")
            stream.write("[exit=%s]\n\n" % returncode)
