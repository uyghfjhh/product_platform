"""Standalone pgcluster configuration validation and fact extraction script."""

import json
import sys

from pgclusterlib.config import load
from pgclusterlib.runtime import Runtime

config = load(sys.argv[1])
target = sys.argv[2]
config.validate(target)
names = Runtime(config).target_instances(target)

nodes = [
    {
        "name": name,
        "host": config.instance(name)["host_config"]["address"],
        "port": config.instance(name)["port"],
        "data_dir": config.instance(name)["data_dir"],
        "installation": config.instance(name)["installation"],
    }
    for name in names
]
hosts = {
    name: {"address": host["address"], "ssh": host.get("ssh")}
    for name, host in config.hosts.items()
}
cluster_extensions = {
    f"{kind}.{name}": cluster.get("extensions") or []
    for kind, attr in (
        ("mmr", "mmr_clusters"),
        ("streaming", "streaming_clusters"),
        ("citus", "citus_clusters"),
    )
    for name, cluster in (getattr(config, attr) or {}).items()
}

print(
    json.dumps(
        {
            "nodes": nodes,
            "installations": config.installations,
            "hosts": hosts,
            "cluster_extensions": cluster_extensions,
        }
    )
)
