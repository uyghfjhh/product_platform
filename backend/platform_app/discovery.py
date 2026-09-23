"""迁移期从旧仓库读取用例清单，独立解释器避免同名 framework 包冲突。"""

import json
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
            "suite": suite.id, "target": case.target, "title": case.summary,
            "enabled": bool(case.enabled), "tags": list(case.tags),
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
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "读取用例目录失败: %s" % (result.stderr.strip() or result.returncode)
        )
    return json.loads(result.stdout)
