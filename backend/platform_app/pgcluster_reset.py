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


def _deployment_roots(config: Path, names=None) -> list[str]:
    """Only selected local PGDATA directories identify reset-owned processes."""
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from platform_app.resources import canonical_host
    try:
        data=yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    except (OSError,ValueError):
        return []
    hosts=data.get('hosts') or {}
    roots=[]
    for name,node in (data.get('instances') or {}).items():
        if names is not None and name not in names:continue
        host=(hosts.get(node.get('host')) or {}).get('address','')
        directory=node.get('data_dir')
        if host and canonical_host(host)=='local' and isinstance(directory,str) and directory.startswith('/'):
            path=Path(directory).resolve()
            if path!=Path('/') and (path/'PG_VERSION').is_file():
                roots.append(str(path))
    return sorted(set(roots))


def _stray_processes(roots: list[str]) -> list[int]:
    """PIDs whose cmdline passes a deployment-rooted path as an argument.

    A deployment root must match a whole argument (``-D /data/node1``) —
    never a substring — so ``/pgdata/mmr1`` cannot kill ``mmr11`` and an
    editor open on a config file inside the root is not a stray process.
    """
    self_pid = os.getpid()
    pids = []
    normalized = [root.rstrip("/") for root in roots]
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == self_pid:
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\x00")
        except OSError:
            continue
        executable=Path(argv[0].decode("utf-8",errors="replace").split(":",1)[0]).name if argv and argv[0] else ''
        if executable not in {'postgres','postmaster'}:continue
        try:
            cwd=str((entry/'cwd').resolve())
        except OSError:
            cwd=''
        matched=cwd in normalized
        for arg in argv:
            text = arg.decode("utf-8", errors="replace")
            candidates = [text]
            if text.startswith("-D"):
                candidates.append(text[2:])
            if "=" in text:
                candidates.append(text.split("=", 1)[1])
            if any(candidate == root
                   for candidate in candidates for root in normalized):
                matched = True
                break
        if matched:
            pids.append(pid)
    return pids


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pgcluster", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument("target")
    parser.add_argument("--control-root", type=Path)
    parser.add_argument("--environment")
    args = parser.parse_args()

    def run(action: str) -> int:
        print("[pgcluster] %s %s" % (action, args.target), flush=True)
        return subprocess.run([
            sys.executable, str(Path(__file__).with_name("pgcluster_entry.py")), str(args.pgcluster),
            *(["--control-root", str(args.control_root), "--environment", args.environment] if args.control_root and args.environment else []),
            "-f", str(args.config),
            action, args.target,
        ], cwd=args.pgcluster.parent, check=False).returncode

    sys.path.insert(0,str(args.pgcluster.parent))
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from pgclusterlib.config import load
    from pgclusterlib.executor import LOCAL_HOSTS
    from pgclusterlib.runtime import Runtime

    from platform_app.resources import local_addresses
    LOCAL_HOSTS.update(local_addresses())
    config=load(args.config);config.validate(args.target)
    runtime=Runtime(config)
    if args.control_root and args.environment:
        from platform_app.deployment.ownership import enroll
        enroll(args.control_root,args.environment,config,args.target,runtime)
    roots = _deployment_roots(args.config,runtime.target_instances(args.target))
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
