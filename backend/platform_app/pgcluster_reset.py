"""Reset a deployed cluster: kill stray product processes, restart, verify.

``deployment.reset`` is the manual escape hatch for wedged regression
environments: test leftovers (fbasecman proxies, redirectors, orphaned
postmasters) can survive a crashed run and hold ports/locks that make a plain
restart flap.  The sequence is kill-by-deployment-root -> pgcluster restart ->
health poll, matching what operators do by hand.
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import yaml


def _deployment_roots(config: Path) -> list[str]:
    """Path prefixes that identify processes belonging to this deployment."""
    try:
        data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return []
    paths = []

    def collect(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("data_dir", "home", "work_dir", "base_dir") \
                        and isinstance(value, str) and value.startswith("/"):
                    paths.append(value)
                else:
                    collect(value)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    collect(data)
    roots = set()
    for value in paths:
        path = Path(value)
        roots.add(str(path.parent if path.name == "data" else path))
    # Collapse nested roots to their common deployment directory.
    collapsed = sorted(roots)
    return [root for root in collapsed
            if not any(other != root and root.startswith(other + "/")
                       for other in collapsed)] or collapsed[:1]


def _stray_processes(roots: list[str]) -> list[int]:
    """PIDs whose cmdline references the deployment's own directories."""
    self_pid = os.getpid()
    pids = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == self_pid:
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode(
                "utf-8", errors="replace")
        except OSError:
            continue
        if any(root in cmdline for root in roots):
            pids.append(pid)
    return pids


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pgcluster", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument("target")
    args = parser.parse_args()

    def run(action: str) -> int:
        print("[pgcluster] %s %s" % (action, args.target), flush=True)
        return subprocess.run([
            sys.executable, str(args.pgcluster), "-f", str(args.config),
            action, args.target,
        ], cwd=args.pgcluster.parent, check=False).returncode

    roots = _deployment_roots(args.config)
    strays = _stray_processes(roots) if roots else []
    if strays:
        print("[reset] 清理 %d 个残留进程: %s" % (len(strays), strays), flush=True)
        for pid in strays:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _stray_processes(roots):
            time.sleep(0.3)
        for pid in _stray_processes(roots):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    else:
        print("[reset] 无残留进程", flush=True)

    result = run("restart")
    if result:
        print("[reset] restart 失败，尝试 stop+start", flush=True)
        run("stop")
        result = run("start")
        if result:
            return result
    time.sleep(2)
    for _ in range(10):
        if run("health") == 0:
            print("[reset] 集群已恢复健康", flush=True)
            return 0
        time.sleep(2)
    print("[reset] 重启后健康检查仍未通过", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
