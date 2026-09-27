"""One-time structured export of legacy FBase case definitions.

The legacy framework is imported only while rebuilding this transition
artifact. The platform reads the exported product data without that import.
"""

import json
import sys
from pathlib import Path


def main() -> None:
    legacy = Path(__file__).resolve().parent / "legacy"
    sys.path.insert(0, str(legacy))
    from suites import SUITES

    cases = [case for suite in SUITES.values() for case in suite["cases"]]
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
