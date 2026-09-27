import json
from pathlib import Path
import datetime
import uuid
import yaml

env_id = f"env_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

nodes = {
    "mac_primary": {
        "data_dir": "/home/postgres/pgdata/mac1",
        "host": "127.0.0.1",
        "port": 15432,
        "role": "primary",
    },
    "mac_standby": {
        "data_dir": "/home/postgres/pgdata/mac2",
        "host": "127.0.0.1",
        "port": 15433,
        "role": "standby",
    },
    "logical_subscriber": {
        "data_dir": "/home/postgres/pgdata/mac3",
        "host": "127.0.0.1",
        "port": 15434,
        "role": "logical_subscriber",
    },
}

for name, info in nodes.items():
    d = Path(info["data_dir"])
    if d.is_dir():
        marker = d / ".fbase_regress_v2.json"
        marker.write_text(json.dumps({"cluster": "mac", "env_id": env_id, "node": name}, ensure_ascii=False, sort_keys=True), encoding="utf-8")

state_payload = {
    "cluster": "mac",
    "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
    "env_id": env_id,
    "nodes": nodes,
    "state": "running",
}

for r_path in [
    "/home/postgres/fly_dev/product_platform/regress/fbase",
]:
    out_dir = Path(r_path) / "output" / "envs" / "mac"
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "state.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump(state_payload, f, default_flow_style=False)

print("Synced MAC markers and state.yaml successfully.")
