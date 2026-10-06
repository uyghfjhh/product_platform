"""Enroll registered, verified instances for explicit lifecycle operations."""

import hashlib
import json
import re
from pathlib import Path

import yaml

from ..config import ROOT, scope
from ..resources import canonical_host


def enroll(control_root, environment_id, config, target, runtime):
    environment_id = scope(environment_id)
    record_path = Path(control_root) / "environments" / (environment_id + ".yaml")
    if not record_path.is_file():
        raise ValueError("生命周期操作缺少登记环境")
    environment = yaml.safe_load(record_path.read_text())
    if Path(environment["deployment_config"]).resolve() != config.path:
        raise ValueError("生命周期配置与登记环境不一致")
    names = runtime.target_instances(target)
    checked = []
    for name in names:
        instance = config.instance(name)
        host = instance["host_config"]["address"]
        directory = instance["data_dir"]
        if not runtime.executor.exists(host, directory + "/PG_VERSION"):
            continue
        if runtime.executor.run(
            ["test", "-O", directory], host=host, check=False
        ).returncode:
            raise ValueError("SSH 账号必须是数据库数据目录的所有者: " + name)
        canonical = runtime.executor.realpath(host, directory)
        if Path(canonical).is_relative_to(ROOT):
            raise ValueError("数据库目录不能位于平台源码或控制面内")
        control = runtime.executor.run(
            [
                "env",
                "LC_ALL=C",
                instance["installation_config"]["home"] + "/bin/pg_controldata",
                directory,
            ],
            host=host,
        ).stdout
        match = re.search(r"Database system identifier:\s*(\d+)", control)
        if not match:
            raise ValueError("无法确认数据库系统标识: " + name)
        identity = hashlib.sha256(
            (canonical_host(host) + "\0" + canonical).encode()
        ).hexdigest()[:32]
        receipt = (
            Path(control_root)
            / "profiles"
            / environment_id
            / "managed-instances"
            / (identity + ".json")
        )
        previous = json.loads(receipt.read_text()) if receipt.is_file() else None
        if previous and previous["system_identifier"] != match[1]:
            raise ValueError("数据库身份已变化，需要重新审阅接管: " + name)
        marker = directory + "/.pgcluster-managed"
        if runtime.executor.exists(host, marker):
            claim = json.loads(runtime.executor.read_text(host, marker))
            owner = claim.get("platform_environment")
            if (
                owner
                and owner != environment_id
                and (
                    Path(control_root) / "environments" / (scope(owner) + ".yaml")
                ).is_file()
            ):
                raise ValueError("数据目录已有其他平台环境归属: " + name)
            if (
                claim.get("system_identifier")
                and claim["system_identifier"] != match[1]
            ):
                raise ValueError("数据目录归属标记与真实数据库身份不符: " + name)
        value = {
            "node": name,
            "platform_environment": environment_id,
            "system_identifier": match[1],
            "host": host,
            "data_dir": canonical,
        }
        checked.append((host, marker, receipt, value))
    # All identities and owners pass before writing any ownership receipt.
    for host, marker, receipt, value in checked:
        runtime.executor.write_text(host, marker, json.dumps(value))
        receipt.parent.mkdir(parents=True, exist_ok=True)
        temporary = receipt.with_suffix(".part")
        temporary.write_text(json.dumps(value))
        temporary.replace(receipt)
