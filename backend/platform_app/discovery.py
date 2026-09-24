import json
import os
import subprocess
import sys

from .config import Settings


CMAN_SCRIPT = """
import json
from suites.registry import get_default_registry
items = []
for suite in get_default_registry().all_suites():
    for case in suite.get_cases():
        items.append({
            "suite": suite.id, "target": case.target,
            "title": getattr(case, "summary", getattr(case, "title", case.target)),
            "enabled": bool(getattr(case, "enabled", True)),
            "tags": list(getattr(case, "tags", []) or []),
        })
print(json.dumps(items, ensure_ascii=False))
"""

FBASE_SCRIPT = """
import json
from suites import SUITES
items = []
for suite_id, suite in SUITES.items():
    for case in suite["cases"]:
        items.append({
            "suite": suite_id, "target": case["id"],
            "title": case.get("name", case["id"]),
            "enabled": True, "tags": [case.get("group", "")],
        })
print(json.dumps(items, ensure_ascii=False))
"""


def discover_cases(settings: Settings, product_id: str) -> list[dict]:
    if product_id == "fbasecman":
        root, script = settings.fbasecman_regress_root, CMAN_SCRIPT
    elif product_id == "fbase-database":
        root, script = settings.fbase_regress_root, FBASE_SCRIPT
    else:
        raise ValueError("未知产品")
    if not root.is_dir():
        raise FileNotFoundError("用例来源目录不存在: %s" % root)
    repo_root = root.parents[1] if root.name == "fbasecman" else root.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{root}{os.pathsep}{repo_root}{os.pathsep}{repo_root / 'backend'}{os.pathsep}{env.get('PYTHONPATH', '')}"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "读取用例目录失败: %s" % (result.stderr.strip() or result.returncode)
        )
    return json.loads(result.stdout)
