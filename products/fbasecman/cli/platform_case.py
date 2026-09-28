"""Product CLI context for an individual platform-managed regression case."""

import argparse
import json
import re
from pathlib import Path

from platform_app.config import load_settings
from platform_regress.cli import main as run_platform_case
from products.fbasecman.deployment.profile import legacy_root, profile_paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a fbasecman case through the platform SDK")
    parser.add_argument("--environment", required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("target")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", args.environment):
        parser.error("环境 ID 无效")
    if not re.fullmatch(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", args.target):
        parser.error("需要完整用例目标 suite.case")
    settings = load_settings()
    _, override = profile_paths(settings, args.environment)
    context = {
        "legacy_source": str(settings.product_regress_root("fbasecman")),
        "legacy_override": str(override),
        "legacy_report_root": str(legacy_root(settings, args.environment)),
    }
    output = args.output_dir or settings.environment_dir / "regression" / args.environment / args.target
    return run_platform_case([
        "--product-dir", str(Path(__file__).resolve().parents[1]),
        "--output-dir", str(output), "--context-json", json.dumps(context), args.target,
    ])


if __name__ == "__main__":
    raise SystemExit(main())
