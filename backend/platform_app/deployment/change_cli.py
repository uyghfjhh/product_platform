"""Subprocess entry point for reviewed changes; platform owns task state."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-root", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--checkpoint", required=True)
    args = parser.parse_args()
    sys.path.insert(0, args.engine_root)
    import yaml
    from pgclusterlib.config import load
    from pgclusterlib.executor import LOCAL_HOSTS
    from pgclusterlib.runtime import Runtime

    from platform_app.deployment.change_execution import ChangeExecutor
    from platform_app.deployment.workbench import diff_operations, digest
    from platform_app.resources import local_addresses

    LOCAL_HOSTS.update(local_addresses())

    path = Path(args.plan)
    plan = json.loads(path.read_text())
    for name, expected in plan["files"].items():
        if digest((path.parent / name).read_text()) != expected:
            raise ValueError("计划文件摘要变化: " + name)
    current = yaml.safe_load((path.parent / "baseline.yaml").read_text())
    desired = load(plan["config_path"])
    diff, _, executable = diff_operations(current, desired.raw)
    if not executable or diff != plan["diff"] or plan["action"] != "deployment.change":
        raise ValueError("变更与审阅计划不匹配")
    executor = ChangeExecutor(
        plan,
        current,
        Runtime(desired),
        Runtime(load(path.parent / "baseline.yaml")),
        args.checkpoint,
        emit=lambda message: print(message, flush=True),
    )
    executor.run()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("变更中止，需要核对检查点: " + str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
