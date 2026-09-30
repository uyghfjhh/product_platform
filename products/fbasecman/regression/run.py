"""Validate a platform-generated regression override against the case catalog.

This module is kept solely for ``--check-profile`` verification: it loads the
product's vendored config context in an isolated ``sys.path`` and checks
that a generated override resolves to a valid configuration and known target.
Case execution was migrated to ``platform_regress.cli``; this entry point no
longer runs any test targets.
"""
import argparse
import json
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
    catalog_path = Path(__file__).resolve().parent / "catalog.json"
    if not catalog_path.is_file() or not override.is_file():
        parser.error("用例目录或测试配置不存在")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    targets = {item["target"] for item in catalog["cases"]}
    suite_ids = {item["suite"] for item in catalog["cases"]}

    # cmanconf lives in the vendored tree; isolate its import path so the
    # platform process stays clean.
    repo_root = Path(__file__).resolve().parents[3]
    for path in (repo_root, repo_root / "backend", source):
        value = str(path)
        while value in sys.path:
            sys.path.remove(value)
        sys.path.insert(0, value)
    import cmanconf
    from cmanconf import validate_profile_isolation

    validate_profile_isolation(
        cmanconf.load_regression_config(source, extra_configs=[override]))
    if args.target not in suite_ids and args.target not in targets:
        print("未知测试目标: %s" % args.target, file=sys.stderr)
        return 2
    print("测试配置有效: %s" % args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
