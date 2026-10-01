import tempfile
from dataclasses import replace
from pathlib import Path

import uvicorn
from platform_app.api import create_app
from platform_app.config import load_settings

root = Path(tempfile.mkdtemp(prefix="platform-workbench-browser-"))
home = root / "installation"
(home / "bin").mkdir(parents=True)
for name in ("postgres", "pg_ctl", "psql", "pg_basebackup", "pg_config"):
    p = home / "bin" / name
    p.write_text('#!/bin/sh\necho "PostgreSQL 15.15"\n')
    p.chmod(0o755)
for name in ("fbase_mac", "fb_license", "fdd_mmr", "citus"):
    p = home / "share/extension" / (name + ".control")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("fixture")
    p = home / "lib" / (name + ".so")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("fixture")
license_file = root / "license.dat"
license_file.write_text("fixture")
settings = replace(load_settings(), data_dir=root / "data", output_dir=root / "output", runtime_dir=root / "runtime", logs_dir=root / "logs")
app = create_app(settings, enqueuer=lambda task_id: None)
(root / "fixture.json").write_text(
    __import__("json").dumps(
        {
            "home": str(home),
            "data_root": str(root / "instances"),
            "license_file": str(license_file),
            "nonce": root.name,
        }
    )
)


@app.get("/api/v1/deployment-browser-fixture")
def fixture_identity():
    return {"nonce": root.name}


app.router.routes.insert(0, app.router.routes.pop())
print(str(root / "fixture.json"), flush=True)
uvicorn.run(app, host="127.0.0.1", port=18769, log_level="warning")
