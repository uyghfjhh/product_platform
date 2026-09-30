"""Streaming local and bounded remote binary fingerprints."""

import hashlib
import shlex
import subprocess
from pathlib import Path


def local_fingerprint(path):
    p = Path(path)
    if not p.is_file():
        return {"path": str(path), "error": "not found"}
    with p.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    stat = p.stat()
    return {
        "path": str(p),
        "sha256": digest,
        "size": stat.st_size,
        "mtime": int(stat.st_mtime),
    }


def remote_fingerprint(host, user, path, *, runner=subprocess.run):
    quoted = shlex.quote(str(path))
    script = f"sha256sum {quoted} 2>/dev/null | awk '{{print $1}}'; stat -c \"%s %Y\" {quoted} 2>/dev/null"
    try:
        result = runner(
            [
                "ssh",
                "-F",
                "/dev/null",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                f"{user}@{host}",
                script,
            ],
            text=True,
            capture_output=True,
            timeout=30,
        )
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if result.returncode or len(lines) != 2:
            return {"path": str(path), "error": result.stderr.strip() or "unreachable"}
        size, mtime = map(int, lines[1].split())
        if len(lines[0]) != 64 or any(
            c not in "0123456789abcdef" for c in lines[0].lower()
        ):
            raise ValueError("invalid fingerprint")
        return {"path": str(path), "sha256": lines[0], "size": size, "mtime": mtime}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"path": str(path), "error": str(exc)}
