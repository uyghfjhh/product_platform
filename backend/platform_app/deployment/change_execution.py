"""Reviewed database changes with durable per-step recovery checkpoints."""

import json
import re
import time
from pathlib import Path


def _quote_literal(value):
    return "E'%s'" % str(value).replace("\\", "\\\\").replace("'", "''")


STRUCTURAL_PARAMETERS = {
    "port",
    "data_directory",
    "config_file",
    "hba_file",
    "ident_file",
    "primary_conninfo",
    "primary_slot_name",
    "shared_preload_libraries",
    "wal_level",
    "hot_standby",
    "listen_addresses",
    "fdd.running_databases",
}


def configurable(parameters):
    return all(
        re.fullmatch(r"[A-Za-z_]\w*(?:\.\w+)*", key)
        and key not in STRUCTURAL_PARAMETERS
        and (
            value["to"] is None
            or isinstance(value["to"], (str, int, float, bool, list))
        )
        for key, value in parameters.items()
    )


def standby_members(config):
    return {
        row["instance"]: (name, cluster["primary"], row)
        for name, cluster in (config.get("streaming_clusters") or {}).items()
        for row in cluster.get("standbys") or []
    }


def removable(current, desired, node):
    before = standby_members(current)
    if node not in before or node in standby_members(desired):
        return False
    cluster, primary, _ = before[node]
    return (
        cluster in desired.get("streaming_clusters", {})
        and desired["streaming_clusters"][cluster]["primary"] == primary
        and primary in desired.get("instances", {})
    )


def movable(current, desired, node):
    before, after = standby_members(current), standby_members(desired)
    if node not in before or node not in after or before[node][:2] != after[node][:2]:
        return False
    old, new = current["instances"][node], desired["instances"][node]
    same_location = (
        current["hosts"][old["host"]]["address"]
        == desired["hosts"][new["host"]]["address"]
        and old["data_dir"] == new["data_dir"]
    )
    if same_location:
        return old["installation"] == new["installation"]  # port-only change
    slot = after[node][2].get("slot")
    occupied = {
        row.get("slot") or name + "_" + row["instance"] + "_slot"
        for name, cluster in current["streaming_clusters"].items()
        for row in cluster.get("standbys") or []
    }
    return bool(slot and slot not in occupied)


