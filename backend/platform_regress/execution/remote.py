"""Explicit remote targets and bounded SSH command execution."""

import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class RemoteTarget:
    host: str
    user: str
    port: int = 22

    def __post_init__(self):
        if (
            not self.host
            or not self.user
            or self.host.startswith("-")
            or self.user.startswith("-")
        ):
            raise ValueError("invalid SSH identity")
        if not 1 <= self.port <= 65535:
            raise ValueError("invalid SSH port")


def ssh_command(
    target: RemoteTarget,
    script: str,
    *,
    login_shell=False,
    strict_host_keys=True,
    connect_timeout=10,
):
    argv = [
        "ssh",
        "-F",
        "/dev/null",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={int(connect_timeout)}",
        "-p",
        str(target.port),
    ]
    if not strict_host_keys:
        argv += ["-o", "StrictHostKeyChecking=no"]
    argv += [f"{target.user}@{target.host}"]
    argv += ["bash -lc " + shlex.quote(script)] if login_shell else ["bash", "-se"]
    return argv


class RemoteExecutor:
    def __init__(self, context, target: RemoteTarget):
        self.context, self.target = context, target

    def run(self, script: str, *, timeout_seconds=60):
        return self.context.command(
            ssh_command(self.target, script),
            input_text=script,
            timeout_seconds=timeout_seconds,
            merge_stderr=True,
        )

    def stat(self, path):
        result = self.run("stat -c '%d %i %s' -- " + shlex.quote(str(path)))
        if result.returncode:
            raise OSError(result.stdout.strip() or "remote stat failed")
        device, inode, size = map(int, result.stdout.split())
        return device, inode, size

    def size(self, path):
        return self.stat(path)[2]

    def tail(self, path, offset=0):
        result = self.run(f"tail -c +{int(offset) + 1} -- " + shlex.quote(str(path)))
        if result.returncode:
            raise OSError(result.stdout.strip() or "remote read failed")
        return result.stdout.encode("utf-8")

    def tail_identity(self, original_path, offset, identity):
        device, inode = map(int, identity)
        parent = shlex.quote(str(original_path.parent))
        script = f"""while IFS= read -r file; do
  identity=$(stat -c '%d %i' -- "$file") || continue
  if [ "$identity" = "{device} {inode}" ]; then
    tail -c +{int(offset) + 1} -- "$file"
    exit $?
  fi
done < <(find {parent} -maxdepth 1 -type f -inum {inode} -print)
echo 'previous log inode is unavailable' >&2
exit 1
"""
        result = self.run(script)
        if result.returncode:
            raise OSError(result.stdout.strip() or "remote rotated log is unavailable")
        return result.stdout.encode("utf-8")
