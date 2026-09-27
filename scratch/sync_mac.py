import json
from pathlib import Path
import datetime
import uuid
import yaml

env_id = f"env_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
nodes = {
    "mac_primary": {"data_dir": "/home/postgres/pgdata/mac1", "host": "127.0.0.1", "port": 15432, "role": "primary"},
    "mac_standby": {"data_dir": "/home/postgres/pgdata/mac2", "host": "127.0.0.1", "port": 15433, "role": "standby"},
    "logical_subscriber": {"data_dir": "/home/postgres/pgdata/mac3", "host": "127.0.0.1", "port": 15434, "role": "logical_subscriber"},
}

for name, info in nodes.items():
    d = Path(info["data_dir"])
    if d.is_dir():
        marker = d / ".fbase_regress_v2.json"
        marker.write_text(json.dumps({"cluster": "mac", "env_id": env_id, "node": name}, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        pg_conf = d / "postgresql.conf"
        if pg_conf.is_file():
            c = pg_conf.read_text(encoding="utf-8", errors="ignore")
            if "include_if_exists = 'fbase_regress.conf'" not in c:
                with pg_conf.open("a", encoding="utf-8") as f:
                    f.write("\ninclude_if_exists = 'fbase_regress.conf'\n")

state_payload = {
    "cluster": "mac",
    "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
    "env_id": env_id,
    "nodes": nodes,
    "state": "running",
}

out_dir = Path("/home/postgres/fly_dev/product_platform/regress/fbase/output/envs/mac")
out_dir.mkdir(parents=True, exist_ok=True)
with (out_dir / "state.yaml").open("w", encoding="utf-8") as f:
    yaml.safe_dump(state_payload, f, default_flow_style=False)

print("SYNC_MAC_SUCCESS")
