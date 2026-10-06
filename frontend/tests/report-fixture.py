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
    app.state.store.environments.put_environment(
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
    base = settings.artifact_dir("fbase-database", eid) / "runs" / "fixture-run" / "cases" / target
    base.mkdir(parents=True)
    evidence_ref = "artifacts/browser/command-1.json"
    evidence = base / evidence_ref
    evidence.parent.mkdir(parents=True)
    evidence.write_text(json.dumps({
        "command": "psql -c 'SELECT state'", "returncode": 0,
        "stdout": "state=ready", "stderr": "",
    }, ensure_ascii=False), encoding="utf-8")
    (base / "result.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "target": target,
                "evidence": [evidence_ref],
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
                        "command": "psql -c 'SELECT state'",
                        "execution": ["psql -c 'SELECT state'"],
                        "expected": "enabled",
                        "actual": "disabled",
                        "assertion": {"type": "rows_equal", "rows": [["enabled"]]},
                        "analysis": "断言 rows_equal 失败；实际结果不满足声明期望",
                        "evidence": [evidence_ref],
                    }
                ]
            }
        )
    )
    (base / "artifacts/browser/case-description.json").write_text(json.dumps({
        "target":target,"purpose":"验证维护进程创建节点后启动、分离后退出且不重启" if suite=="mmr" else "验证审计日志访问权限边界",
        "prerequisites":["使用隔离测试实例"],
        "final_state":"维护进程分离后已退出，supervisor 保持运行" if suite=="mmr" else "未授权账户不能查看审计日志",
        "pass_criteria":[{"title":"检查步骤验收","expected":"enabled"}],
    },ensure_ascii=False),encoding="utf-8")
    (base / "report.txt").write_text("浏览器原始报告验收", encoding="utf-8")
uvicorn.run(app, host="127.0.0.1", port=18767)
