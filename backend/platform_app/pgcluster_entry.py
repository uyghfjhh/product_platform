"""Engine entry with platform host identity and registered resource ownership."""

import argparse
import runpy
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--control-root", type=Path)
    parser.add_argument("--environment")
    parser.add_argument("program", type=Path)
    args, remaining = parser.parse_known_args()
    program = args.program.resolve()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(program.parent))
    from pgclusterlib.executor import LOCAL_HOSTS

    from platform_app.resources import local_addresses

    LOCAL_HOSTS.update(local_addresses())
    if args.control_root and args.environment:
        from pgclusterlib.cli import parser as engine_parser
        from pgclusterlib.config import load
        from pgclusterlib.runtime import Runtime

        from platform_app.deployment.ownership import enroll

        native = engine_parser().parse_args(remaining)
        if native.command in {
            "create",
            "start",
            "stop",
            "restart",
            "clean",
            "failover",
            "switchover",
            "rejoin",
            "restore",
        }:
            config = load(native.file)
            # 裸实例名是引擎支持的合法目标（target_instances 直接解析），
            # validate 只约束 kind.name 形式的集群目标。
            if native.target not in config.instances:
                config.validate(native.target)
            enroll(
                args.control_root,
                args.environment,
                config,
                native.target,
                Runtime(config),
            )
    sys.argv = [str(program), *remaining]
    runpy.run_path(str(program), run_name="__main__")


if __name__ == "__main__":
    main()
