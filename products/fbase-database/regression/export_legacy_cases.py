"""One-time structured export of legacy FBase case definitions.

The legacy framework is imported only while rebuilding this transition
artifact. The platform reads the exported product data without that import.
"""

import json
import re
import sys
from pathlib import Path


# Cases whose legacy modules derive object names from a per-process uuid
# TOKEN (e.g. `uuid.uuid4().hex[:12]` at module import).  The exported
# catalog freezes those names; runtime_tokens marks the literals the
# executor must re-randomize per run to preserve legacy semantics.
RUNTIME_TOKEN_CASES = {
    "mac.audit.role_audit_logs",
}


def main() -> None:
    legacy = Path(__file__).resolve().parent / "legacy"
    sys.path.insert(0, str(legacy))
    from suites import SUITES

    cases = [case for suite in SUITES.values() for case in suite["cases"]]
    for case in cases:
        if case.get("id") not in RUNTIME_TOKEN_CASES:
            continue
        rendered = json.dumps(case, ensure_ascii=False)
        tokens = sorted(set(re.findall(r"fbase_regress\w*?_([0-9a-f]{12})\b", rendered)))
        if not tokens:
            tokens = sorted(set(re.findall(r"_([0-9a-f]{12})\b", rendered)))
        case["runtime_tokens"] = tokens
    suites = {
        suite_id: {"title": suite.get("name", suite_id),
                   "description": suite.get("description", "")}
        for suite_id, suite in SUITES.items()
    }
    payload = {"schema_version": 1, "suites": suites, "cases": cases}
    output = Path(__file__).with_name("cases.json")
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {len(cases)} FBase cases to {output}")


if __name__ == "__main__":
    main()
