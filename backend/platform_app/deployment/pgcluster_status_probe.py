"""Standalone pgcluster runtime status & PID probe script."""

import json
import re
import sys

from pgclusterlib.config import load
from pgclusterlib.runtime import Runtime

try:
    from pgclusterlib.executor import LOCAL_HOSTS
    from platform_app.resources import local_addresses

    LOCAL_HOSTS.update(local_addresses())
except Exception:
    pass

config = load(sys.argv[1])
target = sys.argv[2]
config.validate(target)
runtime = Runtime(config)
status = runtime.status_display(target)

# 为各运行中实例探测 postmaster (pgmaster) 根守护进程 PID
for name, info in status.items():
    if not isinstance(info, dict):
        continue
    pid = None
    if info.get("running"):
        # 1. 优先从 pg_ctl status 回显中直接提取 PID: "pg_ctl: server is running (PID: 14871)"
        msg = info.get("message") or ""
        m = re.search(r"\(PID:\s*(\d+)\)", msg)
        if m:
            try:
                pid = int(m.group(1))
            except (ValueError, TypeError):
                pid = None
        # 2. 回退到读取 ${data_dir}/postmaster.pid 首行
        if not pid:
            try:
                inst = config.instance(name)
                data_dir = inst.get("data_dir")
                address = (inst.get("host_config") or {}).get("address")
                if data_dir:
                    host = "local" if runtime.executor.is_local(address) else address
                    proc = runtime.executor.run(
                        ["head", "-n", "1", f"{data_dir}/postmaster.pid"],
                        host=host,
                        check=False,
                    )
                    if proc.returncode == 0 and proc.stdout.strip().isdigit():
                        pid = int(proc.stdout.strip())
            except Exception:
                pass
    info["pid"] = pid

print(json.dumps(status, ensure_ascii=False))
