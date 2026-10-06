"""Bounded local/SSH probes using the same host identity as pgcluster."""

import json
import shlex
import subprocess
import sys
from pathlib import Path

from ..resources import canonical_host


def probe(host, request, ssh=None, *, agent="probe_agent.py"):
    script = Path(__file__).with_name(agent).read_text()
    args = ["python3", "-c", script, json.dumps(request)]
    if canonical_host(host) == 'local':
        args[0] = sys.executable
    else:
        remote_command = shlex.join(args)
        ssh = ssh or {}
        target = (
            f"{ssh['user']}@{host}" if ssh.get("user") else host
        )
        args = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={int(ssh.get('connect_timeout', 5))}",
        ]
        if ssh.get("identity_file"):
            args += ["-i", ssh["identity_file"]]
        if ssh.get("port"):
            args += ["-p", str(int(ssh["port"]))]
        args += [target, "--", remote_command]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)
        if result.returncode:
            raise ValueError(result.stderr.strip() or result.stdout.strip() or f"退出码 {result.returncode}")
        payload = json.loads(result.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"目标主机探测失败：{exc}") from exc
    if result.returncode or payload.get("error"):
        raise ValueError(
            "目标主机探测失败：" + (payload.get("error") or result.stderr[-1000:])
        )
    return payload
