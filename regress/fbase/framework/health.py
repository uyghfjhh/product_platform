from pathlib import Path

from framework.errors import OperationError


def _sql_literal(value):
    return "'%s'" % str(value).replace("'", "''")


class EnvironmentHealthMixin(object):
    """PostgreSQL node and replication-group health views."""

    def status_rows(self):
        state = self.store.load()
        rows = []
        roles = self.roles()
        for name, node in self.nodes.items():
            data_dir = Path(node["data_dir"])
            if not data_dir.exists():
                process_state = "not_created"
                recovery = "-"
            else:
                result = self.runner.run([
                    self.binary("pg_isready"), "-h", node["host"], "-p", node["port"],
                    "-d", "postgres", "-U", "postgres", "-t", "2",
                ], check=False)
                process_state = "running" if result.returncode == 0 else "stopped"
                if process_state == "running":
                    try:
                        recovery_value = self._query_value(
                            name, "postgres", "SELECT pg_is_in_recovery()")
                        recovery = {"t": "true", "f": "false"}.get(
                            recovery_value.lower(), recovery_value)
                    except OperationError:
                        recovery = "unknown"
                else:
                    recovery = "-"
            role = roles.get(name, "standalone")
            health = self._node_health(role, process_state, recovery)
            health_check = "plugins" if self.plugins else "postgres"
            if health == "healthy" and self.plugins:
                try:
                    if not self._plugins_healthy(name):
                        health = "unhealthy"
                except OperationError:
                    health = "unhealthy"
            if role.startswith("mmr_primary:"):
                health_check = "fdd_udf"
                if health == "healthy":
                    databases = (self.plugins.get("fdd_mmr") or {}).get(
                        "enabled_databases", ["postgres"])
                    try:
                        for database in databases:
                            udf_rows = self._mmr_udf_check(name, database, all_nodes=False)
                            if len(udf_rows) != 1 or not self._mmr_rows_healthy(udf_rows):
                                health = "unhealthy"
                                break
                    except OperationError:
                        health = "unhealthy"
            rows.append((name, role, str(node["host"]), int(node["port"]),
                         process_state, recovery, health, health_check, str(data_dir)))
        return state, rows

    @staticmethod
    def _node_health(role, process_state, recovery):
        if process_state != "running" or recovery not in ("true", "false"):
            return "unhealthy"
        expects_standby = role == "standby" or role.startswith("mmr_standby:")
        return "healthy" if (recovery == "true") == expects_standby else "unhealthy"

    @staticmethod
    def _mmr_rows_healthy(rows):
        return all(row.get("is_abnormal") == "OK" and
                   row.get("nodestate") == "ACTIVE" and
                   row.get("real_nodestate") == "ACTIVE" for row in rows)

    def _plugins_healthy(self, node_name):
        extensions = sorted(self.plugins)
        extension_names = ", ".join(_sql_literal(name) for name in extensions)
        installed = int(self._query_value(
            node_name, "postgres",
            "SELECT count(*) FROM pg_extension WHERE extname IN (%s)" % extension_names,
        ))
        if installed != len(extensions):
            return False
        required_preload = {
            name for name in extensions
            if (self.plugins.get(name) or {}).get("preload", True)
        }
        configured = self._query_value(
            node_name, "postgres", "SHOW shared_preload_libraries")
        actual_preload = {name.strip() for name in configured.split(",") if name.strip()}
        return required_preload.issubset(actual_preload)

    def streaming_status_rows(self):
        streaming = self.groups.get("streaming") or {}
        if not streaming:
            return []
        primary_name = streaming["primary"]
        standbys = streaming.get("standbys") or []
        active_senders = 0
        max_lag = None
        try:
            raw = self._query_value(
                primary_name, "postgres",
                "SELECT count(*) || '|' || "
                "coalesce(max(pg_wal_lsn_diff(pg_current_wal_lsn(), r.replay_lsn)), 0)::bigint "
                "FROM pg_stat_replication r "
                "WHERE r.state = 'streaming' AND NOT EXISTS ("
                "SELECT 1 FROM pg_replication_slots s "
                "WHERE s.active_pid = r.pid AND s.slot_type = 'logical')",
            )
            active_text, lag_text = raw.split("|", 1)
            active_senders = int(active_text)
            max_lag = int(lag_text)
        except (OperationError, ValueError):
            pass
        health = "healthy" if active_senders == len(standbys) and max_lag is not None \
            else "degraded"
        detail = "OK" if health == "healthy" else "connected=%s expected=%s" % (
            active_senders, len(standbys))
        return [("streaming", self._format_bytes(max_lag), health, detail)]

    def logical_status_rows(self):
        logical = self.groups.get("logical") or {}
        if not logical:
            return []
        database = logical.get("database", "postgres")
        publisher = logical["publisher"]
        subscribers = logical.get("subscribers") or {}
        active = 0
        lags = []
        for subscriber_name, options in subscribers.items():
            subscription = options["subscription_name"]
            slot = options["slot_name"]
            worker_active = False
            try:
                workers = int(self._query_value(
                    subscriber_name, database,
                    "SELECT count(*) FROM pg_subscription s "
                    "JOIN pg_stat_subscription st ON st.subid = s.oid "
                    "WHERE s.subname = %s AND s.subenabled "
                    "AND st.relid IS NULL AND st.pid IS NOT NULL" %
                    _sql_literal(subscription),
                ))
                worker_active = workers == 1
            except (OperationError, ValueError):
                pass
            slot_active = False
            try:
                raw = self._query_value(
                    publisher, database,
                    "SELECT active::int || '|' || "
                    "coalesce(pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn), 0)::bigint "
                    "FROM pg_replication_slots "
                    "WHERE slot_name = %s AND slot_type = 'logical' "
                    "AND coalesce(wal_status, '') <> 'lost'" % _sql_literal(slot),
                )
                active_text, lag_text = raw.split("|", 1)
                slot_active = active_text == "1"
                lags.append(int(lag_text))
            except (OperationError, ValueError):
                pass
            if worker_active and slot_active:
                active += 1
        expected = len(subscribers)
        max_lag = max(lags) if lags else None
        health = "healthy" if active == expected and len(lags) == expected else "degraded"
        detail = "OK" if health == "healthy" else "active=%s expected=%s" % (
            active, expected)
        return [("logical", self._format_bytes(max_lag), health, detail)]

    @staticmethod
    def _format_bytes(value):
        if value is None:
            return "unknown"
        value = max(0, int(value))
        units = ("B", "KiB", "MiB", "GiB", "TiB")
        amount = float(value)
        for unit in units:
            if amount < 1024 or unit == units[-1]:
                return "%d %s" % (amount, unit) if unit == "B" else "%.1f %s" % (amount, unit)
            amount /= 1024

    def mmr_status_rows(self):
        mmr_group = self.groups.get("mmr") or {}
        members = list((mmr_group.get("members") or {}).items())
        if not members:
            return []
        group_name = mmr_group["group_name"]
        databases = (self.plugins.get("fdd_mmr") or {}).get(
            "enabled_databases", ["postgres"])
        problems = []
        for database in databases:
            for member_name, relation in members:
                primary = relation["primary"]
                try:
                    udf_rows = self._mmr_udf_check(primary, database)
                    ok_count = sum(1 for row in udf_rows if self._mmr_rows_healthy([row]))
                    expected = len(members)
                    healthy = len(udf_rows) == expected and ok_count == expected
                    member_problems = [
                        "%s:%s(state=%s,real=%s)" %
                        (row.get("nodename"), row.get("detail"),
                         row.get("nodestate"), row.get("real_nodestate"))
                        for row in udf_rows
                        if row.get("is_abnormal") != "OK" or
                        row.get("nodestate") != "ACTIVE" or
                        row.get("real_nodestate") != "ACTIVE"
                    ]
                    if not healthy:
                        detail = ", ".join(member_problems) or "nodes=%s expected=%s" % (
                            len(udf_rows), expected)
                        problems.append("%s/%s: %s" % (database, member_name, detail))
                except OperationError:
                    problems.append("%s/%s: UDF failed" % (database, member_name))
        health = "healthy" if not problems else "degraded"
        detail = "all member checks passed" if not problems else "; ".join(problems)
        return [(group_name, len(members), health, detail)]

    def cluster_status_rows(self, state=None):
        state = state or self.store.load()
        if state.get("state") != "running":
            return [(self.cluster_name, "-", "unhealthy",
                     "environment=%s" % state.get("state", "-"))]
        rows = []
        for group, lag, health, detail in self.streaming_status_rows():
            status_detail = "max_replay_lag=%s" % lag
            if detail != "OK":
                status_detail += "; " + detail
            rows.append((self.cluster_name, group, health, status_detail))
        for group, lag, health, detail in self.logical_status_rows():
            status_detail = "max_slot_lag=%s" % lag
            if detail != "OK":
                status_detail += "; " + detail
            rows.append((self.cluster_name, group, health, status_detail))
        for group, members, health, detail in self.mmr_status_rows():
            status_detail = "members=%s" % members
            if detail != "all member checks passed":
                status_detail += "; " + detail
            rows.append((self.cluster_name, group, health, status_detail))
        if not rows:
            rows.append((self.cluster_name, "-", "healthy", "no replication group"))
        return rows