class ChangeExecutor:
    def __init__(self, plan, current, runtime, old_runtime, checkpoint, emit=print):
        self.plan, self.current = plan, current
        self.runtime, self.old_runtime = runtime, old_runtime
        self.path, self.emit = Path(checkpoint), emit
        self.state = (
            json.loads(self.path.read_text())
            if self.path.exists()
            else {"plan_id": plan["id"], "completed": [], "settings": {}}
        )
        if self.state["plan_id"] != plan["id"]:
            raise ValueError("变更检查点不属于当前计划")

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2))
        temporary.replace(self.path)

    def step(self, key, function):
        if key in self.state["completed"]:
            self.emit("已完成，跳过: " + key)
            return
        self.emit("执行: " + key)
        self.state["in_progress"] = key
        self.save()
        function()
        self.state["completed"].append(key)
        self.state.pop("in_progress", None)
        self.save()

    def sql(self, runtime, node, sql):
        return runtime._psql(node, sql, tuples=True)

    def stop(self, runtime, node):
        instance = runtime.config.instance(node)
        status = runtime.status_instance(node)
        if not status.get("known", True):
            raise ValueError("无法确认实例运行状态，停止变更: " + node)
        if not status["running"]:
            return
        identity = self.sql(
            runtime,
            node,
            "SELECT current_setting('data_directory') || '|' || inet_server_port()",
        )
        if identity.strip() != instance["data_dir"] + "|" + str(instance["port"]):
            raise ValueError("停止前连接身份与计划不一致: " + node)
        # A normal shutdown must succeed; never escalate to kill for a change plan.
        runtime.executor.run(
            [
                instance["installation_config"]["home"] + "/bin/pg_ctl",
                "stop",
                "-D",
                instance["data_dir"],
                "-m",
                "fast",
                "-w",
                "-t",
                "60",
            ],
            host=instance["host_config"]["address"],
        )

    def preflight(self, nodes, parameters):
        for node in nodes:
            instance = self.runtime.config.instance(node)
            identity = self.sql(
                self.runtime,
                node,
                "SELECT current_setting('data_directory') || '|' || inet_server_port()",
            )
            if identity.strip() != instance["data_dir"] + "|" + str(instance["port"]):
                raise ValueError("连接身份与计划不一致: " + node)
            settings = self.state["settings"].setdefault(node, {})
            for key in parameters:
                if key in settings:
                    continue
                raw = self.sql(
                    self.runtime,
                    node,
                    "SELECT row_to_json(t) FROM (SELECT name,setting,unit,context FROM pg_settings WHERE name="
                    + _quote_literal(key)
                    + ") t",
                )
                if not raw.strip():
                    raise ValueError("未知数据库参数: " + key)
                row = json.loads(raw)
                if row["context"] == "internal":
                    raise ValueError("内部参数不能修改: " + key)
                settings[key] = row
        # Persist original values before the first mutation.
        self.save()

    def parameters(self, nodes, parameters):
        if not configurable(parameters):
            raise ValueError("参数涉及结构配置或无效值")
        for node in nodes:
            stop_key = "restart-stop:" + node
            if (
                stop_key in self.state["completed"]
                or self.state.get("in_progress") == stop_key
            ) and not self.runtime.status_instance(node)["running"]:
                if stop_key not in self.state["completed"]:
                    self.state["completed"].append(stop_key)
                    self.save()
                self.step(
                    "restart-start:" + node,
                    lambda n=node: self.runtime.start_instance(n),
                )
        self.preflight(nodes, parameters)
        for node in nodes:
            for key, change in parameters.items():
                value = change["to"]
                if isinstance(value, list):
                    value = ",".join(str(v) for v in value)
                if isinstance(value, bool):
                    value = "on" if value else "off"
                statement = (
                    "ALTER SYSTEM RESET " + key
                    if value is None
                    else "ALTER SYSTEM SET " + key + " = " + _quote_literal(str(value))
                )
                self.step(
                    "parameter:" + node + ":" + key,
                    lambda n=node, s=statement: self.sql(self.runtime, n, s),
                )
            self.step(
                "reload:" + node,
                lambda n=node: self.sql(self.runtime, n, "SELECT pg_reload_conf()"),
            )
        standby_names = set(standby_members(self.runtime.config.raw))
        ordered = sorted(nodes, key=lambda node: (node not in standby_names, node))
        for node in ordered:
            requires_restart = any(
                self.state["settings"][node][key]["context"] == "postmaster"
                for key in parameters
            )
            if requires_restart:
                self.step(
                    "restart-stop:" + node, lambda n=node: self.stop(self.runtime, n)
                )
                self.step(
                    "restart-start:" + node,
                    lambda n=node: (
                        self.runtime.start_instance(n)
                        if not self.runtime.status_instance(n)["running"]
                        else None
                    ),
                )
            self.step(
                "settings-check:" + node,
                lambda n=node: self.check_settings(n, parameters),
            )

    def check_settings(self, node, parameters):
        # A post-reload session sees new GUC values; file errors and pending
        # restarts prevent a false successful checkpoint.
        keys = ",".join(_quote_literal(key) for key in parameters)
        errors = self.sql(
            self.runtime,
            node,
            "SELECT count(*) FROM pg_file_settings WHERE error IS NOT NULL AND name IN ("
            + keys
            + ")",
        )
        pending = self.sql(
            self.runtime,
            node,
            "SELECT count(*) FROM pg_settings WHERE pending_restart AND name IN ("
            + keys
            + ")",
        )
        if errors.strip() != "0" or pending.strip() != "0":
            raise ValueError("参数应用未完成或配置解析失败: " + node)

    def shrink(self, node):
        cluster, primary, member = standby_members(self.current)[node]
        self.check_sync_retirement(cluster, primary)
        if node not in self.state.setdefault("retired_slots", {}):
            slot = member.get("slot")
            if self.old_runtime.status_instance(node)["running"]:
                actual = self.sql(
                    self.old_runtime, node, "SHOW primary_slot_name"
                ).strip()
                if slot and actual and slot != actual:
                    raise ValueError("备库实际复制槽与计划不一致: " + node)
                slot = actual or slot
            self.state["retired_slots"][node] = slot
            self.save()
        self.step("stop-retired:" + node, lambda: self.stop(self.old_runtime, node))
        # Only the configured physical slot can be retired. An adopted standby
        # with no declared slot does not acquire an invented slot name.
        slot = self.state["retired_slots"][node]
        if slot:

            def drop_slot():
                row = self.sql(
                    self.old_runtime,
                    primary,
                    "SELECT coalesce(row_to_json(t)::text,'') FROM (SELECT slot_type,active FROM pg_replication_slots WHERE slot_name="
                    + _quote_literal(slot)
                    + ") t",
                )
                if not row.strip():
                    return
                details = json.loads(row)
                if details["slot_type"] != "physical" or details["active"]:
                    raise ValueError("缩容槽仍活跃或不是物理槽: " + slot)
                self.sql(
                    self.old_runtime,
                    primary,
                    "SELECT pg_drop_replication_slot(" + _quote_literal(slot) + ")",
                )

            self.step("retire-slot:" + node, drop_slot)
        self.emit(
            "备库已退役，原数据目录保留: " + self.current["instances"][node]["data_dir"]
        )

    def check_sync_retirement(self, cluster, primary):
        synchronous = self.sql(
            self.old_runtime, primary, "SHOW synchronous_standby_names"
        ).strip()
        if not synchronous:
            return
        remaining = (
            (self.runtime.config.raw.get("streaming_clusters") or {})
            .get(cluster, {})
            .get("standbys", [])
        )
        names = {row.get("application_name") or row["instance"] for row in remaining}
        quorum = re.search(r"(?:ANY|FIRST)\s+(\d+)", synchronous, re.I)
        required = int(quorum[1]) if quorum else 1
        if not names or len(names) < required:
            raise ValueError("缩容会破坏同步复制仲裁，请先审阅并调整同步规则")
        quoted = ",".join(_quote_literal(name) for name in names)
        available = self.sql(
            self.old_runtime,
            primary,
            "SELECT count(DISTINCT application_name) FROM pg_stat_replication WHERE application_name IN ("
            + quoted
            + ") AND sync_state IN ('sync','quorum','potential')",
        ).strip()
        if int(available) < required:
            raise ValueError("保留备库尚未满足同步复制规则，原备库不会停止")

    def scale_out(self, node, start=True):
        cluster, primary, member = standby_members(self.runtime.config.raw)[node]
        instance = self.runtime.config.instance(node)
        host = instance["host_config"]["address"]
        slot = member.get("slot") or cluster + "_" + node + "_slot"

        def ensure_slot():
            existing = self.sql(
                self.runtime,
                primary,
                "SELECT slot_type FROM pg_replication_slots WHERE slot_name="
                + _quote_literal(slot),
            )
            if existing.strip() and existing.strip() != "physical":
                raise ValueError("扩容槽名被逻辑槽占用: " + slot)
            if not existing.strip():
                self.sql(
                    self.runtime,
                    primary,
                    "SELECT pg_create_physical_replication_slot("
                    + _quote_literal(slot)
                    + ")",
                )

        self.step("new-slot:" + node, ensure_slot)

        def clone():
            if self.runtime._managed(node):
                return
            parent = self.runtime.config.instance(primary)
            self.runtime.executor.run(
                ["mkdir", "-p", str(Path(instance["data_dir"]).parent)], host=host
            )
            self.runtime.executor.run(
                [
                    instance["installation_config"]["home"] + "/bin/pg_basebackup",
                    "-h",
                    parent["host_config"]["address"],
                    "-p",
                    str(parent["port"]),
                    "-U",
                    "postgres",
                    "-D",
                    instance["data_dir"],
                    "-R",
                    "--checkpoint=fast",
                    "-X",
                    "stream",
                    "-S",
                    slot,
                ],
                host=host,
            )
            self.runtime.executor.write_text(
                host, self.runtime._marker(node), json.dumps({"node": node})
            )

        self.step("basebackup:" + node, clone)

        def configure():
            parent = self.runtime.config.instance(primary)
            parameters = dict(
                (self.runtime.config.raw.get("postgresql_config") or {}).get(
                    "parameters"
                )
                or {}
            )
            parameters.update(
                port=instance["port"],
                primary_conninfo="host="
                + parent["host_config"]["address"]
                + " port="
                + str(parent["port"])
                + " user=postgres application_name="
                + member.get("application_name", node),
                primary_slot_name=slot,
            )
            parameters.setdefault("listen_addresses", host)
            # Preserve inherited HBA and other source configuration. Only the
            # new standby's endpoint/receiver and explicitly declared GUCs change.
            self.runtime._seed_auto_conf(host, instance["data_dir"], parameters)

        self.step("new-config:" + node, configure)
        if start:
            self.step(
                "new-start:" + node,
                lambda: (
                    self.runtime.start_instance(node)
                    if not self.runtime.status_instance(node)["running"]
                    else None
                ),
            )

    def move(self, node):
        old = self.old_runtime.config.instance(node)
        new = self.runtime.config.instance(node)
        same_location = (
            old["host_config"]["address"] == new["host_config"]["address"]
            and old["data_dir"] == new["data_dir"]
        )
        if same_location:
            self.step(
                "move-port:" + node,
                lambda: self.sql(
                    self.old_runtime,
                    node,
                    "ALTER SYSTEM SET port = " + _quote_literal(new["port"]),
                ),
            )
            self.step("move-stop:" + node, lambda: self.stop(self.old_runtime, node))
            self.step(
                "move-start:" + node,
                lambda: (
                    self.runtime.start_instance(node)
                    if not self.runtime.status_instance(node)["running"]
                    else None
                ),
            )
        else:
            cluster, primary, member = standby_members(self.runtime.config.raw)[node]
            same_endpoint = (
                old["host_config"]["address"] == new["host_config"]["address"]
                and old["port"] == new["port"]
            )
            synchronous = self.sql(
                self.old_runtime, primary, "SHOW synchronous_standby_names"
            ).strip()
            application_name = member.get("application_name", node)
            if (
                same_endpoint
                and synchronous
                and "*" not in synchronous
                and not re.search(
                    r"(?<![A-Za-z0-9_])"
                    + re.escape(application_name)
                    + r"(?![A-Za-z0-9_])",
                    synchronous,
                )
            ):
                raise ValueError(
                    "同端点迁移必须保持同步规则中的 application_name，或先调整同步规则"
                )
            if (
                node not in self.state.setdefault("retired_slots", {})
                and self.old_runtime.status_instance(node)["running"]
            ):
                actual_slot = self.sql(
                    self.old_runtime, node, "SHOW primary_slot_name"
                ).strip()
                if actual_slot and member["slot"] == actual_slot:
                    raise ValueError("迁移备库必须使用独立的新复制槽")
                self.state["retired_slots"][node] = actual_slot or standby_members(
                    self.current
                )[node][2].get("slot")
                self.save()
            self.scale_out(node, start=not same_endpoint)
            if same_endpoint:
                self.step(
                    "move-same-endpoint-stop:" + node,
                    lambda: self.stop(self.old_runtime, node),
                )
                self.step(
                    "new-start:" + node,
                    lambda: (
                        self.runtime.start_instance(node)
                        if not self.runtime.status_instance(node)["running"]
                        else None
                    ),
                )
            source_lsn = self.sql(
                self.runtime, primary, "SELECT pg_current_wal_lsn()"
            ).strip()
            deadline = time.monotonic() + 60
            while (
                self.sql(
                    self.runtime,
                    node,
                    "SELECT pg_is_in_recovery() AND coalesce(pg_last_wal_replay_lsn()>="
                    + _quote_literal(source_lsn)
                    + "::pg_lsn,false)",
                ).strip()
                != "t"
            ):
                if time.monotonic() >= deadline:
                    raise ValueError("新备库尚未追平，原备库保持运行: " + node)
                time.sleep(1)
            self.shrink(node)
        self.emit("备库迁移完成: " + node)

    def run(self):
        diff = self.plan["diff"]
        nodes = self.runtime.target_instances(self.plan["target"])
        from .member_instances import MemberInstances

        members = MemberInstances(self)
        members.initialize()
        for section, changes in diff.get('topology', {}).items():
            if section in {'mmr_clusters', 'citus_clusters'}:
                from .member_changes import MemberChanges

                for name in changes['changed']:
                    MemberChanges(self, section, name).run()
            elif section == 'logical_replications':
                from .logical_changes import LogicalChanges

                for name in changes['added'] + changes['removed'] + changes['changed']:
                    LogicalChanges(self, name).run()
        for node in diff["added"]:
            if node not in members.new_nodes:
                self.scale_out(node)
        for changed in diff.get("changed", []):
            from .primary_migration import PrimaryMigration, primary_cluster

            cluster = primary_cluster(self.current, self.runtime.config.raw, changed["name"])
            if cluster:
                PrimaryMigration(self, changed["name"], cluster).run()
            else:
                self.move(changed["name"])
        if diff["parameters"]:
            self.parameters(nodes, diff["parameters"])
        for node in diff["removed"]:
            if node not in members.retired_nodes:
                self.shrink(node)
        members.retire()
        deadline = time.monotonic() + 60
        while True:
            try:
                self.runtime.verify_target(self.plan["target"])
                break
            except Exception:  # noqa: BLE001 - bounded read-only health convergence
                if time.monotonic() >= deadline:
                    raise
                self.emit("等待重启后的复制连接恢复…")
                time.sleep(1)
        if "health" not in self.state["completed"]:
            self.state["completed"].append("health")
        self.state["status"] = "APPLIED"
        self.save()
