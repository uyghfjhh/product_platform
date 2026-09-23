"""过渡期运行原 fbasecman 用例，部署配置由平台的 pgcluster 方案提供。"""

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--override", type=Path, required=True)
    parser.add_argument("--check-profile", action="store_true")
    parser.add_argument("target")
    args = parser.parse_args()
    source = args.source.resolve()
    override = args.override.resolve()
    if not (source / "suites" / "registry.py").is_file() or not override.is_file():
        parser.error("用例来源或测试配置不存在")

    sys.path.insert(0, str(source))
    import framework.configuration as package
    import framework.configuration.loader as loader

    original = loader.load_regression_config

    def load_with_profile(root_dir, extra_configs=None, validate=True):
        # 原测试代码多处直接调用该函数，统一注入平台生成的覆盖配置。
        extras = list(extra_configs or []) + [override]
        return original(root_dir, extra_configs=extras, validate=validate)

    loader.load_regression_config = load_with_profile
    package.load_regression_config = load_with_profile

    from framework.configuration import validate_profile_isolation
    from suites.registry import get_default_registry

    validate_profile_isolation(load_with_profile(source))
    registry = get_default_registry()
    if args.check_profile:
        if args.target not in registry.suite_ids() and not registry.selected_targets(args.target):
            print("未知测试目标: %s" % args.target, file=sys.stderr)
            return 2
        print("测试配置有效: %s" % args.target)
        return 0
    targets = [args.target]
    if args.target == "failed":
        output = load_with_profile(source).output_dir / "runs"
        targets = []
        for summary in output.glob("*/*/summary.json"):
            try:
                if json.loads(summary.read_text(encoding="utf-8")).get("status") == "FAIL":
                    targets.append(summary.parent.parent.name + "." + summary.parent.name)
            except (OSError, ValueError):
                continue
        print("待重跑失败用例: %d" % len(targets), flush=True)
    failures = 0
    for target in targets:
        if target not in registry.suite_ids() and not registry.selected_targets(target):
            print("未知测试目标: %s" % target, file=sys.stderr)
            return 2
        failures += registry.run_target(source, target, preflight="off") != 0
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
