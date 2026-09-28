"""Validate a platform-generated regression override against the legacy catalog.

This module is kept solely for ``--check-profile`` verification: it loads the
product's vendored suite context in an isolated ``sys.path`` and checks
that a generated override resolves to a valid configuration and known target.
Case execution was migrated to ``platform_regress.cli``; this entry point no
longer runs any test targets.
"""
import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate fbasecman regression profile and target")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--override", type=Path, required=True)
    parser.add_argument("--check-profile", action="store_true", required=True)
    parser.add_argument("target")
    args = parser.parse_args()
    source = args.source.resolve()
    override = args.override.resolve()
    if not (source / "suites" / "registry.py").is_file() or not override.is_file():
        parser.error("用例来源或测试配置不存在")

    # The legacy suite still imports product-private modules (cmanconf,
    # suites.*). Isolate its import path so the platform process stays clean.
    repo_root = Path(__file__).resolve().parents[3]
    for path in (repo_root, repo_root / "backend", source):
        value = str(path)
        while value in sys.path:
            sys.path.remove(value)
        sys.path.insert(0, value)
    import cmanconf

    original = cmanconf.load_regression_config

    def load_with_profile(root_dir, extra_configs=None, validate=True):
        extras = list(extra_configs or []) + [override]
        return original(root_dir, extra_configs=extras, validate=validate)

    cmanconf.load_regression_config = load_with_profile

    from cmanconf import validate_profile_isolation
    from suites.registry import get_default_registry

    validate_profile_isolation(load_with_profile(source))
    registry = get_default_registry()
    if args.target not in registry.suite_ids() and not registry.selected_targets(args.target):
        print("未知测试目标: %s" % args.target, file=sys.stderr)
        return 2
    print("测试配置有效: %s" % args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
