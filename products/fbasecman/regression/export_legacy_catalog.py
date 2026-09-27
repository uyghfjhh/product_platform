"""One-time export of fbasecman case metadata from its legacy suite registry."""

import json
import sys
from pathlib import Path


def main() -> None:
    legacy = Path(__file__).resolve().parent / "legacy"
    sys.path.insert(0, str(legacy))
    from suites.registry import get_default_registry

    cases = []
    for suite in get_default_registry().all_suites():
        for case in suite.get_cases():
            summary = getattr(case, "summary", "") or (
                case.notes[0] if getattr(case, "notes", None) else ""
            )
            cases.append({
                "suite": suite.id,
                "suite_title": getattr(suite, "title", suite.id),
                "suite_description": getattr(suite, "description", ""),
                "target": case.target,
                "name": getattr(case, "name", case.target.split(".")[-1]),
                "title": summary or case.target,
                "core_id": getattr(case, "core_id", "") or "",
                "summary": summary,
                "enabled": bool(getattr(case, "enabled", True)),
                "tags": list(getattr(case, "tags", []) or []),
            })
    output = Path(__file__).with_name("catalog.json")
    output.write_text(json.dumps({"schema_version": 1, "cases": cases},
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {len(cases)} fbasecman cases to {output}")


if __name__ == "__main__":
    main()
