"""Serve disposable MAC/MMR report fixtures for report-viewer.mjs (no database)."""

import json
import tempfile
from pathlib import Path

import uvicorn
from platform_app.api import create_app
from platform_app.config import Settings

root = Path(tempfile.mkdtemp(prefix="platform-report-review-"))
settings = Settings(
    data_dir=root / "data",
    output_dir=root / "output",
    pgcluster_root=root / "pgcluster",
    license_key_dir=root / "keys",
    license_vendor="测试",
)
app = create_app(settings, enqueuer=lambda task_id: None)
for suite, target in [
    ("mac", "mac.audit.log_access_restrictions"),
    ("mmr", "mmr.background.maintenance_lifecycle"),
]:
    eid = "report-" + suite
    app.state.store.put_environment(
        {
            "id": eid,
            "product_id": "fbase-database",
            "title": "报告验收 " + suite,
            "host": "127.0.0.1",
            "port": 5432,
            "database_name": "postgres",
            "database_user": "postgres",
            "deployment_target": "streaming.mac" if suite == "mac" else "mmr.regress",
        }
    )
    base = settings.output_dir / "regression" / eid / suite / target
    base.mkdir(parents=True)
    (base / "result.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "target": target,
                "evidence": [],
                "verdict": "FAIL",
                "reason": "报告验收失败原因",
                "duration_seconds": 1.2,
                "execution_id": "browser",
            }
        )
    )
    (base / "steps.json").write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "title": "检查步骤验收",
                        "result": "FAIL",
                        "expected": "enabled",
                        "actual": "disabled",
                    }
                ]
            }
        )
    )
    (base / "report.txt").write_text("浏览器原始报告验收", encoding="utf-8")
uvicorn.run(app, host="127.0.0.1", port=18767)
