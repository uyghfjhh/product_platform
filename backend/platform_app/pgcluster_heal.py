"""Run an explicit pgcluster health/restart/health recovery sequence."""

import argparse
import subprocess
import sys
import time
from pathlib import Path


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
        result = subprocess.run([
            sys.executable, str(Path(__file__).with_name("pgcluster_entry.py")), str(args.pgcluster),
            *(["--control-root", str(args.control_root), "--environment", args.environment] if args.control_root and args.environment else []),
            "-f", str(args.config),
            action, args.target,
        ], cwd=args.pgcluster.parent, check=False)
        return result.returncode

    if run("health") == 0:
        print("[pgcluster] 集群健康，无需恢复", flush=True)
        return 0
    print("[pgcluster] 健康检查失败，执行重启后再次验证", flush=True)
    result = run("restart")
    if result:
        return result
    time.sleep(2)
    for _ in range(5):
        code = run("health")
        if code == 0:
            return 0
        time.sleep(1)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
