"""Bounded local/SSH probes using the same host identity as pgcluster."""

import json
import shlex
import socket
import subprocess
import sys
from pathlib import Path


def probe(host, request):
    script = Path(__file__).with_name("probe_agent.py").read_text()
    args = ["python3", "-c", script, json.dumps(request)]
    if host in {"127.0.0.1", "localhost", "::1", socket.gethostname()}:
        args[0] = sys.executable
    else:
        args = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            host,
            "--",
            shlex.join(args),
        ]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        payload = json.loads(result.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"目标主机探测失败：{exc}") from exc
    if result.returncode or payload.get("error"):
        raise ValueError(
            "目标主机探测失败：" + (payload.get("error") or result.stderr[-1000:])
        )
    return payload
