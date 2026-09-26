"""Run an explicit pgcluster health/restart/health recovery sequence."""

import argparse
import subprocess
import sys
from pathlib import Path


import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pgcluster", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument("target")
    args = parser.parse_args()

    def run(action: str) -> int:
        print("[pgcluster] %s %s" % (action, args.target), flush=True)
        result = subprocess.run([
            sys.executable, str(args.pgcluster), "-f", str(args.config),
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
