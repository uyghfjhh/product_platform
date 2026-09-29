"""fbasecman 产品适配层的用例运行时。

``FbasecmanCaseRuntime`` 在通用 ``CaseRuntime`` 之上提供 fbasecman 用例的
完整执行环境：

- 动态端口对分配与 ``FbasecmanProcess`` 生命周期（含端口被占后的重选重试）；
- ``render_conf`` 生成完整 fbasecman.conf（mmr/rep/balance/single 四种 group、
  datasources 实时元数据、monitor 状态机参数），``transform`` 钩子供用例改写；
- console/业务两类 ``psql_*`` 执行助手，统一带 EvidenceStep 证据记录与重试；
- 每条 console 变更命令后的配置文件语义级 diff 校验（只容许预期字段变化）；
- 配置备份目录的前后快照断言（``backup_checkpoint`` 系列）；
- FAIL 时自动抓取崩溃诊断（core/backtrace）并追加到报告与 summary。

suite 运行时按需继承本类，只补充自己的用例级逻辑，例如
``suites.ha_commands.runtime.HaCommandRuntime``。
"""

import difflib
import re
import shlex
import time
from pathlib import Path

from platform_regress.clients.psql import build_psql_command
from platform_regress.execution.command import run_logged_command
from platform_regress.execution.ports import free_port_pair, port_is_free
from platform_regress.reporting.model import ReportStep
from platform_regress.runtime import CaseRuntime
from products.fbasecman.process import FbasecmanProcess, FbasecmanProcessError


class BackupCheckpoint(object):
    """配置备份目录快照：记录某时刻已有的 .bak 文件集合与配置原文。"""

    def __init__(self, files, config_content, backup_dir=None):
        self.files = frozenset(files)
        self.config_content = config_content
        self.backup_dir = backup_dir


class FbasecmanCaseRuntime(CaseRuntime):
    """fbasecman 回归用例运行时：进程 + 配置模板 + console/业务断言助手。"""

    # 所有 fbasecman 用例共享同一批后端数据库，必须串行执行，共用一把锁。
    lock_name = "fbasecman_cases"

    def __init__(self, root, case, env=None, context_data=None):
        super(FbasecmanCaseRuntime, self).__init__(
            root, case, env=env, context_data=context_data)
        self.proxy_log = self.run_root / "fbasecman.log"
        self._port_seed = 1
        self.listen_port, self.read_port = free_port_pair(1)
        self.pid_file = self.workdir / "fbasecman.pid"
        self.active_conf = None
        self._crash_info = None
        # Every HA console mutation gets a before/after console snapshot.
        self.process = FbasecmanProcess(
            self.env.config["fbasecman"]["fbasecman_bin"],
            self.env.config["local"]["postgres_dir"], self.listen_port,
            self.listen_port + 2, self.pid_file, self.workdir / "locks",
            self.proxy_log, self.logs_dir, self.run_command, self.trace,
            port_is_free,
        )

    # ------------------------------------------------------------------
    # 进程与配置生命周期
    # ------------------------------------------------------------------

    def render_conf(self, transform=None):
        """渲染完整 fbasecman.conf 到 workdir/<case>.conf 并返回路径。

        datasource 元数据（system_identifier、group_uuid 等）实时从数据库
        查询而非复用缓存上下文，保证与环境当前状态一致。
        ``transform(content) -> content`` 钩子供用例改写配置文本。
        """
        db = self.env.config["database"]
        ports = db["ports"]
        sysids, real_group, group_uuid = self._live_metadata()
        lines = [
            'pid_file "%s"' % self.pid_file,
            "daemonize yes", 'unix_socket_dir "/tmp"', 'unix_socket_mode "0644"',
            'locks_dir "%s"' % (self.workdir / "locks"),
            'license_dir "%s"' % self.env.config["fbasecman"]["license_dir"],
            'priority 0', 'log_to_stdout no',
            'log_syslog no', 'log_format "%p %t %l [%i %s] (%c) %m\\n"',
            'log_syslog_ident "fbasecman"', 'log_syslog_facility "daemon"',
            'enable_guc_sync yes',
            'log_debug yes', 'log_config yes', 'log_session yes', 'log_query no',
            'log_stats yes', 'stats_interval 60', 'log_file "%s"' % self.proxy_log,
            'log_min_messages "info"', 'promhttp_server_port %s' % (self.listen_port + 2),
            # Match new-config/fbasecman-all-new.conf.  Keep this value at the
            # template setting so the regression suite exercises the shipped
            # configuration rather than an ASAN-specific variant.
            'server_login_retry 5', 'cache_msg_gc_size 0',
            'cache_coroutine 108', 'coroutine_stack_size 16',
            'workers 8', 'resolvers 1',
            'readahead 8192', 'nodelay yes',
            'log_general_stats_prom no', 'log_route_stats_prom no',
            'graceful_die_on_errors yes', 'enable_online_restart no',
            'bindwith_reuseport yes', 'keepalive 15',
            'keepalive_keep_interval 75', 'keepalive_probes 9',
            'keepalive_usr_timeout 0',
            'host "*"', 'ports "%s"' % self.listen_port, 'backlog 128',
            'compression yes', 'tls "disable"',
            'heartbeat_request "select 1"', 'admin_database "console"',
            # HA 用例显式固定 monitor 状态机参数，避免依赖产品默认值变化。
            'monitor_enabled yes', 'monitor_period 10',
            'monitor_recovery_period 10', 'monitor_retry_period_ms 1000',
            'monitor_max_retries 3', 'monitor_recovery_max_retries 3',
            'monitor_timeout 5',
            '', 'group "mmr_group" {', '    group_mode "mmr"',
            '    storage_db "postgres"', '    backend_clusters "pg_cluster_1,pg_cluster_2"',
            '    write_cluster "pg_cluster_2"', '    promoted_cluster "pg_cluster_1"',
            '    real_group_name "%s"' % real_group,
            '    group_uuid "%s"' % group_uuid, '    check "auto"', '}', '',
            'group "rep_group" {', '    group_mode "replication"',
            '    storage_db "postgres"', '    backend_clusters "pg_cluster_1"',
            '    check "auto"', '}', '',
            'group "balance_group" {', '    group_mode "balance"',
            '    storage_db "postgres"', '    access_mode "read_write"',
            '    backend_clusters "pg_cluster_1,pg_cluster_2"',
            '    check "auto"', '}', '',
            'group "single_group" {', '    group_mode "single"',
            '    storage_db "postgres"', '    access_mode "read_write"',
            '    backend_clusters "pg_cluster_1"', '    check "auto"', '}', '',
        ]
        datasource = [
            ("pg_1", ports["mmr1"], "pg_cluster_1", ""),
            ("pg_2", ports["mmr2"], "pg_cluster_2", ""),
            # These are the application_name values written into the
            # regression fixture's standby primary_conninfo by the environment MMR builder.
            # They are distinct from the fbasecman datasource aliases.
            ("pg_3", ports["mmr1_standby1"], "pg_cluster_1", "pg_240"),
            ("pg_4", ports["mmr2_standby1"], "pg_cluster_2", "pg_250"),
        ]
        self.datasource_metadata = [
            {
                "name": name,
                "host": db["mmr_host"],
                "port": port,
                "cluster": cluster,
                "application_name": application_name or "(default)",
                "system_identifier": sysids[name],
                "status": "active",
            }
            for name, port, cluster, application_name in datasource
        ]
        for name, port, cluster, application_name in datasource:
            lines.extend([
                'datasources "%s" {' % name,
                '    host "%s"' % db["mmr_host"], '    port %s' % port,
                '    cluster_name "%s"' % cluster, '    weight 10',
                '    status "active"',
                ('    application_name "%s"' % application_name) if application_name else "",
                '    system_identifier "%s"' % sysids[name],
                '    tls "disable"', '}', '',
            ])
        lines.extend([
            'user "postgres" {',
            '    group_names "mmr_group,rep_group,balance_group,single_group"',
            '    authentication "none"', '    storage_user "postgres"',
            '    pool "transaction"', '    pool_size 20', '    pool_discard no',
            '    rw_split_method "none"', '}', '',
            'user "admin" {', '    authentication "none"', '    pool "session"',
            '    role "admin"', '}', '',
        ])
        path = self.workdir / (self.case.name + ".conf")
        content = "\n".join(line for line in lines if line != "") + "\n"
        if transform is not None:
            content = transform(content)
        path.write_text(content, encoding="utf-8")
        return path

    def start(self, transform=None, env=None):
        """渲染配置并启动 fbasecman；监听端口被占时自动换端口对重试 3 次。"""
        for attempt in range(3):
            conf = self.render_conf(transform=transform)
            ports = (self.listen_port, self.read_port, self.listen_port + 2)
            availability = tuple(port_is_free(port) for port in ports)
            if all(availability):
                break
            self.trace("[retry] rendered listener ports became busy: %s" %
                       ",".join(str(port) for port, free in zip(ports, availability) if not free))
            self._port_seed += 1
            self.listen_port, self.read_port = free_port_pair(self._port_seed)
            self.process.listen_port = self.listen_port
            self.process.prom_port = self.listen_port + 2
        else:
            raise self.failure_class("listener ports became busy while rendering configuration")
        self.process.start(conf, ready_timeout=90.0, record=False, env=env)
        self.active_conf = conf
        self.record_step(
            "启动 fbasecman", "%s %s" % (self.process.binary, conf),
            "console 可连接且目标 group (%s) 配置加载成功" %
            ", ".join(getattr(self.case, "report_groups", ())),
            "console ready",
            "PASS", [("监听端口", str(self.listen_port)), ("配置文件", str(conf))],
        )
        return conf

    def start_rejected(self, transform, title, expected, predicate):
        """验证非法配置会被启动期拒绝：预期启动失败且错误信息匹配 predicate。"""
        conf = self.render_conf(transform=transform)
        try:
            self.process.start(conf, ready_timeout=3.0, record=False)
        except FbasecmanProcessError as exc:
            actual = str(exc)
            passed = predicate(actual)
            self.record_step(title, "%s %s" % (self.process.binary, conf),
                             expected, actual, "PASS" if passed else "FAIL",
                             [("配置文件", str(conf))])
            if not passed:
                raise self.failure_class("%s: unexpected startup error: %s" %
                                         (title, actual))
            return conf
        self.record_step(title, "%s %s" % (self.process.binary, conf),
                         expected, "fbasecman unexpectedly started", "FAIL",
                         [("配置文件", str(conf))])
        raise self.failure_class("%s: fbasecman unexpectedly started" % title)

    def stop(self):
        self.process.stop(best_effort=True, record=False)

    def postgres_node_action(self, pgdata, action, title):
        """Stop/start one remote PostgreSQL fixture node for monitor probes."""
        if action not in ("start", "stop"):
            raise ValueError("unsupported postgres node action: %s" % action)
        db = self.env.config["database"]
        pgctl = "%s/bin/pg_ctl" % db["mmr_postgres_dir"]
        data = shlex.quote(str(pgdata))
        logfile = shlex.quote(str(pgdata) + "/logfile")
        if action == "stop":
            script = ("%s -D %s status >/dev/null 2>&1 && "
                      "%s -D %s stop -m immediate -w && "
                      "! %s -D %s status >/dev/null 2>&1" %
                      (pgctl, data, pgctl, data, pgctl, data))
        else:
            script = ("%s -D %s status >/dev/null 2>&1 || "
                      "%s -D %s start -l %s -w; "
                      "%s -D %s status >/dev/null 2>&1" %
                      (pgctl, data, pgctl, data, logfile, pgctl, data))
        command = ["ssh", "-F", "/dev/null",
                   "%s@%s" % (db["mmr_pg_user"], db["mmr_host"]), script]
        return self.run_command(command, self.logs_dir / ("node_%s.log" % action),
                                cwd=self.workdir, step_title=title)

    # ------------------------------------------------------------------
    # 实时元数据（直接从 PostgreSQL 查询，避免上下文缓存过期）
    # ------------------------------------------------------------------

    def _query_scalar(self, port, sql, log_name):
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"],
            self.env.config["database"]["mmr_host"], port,
            self.env.config["database"]["mmr_pg_user"], "postgres", sql,
            footer=False, output_format="unaligned", tuples_only=True,
        )
        result = run_logged_command(command, self.logs_dir / log_name, cwd=self.workdir)
        value = result.output.strip()
        if result.returncode != 0 or not value:
            raise self.failure_class("metadata query failed: %s\n%s" % (result.command, result.output))
        return value.splitlines()[-1].strip()

    def _live_metadata(self):
        """Read topology identities from PostgreSQL instead of stale context data."""
        ports = self.env.config["database"]["ports"]
        system_identifiers = {}
        for name, port in (("pg_1", ports["mmr1"]), ("pg_2", ports["mmr2"]),
                           ("pg_3", ports["mmr1_standby1"]),
                           ("pg_4", ports["mmr2_standby1"])):
            system_identifiers[name] = self._query_scalar(
                port, "SELECT system_identifier FROM pg_control_system();",
                "metadata_%s_system_identifier.log" % name,
            )
        group = self._query_scalar(
            ports["mmr1"],
            "SELECT group_name || '|' || group_uuid FROM fdd.mmr_group ORDER BY group_name LIMIT 1;",
            "metadata_mmr_group.log",
        )
        fields = group.split("|", 1)
        if len(fields) != 2 or not all(fields):
            raise self.failure_class("invalid MMR metadata response: %s" % group)
        return system_identifiers, fields[0], fields[1]

    # ------------------------------------------------------------------
    # console 状态快照（HA 命令前后各取一次，报告里做对比佐证）
    # ------------------------------------------------------------------

    def _ha_state_sql(self, sql):
        """按命令语义选取对应的 SHOW 状态查询，用于变更前后的状态快照。"""
        upper = sql.upper()
        if upper.startswith("SET CLUSTER") or " PARTED" in upper or " ACTIVE" in upper:
            return "SHOW NODE_STATUS;"
        if "WEIGHT" in upper:
            return "SHOW NODES;"
        if "WRITE" in upper or "PROMOTED" in upper or "GROUP" in upper:
            match = re.search(r'(?i)\bIN\s+GROUPS?\s*\(?\s*([A-Za-z_][A-Za-z0-9_]*)', sql)
            return "SHOW GROUP_ROUTING %s;" % (match.group(1) if match else "mmr_group")
        return "SHOW NODES;"

    def _record_ha_state(self, sql, phase):
        state_sql = self._ha_state_sql(sql)
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"], "127.0.0.1",
            self.listen_port, "admin", "console", state_sql,
        )
        result = run_logged_command(
            command, self.logs_dir / ("ha_state_%02d.log" % self._next_order()),
            cwd=self.workdir,
        )
        output = result.output.rstrip() or "<empty>"
        self.record_step(
            "%s：%s" % (phase, state_sql),
            "$ %s" % result.command, "console 状态可读取", output,
            "PASS" if result.returncode == 0 else "FAIL",
        )
        if result.returncode != 0:
            raise self.failure_class("HA state query failed: %s" % state_sql)
        return output

    # ------------------------------------------------------------------
    # fbasecman.conf 语义级 diff（忽略格式差异，只比对 group/datasources 字段）
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_inline_comment(value):
        quoted = False
        escaped = False
        for index, char in enumerate(value):
            if escaped:
                escaped = False
                continue
            if char == "\\" and quoted:
                escaped = True
                continue
            if char == '"':
                quoted = not quoted
            elif char == "#" and not quoted:
                return value[:index].rstrip()
        return value.strip()

    @staticmethod
    def _semantic_objects(text):
        objects = {}
        order = []
        pattern = re.compile(
            r'(?ms)^\s*(group|datasources)\s+"([^"]+)"\s*\{(.*?)\}')
        for match in pattern.finditer(text):
            kind, name, body = match.groups()
            identity = (kind, name)
            if identity in objects:
                raise ValueError("duplicate object %s %s" % identity)
            fields = {}
            token_text = " ".join(
                FbasecmanCaseRuntime._strip_inline_comment(raw).strip()
                for raw in body.splitlines()
                if FbasecmanCaseRuntime._strip_inline_comment(raw).strip()
            )
            field_pattern = re.compile(
                r'([A-Za-z_][A-Za-z0-9_]*)\s+("(?:\\.|[^"\\])*"|[^\s]+)')
            for field_match in field_pattern.finditer(token_text):
                key, value = field_match.groups()
                if key in fields:
                    raise ValueError("duplicate field %s.%s" % (name, key))
                fields[key] = value
            objects[identity] = fields
            order.append(identity)
        return objects, order

    @staticmethod
    def _command_scope(sql, before_objects):
        """推导一条 SET 命令"允许变更"的字段集合，用于 diff 白名单校验。"""
        upper = sql.upper()
        allowed = {}
        if " WRITE " in upper or " PROMOTED " in upper:
            names = []
            groups = re.search(r'(?i)\bIN\s+GROUPS\s*\(([^)]*)\)', sql)
            group = re.search(r'(?i)\bIN\s+GROUP\s+([^\s;]+)', sql)
            if groups:
                names = [item.strip() for item in groups.group(1).split(",")]
            elif group:
                names = [group.group(1)]
            else:
                names = [name for kind, name in before_objects if kind == "group"]
            fields = {"promoted_cluster"}
            if " WRITE " in upper:
                fields.add("write_cluster")
            target = re.search(
                r'(?i)^SET\s+(?:NODE|CLUSTER)\s+(?:WRITE|PROMOTED)\s+([^\s]+)',
                sql)
            datasource = target.group(1).strip(";,()") if target else ""
            cluster = datasource
            if re.match(r'(?i)^SET\s+NODE\s+', sql):
                # SET NODE 的目标是 datasource，需先反查它所属的 cluster
                cluster = next(
                    (v.get("cluster_name", "").strip('"')
                     for (k, n), v in before_objects.items()
                     if k == "datasources" and n == datasource),
                    datasource)
            for name in names:
                for field in fields:
                    if field == "promoted_cluster" and " WRITE " in upper:
                        # WRITE 命令同时会重置 promoted_cluster 为原 write_cluster 值
                        prior = before_objects.get(("group", name), {}).get("write_cluster")
                        allowed[("group", name, field)] = prior
                    else:
                        allowed[("group", name, field)] = '"%s"' % cluster
        elif upper.startswith("SET CLUSTER "):
            match = re.search(r'(?i)^SET\s+CLUSTER\s+(?:ACTIVE|PARTED)\s+([^\s;]+)', sql)
            cluster = match.group(1) if match else ""
            value = '"active"' if " ACTIVE " in upper else '"parted"'
            allowed = {
                (kind, name, "status"): value for (kind, name), fields in before_objects.items()
                if kind == "datasources" and fields.get("cluster_name", "").strip('"') == cluster
            }
        else:
            field = "weight" if " WEIGHT " in upper else "status"
            targets = set(re.findall(r'\b[A-Za-z_][A-Za-z0-9_]*\b', sql))
            assignments = dict(re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\d+)', sql))
            allowed = {
                (kind, name, field): assignments.get(name) for kind, name in before_objects
                if kind == "datasources" and name in targets
            }
            # Resolve host:port targets against the configuration snapshot.
            for (kind, name), fields in before_objects.items():
                endpoint = "%s:%s" % (fields.get("host", "").strip('"'), fields.get("port", ""))
                if kind == "datasources" and endpoint in sql:
                    value = assignments.get(name)
                    allowed[(kind, name, field)] = value
        return allowed

    def _semantic_config_diff(self, before, after, sql):
        """对比命令前后配置：只有 _command_scope 白名单内的变化才算预期。"""
        try:
            old, old_order = self._semantic_objects(before)
            new, new_order = self._semantic_objects(after)
        except ValueError as exc:
            return False, "配置结构错误: %s" % exc
        lines = []
        valid = old_order == new_order and set(old) == set(new)
        if old_order != new_order:
            lines.append("对象顺序发生变化")
        allowed = self._command_scope(sql, old)
        for identity in sorted(set(old) | set(new)):
            old_fields, new_fields = old.get(identity, {}), new.get(identity, {})
            for field in sorted(set(old_fields) | set(new_fields)):
                old_value, new_value = old_fields.get(field), new_fields.get(field)
                if old_value == new_value:
                    continue
                change = (identity[0], identity[1], field)
                expected = change in allowed and (allowed[change] is None or allowed[change] == new_value)
                lines.append("%s %s.%s: %s -> %s%s" % (
                    identity[0], identity[1], field,
                    old_value, new_value, "" if expected else " [非预期]"))
                valid = valid and expected
        return valid, "\n".join(lines) or "<no semantic changes>"

    # ------------------------------------------------------------------
    # psql 执行助手（console 管理面 / business 业务面 两个入口）
    # ------------------------------------------------------------------

    def psql(self, sql, title, expected, predicate):
        """执行一条 console 命令并断言输出；命令后自动做配置语义 diff 校验。"""
        self.last_ha_sql = sql.strip()
        config_before = self.active_conf.read_text(encoding="utf-8") if self.active_conf else None
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"], "127.0.0.1",
            self.listen_port, "admin", "console", sql,
        )
        with self.evidence_step(title, expected=expected) as step:
            retry_enabled = sql.lstrip().upper().startswith("SHOW ")
            deadline = time.time() + (30 if retry_enabled else 0)
            started = time.time()
            order = self._next_order()
            attempt = 0
            while True:
                attempt += 1
                result = run_logged_command(
                    command,
                    self.logs_dir / ("psql_%02d_%02d.log" % (order, attempt)),
                    cwd=self.workdir,
                )
                output = result.output.rstrip() or "<empty>"
                passed = result.returncode == 0 and predicate(output)
                if passed or time.time() >= deadline:
                    break
                time.sleep(0.2)
            step.actual_execution("$ %s" % result.command, output)
            actual = "returncode=%s attempts=%s elapsed=%.2fs" % (
                result.returncode, attempt, time.time() - started)
            step.assess(expected, actual, passed)
        if not passed:
            raise self.failure_class("%s: %s" % (title, actual))
        if config_before is not None:
            config_after = self.active_conf.read_text(encoding="utf-8")
            if config_before != config_after:
                diff_text = "".join(difflib.unified_diff(
                    config_before.splitlines(True), config_after.splitlines(True),
                    fromfile="config.before", tofile="config.after"))
                valid_diff, semantic_diff = self._semantic_config_diff(
                    config_before, config_after, self.last_ha_sql)
                self.record_step(
                    "%s：配置文件实际 diff（对应命令：%s）" %
                    (title, self.last_ha_sql),
                    "高可用命令: %s\ndiff -u config.before config.after" %
                    self.last_ha_sql,
                    "只包含本次高可用命令预期修改",
                    "语义变化:\n%s\n\n原始文本 diff (--minimal):\n%s" %
                    (semantic_diff, diff_text.rstrip() or "<no differences>"),
                    "PASS" if valid_diff else "FAIL",
                )
                if not valid_diff:
                    raise self.failure_class(
                        "configuration persistence changed an unexpected object or field")
        return output

    def psql_monitor(self, sql, title, expected, predicate, retry_timeout=5):
        """Run monitor SHOW in expanded form so field assertions are exact."""
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"], "127.0.0.1", self.listen_port,
            "admin", "console", sql, expanded=True,
        )
        with self.evidence_step(title, expected=expected) as step:
            deadline = time.time() + max(0, retry_timeout)
            started = time.time()
            attempt = 0
            while True:
                attempt += 1
                result = run_logged_command(
                    command,
                    self.logs_dir / ("psql_monitor_%02d_%02d.log" %
                                     (self._step_order, attempt)),
                    cwd=self.workdir)
                output = result.output.rstrip() or "<empty>"
                passed = result.returncode == 0 and predicate(output)
                if passed or time.time() >= deadline:
                    break
                time.sleep(0.2)
            step.actual_execution("$ %s" % result.command, output)
            step.assess(expected,
                        "returncode=%s attempts=%s elapsed=%.2fs" %
                        (result.returncode, attempt, time.time() - started),
                        passed)
        if not passed:
            raise self.failure_class("%s: returncode=%s" % (title, result.returncode))
        return output

    @staticmethod
    def _expanded_rows(output, key):
        """Parse psql expanded output into rows indexed by one text column."""
        rows = {}
        current = {}
        for line in output.splitlines():
            if re.match(r"^-\[ RECORD", line):
                if current.get(key):
                    rows[current[key]] = current
                current = {}
                continue
            match = re.match(r"^([a-z_]+)\s*\|\s*(.*?)\s*$", line)
            if match:
                current[match.group(1)] = match.group(2)
        if current.get(key):
            rows[current[key]] = current
        return rows

    def wait_node_monitor(self, title, expected_rows, retry_timeout=30):
        """Wait until named node projections expose the requested trusted fields."""
        expected = "; ".join(
            "%s %s" % (node, ", ".join(
                "%s=%s" % (field, value)
                for field, value in sorted(fields.items())))
            for node, fields in sorted(expected_rows.items()))

        def matches(output):
            rows = self._expanded_rows(output, "node_name")
            for node, fields in expected_rows.items():
                row = rows.get(node)
                if row is None or any(row.get(field) != value
                                      for field, value in fields.items()):
                    return False
            return True

        return self.psql_monitor(
            "SHOW NODE_MONITOR;", title, expected, matches,
            retry_timeout=retry_timeout)

    def assert_table(self, sql, title, expected_rows, key="node_name", retry_timeout=15):
        """Execute a console query and assert table rows with structured diffs."""
        from platform_regress.clients.psql import assert_table_rows

        expected_desc = "; ".join(
            "%s [%s]" % (k, ", ".join("%s=%s" % (col, val) for col, val in v.items()))
            for k, v in expected_rows.items()
        )
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"], "127.0.0.1", self.listen_port,
            "admin", "console", sql,
        )
        with self.evidence_step(title, expected=expected_desc) as step:
            deadline = time.time() + max(0, retry_timeout)
            started = time.time()
            attempt = 0
            last_summary = ""
            while True:
                attempt += 1
                result = run_logged_command(
                    command,
                    self.logs_dir / ("assert_table_%02d_%02d.log" % (self._step_order, attempt)),
                    cwd=self.workdir,
                )
                output = result.output.rstrip() or "<empty>"
                passed, summary, _ = assert_table_rows(output, expected_rows, key=key)
                last_summary = summary
                if (result.returncode == 0 and passed) or time.time() >= deadline:
                    break
                time.sleep(0.3)

            step.actual_execution("$ %s" % result.command, output)
            step.assess(expected_desc, last_summary, result.returncode == 0 and passed)

        if result.returncode != 0 or not passed:
            raise self.failure_class("%s 失败:\n%s" % (title, last_summary))
        return output

    def psql_business(self, sql, title, expected, predicate, group="mmr_group",
                      port=None, user="postgres", retry_timeout=0):
        """Run a query through the configured MMR business route."""
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"], "127.0.0.1",
            port or self.listen_port, user, group, sql,
        )
        deadline = time.time() + retry_timeout
        attempt = 0
        order = self._next_order()
        with self.evidence_step(title, expected=expected) as step:
            while True:
                attempt += 1
                result = run_logged_command(
                    command, self.logs_dir / ("psql_business_%02d_%02d.log" % (order, attempt)),
                    cwd=self.workdir,
                )
                output = result.output.rstrip() or "<empty>"
                passed = result.returncode == 0 and predicate(output)
                if passed or time.time() >= deadline:
                    break
                time.sleep(0.2)
            step.actual_execution("$ %s" % result.command, output)
            actual = "returncode=%s attempts=%s" % (result.returncode, attempt)
            step.assess(expected, actual, passed)
        if not passed:
            raise self.failure_class("%s: %s" % (title, actual))
        return output

    def psql_business_error(self, sql, title, expected, predicate,
                            group="mmr_group", port=None, user="postgres"):
        """执行业务 SQL 并断言它按预期失败（rc != 0 且输出匹配 predicate）。"""
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"], "127.0.0.1",
            port or self.listen_port, user, group, sql,
        )
        with self.evidence_step(title, expected=expected) as step:
            result = run_logged_command(
                command, self.logs_dir / ("psql_business_error_%02d.log" % self._next_order()),
                cwd=self.workdir,
            )
            output = result.output.rstrip() or "<empty>"
            step.actual_execution("$ %s" % result.command, output)
            passed = result.returncode != 0 and predicate(output)
            actual = "returncode=%s" % result.returncode
            step.assess(expected, actual, passed)
        if not passed:
            raise self.failure_class("%s: %s" % (title, actual))
        return output

    def psql_error(self, sql, title, expected, predicate, compare_config=True):
        """执行 console 命令并断言被拒绝；可选校验配置未被污染。"""
        config_before = self.active_conf.read_bytes() if self.active_conf else None
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"], "127.0.0.1",
            self.listen_port, "admin", "console", sql,
        )
        with self.evidence_step(title, expected=expected) as step:
            result = run_logged_command(
                command, self.logs_dir / ("psql_error_%02d.log" % self._next_order()),
                cwd=self.workdir,
            )
            output = result.output.rstrip() or "<empty>"
            step.actual_execution("$ %s" % result.command, output)
            passed = result.returncode != 0 and predicate(output)
            actual = "returncode=%s" % result.returncode
            step.assess(expected, actual, passed)
        if not passed:
            raise self.failure_class("%s: %s" % (title, actual))
        if config_before is not None and compare_config:
            config_after = self.active_conf.read_bytes()
            unchanged = config_before == config_after
            self.record_step(
                "%s：验证错误命令未修改活动配置" % title,
                "逐字节比较命令执行前后的活动配置文件",
                "配置文件内容保持不变",
                "config unchanged=%s" % unchanged,
                "PASS" if unchanged else "FAIL",
            )
            if not unchanged:
                raise self.failure_class("rejected HA command changed active configuration")
        return output

    # ------------------------------------------------------------------
    # 配置 diff 与备份断言
    # ------------------------------------------------------------------

    def diff(self, before, after):
        """断言配置文件与测试开始时的基线完全一致（diff 为空）。"""
        command = ["diff", "-u", str(before), str(after)]
        result = run_logged_command(command, self.logs_dir / "config_diff.log", cwd=self.workdir)
        output = result.output.rstrip() or "<no differences>"
        self.record_step("检查配置文件 diff（与测试开始时初始配置比较）", result.command,
                         "与测试开始时保存的初始配置无差异", output,
                         "PASS" if result.returncode == 0 else "FAIL")
        if result.returncode != 0:
            raise self.failure_class("configuration diff is not empty: %s" % output)

    def diff_contains(self, before, after, expected, title):
        """断言配置 diff 恰好包含 expected 中列出的变化内容。"""
        command = ["diff", "-u", str(before), str(after)]
        result = run_logged_command(
            command, self.logs_dir / ("config_diff_%02d.log" % (self._step_order + 1)),
            cwd=self.workdir,
        )
        output = result.output.rstrip() or "<no differences>"
        passed = result.returncode == 1 and all(item in output for item in expected)
        self.record_step(
            title, result.command, "diff 包含: %s" % ", ".join(expected), output,
            "PASS" if passed else "FAIL",
        )
        if not passed:
            raise self.failure_class("configuration diff did not contain expected change: %s" % output)

    @staticmethod
    def _backup_dir_path(config_path, backup_dir):
        if backup_dir is not None:
            return Path(backup_dir)
        return Path(config_path).parent / "conf-backup"

    def backup_checkpoint(self, config_path, backup_dir=None):
        """记录备份目录现状快照，供命令执行后对比新增备份文件。"""
        backup_dir = self._backup_dir_path(config_path, backup_dir)
        files = tuple(path.name for path in backup_dir.iterdir()
                      if ".bak." in path.name) if backup_dir.is_dir() else ()
        return BackupCheckpoint(files, Path(config_path).read_bytes(), backup_dir)

    def assert_backup_created(self, checkpoint, config_path, title="验证配置备份文件"):
        """断言恰好新增一个 .bak 备份且内容与命令前配置逐字节一致。"""
        backup_dir = checkpoint.backup_dir or self._backup_dir_path(config_path, None)
        current = set(path.name for path in backup_dir.iterdir()
                      if ".bak." in path.name) if backup_dir.is_dir() else set()
        created = sorted(current - set(checkpoint.files))
        content_matches = False
        if len(created) == 1:
            content_matches = (backup_dir / created[0]).read_bytes() == checkpoint.config_content
        actual = "新增备份=%s；备份数量 %d -> %d；内容与命令前配置%s" % (
            created[0] if len(created) == 1 else created,
            len(checkpoint.files), len(current),
            "一致" if content_matches else "不一致",
        )
        passed = len(created) == 1 and content_matches
        self.record_step(title, "检查备份目录 %s" % backup_dir,
                         "恰好新增一个备份，且内容等于命令执行前配置",
                         actual, "PASS" if passed else "FAIL")
        if not passed:
            raise self.failure_class("backup verification failed: %s" % actual)
        return created[0]

    def assert_no_backup_created(self, checkpoint, config_path,
                                 title="验证未创建配置备份"):
        """断言备份文件集合未发生任何变化（用于拒绝类命令）。"""
        backup_dir = checkpoint.backup_dir or self._backup_dir_path(config_path, None)
        current = set(path.name for path in backup_dir.iterdir()
                      if ".bak." in path.name) if backup_dir.is_dir() else set()
        created = sorted(current - set(checkpoint.files))
        actual = "新增备份=%s；备份数量 %d -> %d" % (
            created, len(checkpoint.files), len(current),
        )
        passed = current == set(checkpoint.files)
        self.record_step(title, "检查备份目录 %s" % backup_dir,
                         "备份文件集合保持不变", actual,
                         "PASS" if passed else "FAIL")
        if not passed:
            raise self.failure_class("unexpected backup created: %s" % actual)

    # ------------------------------------------------------------------
    # 报告钩子
    # ------------------------------------------------------------------

    def datasource_config_lines(self):
        """按用例 report_groups 过滤出相关 datasource 的报告配置行。"""
        group_clusters = {
            "mmr_group": ("pg_cluster_1", "pg_cluster_2"),
            "rep_group": ("pg_cluster_1",),
            "balance_group": ("pg_cluster_1", "pg_cluster_2"),
            "single_group": ("pg_cluster_1",),
        }
        relevant_clusters = set()
        for group in getattr(self.case, "report_groups", ()):
            relevant_clusters.update(group_clusters.get(group, ()))
        report_datasources = [
            item for item in getattr(self, "datasource_metadata", [])
            if getattr(self.case, "report_all_datasources", False)
            or item["cluster"] in relevant_clusters
        ]
        return [
            "datasource %s: host=%s port=%s cluster=%s application_name=%s "
            "system_identifier=%s status=%s" % (
                item["name"], item["host"], item["port"], item["cluster"],
                item["application_name"], item["system_identifier"],
                item["status"],
            )
            for item in report_datasources
        ], report_datasources

    def report_config_lines(self):
        """报告"关键配置"区块：拓扑/端口/手动启动命令 + datasource + group 描述。"""
        datasource_lines, report_datasources = self.datasource_config_lines()
        group_descriptions = {
            "mmr_group": "group mmr_group: mode=mmr backend_clusters=pg_cluster_1,pg_cluster_2 "
                         "write_cluster=pg_cluster_2 promoted_cluster=pg_cluster_1 check=auto",
            "rep_group": "group rep_group: mode=replication backend_clusters=pg_cluster_1 "
                         "access_mode=default check=auto",
            "balance_group": "group balance_group: mode=balance "
                             "backend_clusters=pg_cluster_1,pg_cluster_2 "
                             "access_mode=read_write check=auto",
            "single_group": "group single_group: mode=single backend_clusters=pg_cluster_1 "
                            "access_mode=read_write check=auto",
        }
        if self.case.name == "reload_failure_rollback":
            group_descriptions["single_group"] = (
                "group single_group: mode=single backend_clusters=pg_cluster_1 "
                "access_mode=read_only check=auto (唯一 active replica=pg_3)"
            )
        # 个别用例（如综合配置校验）自定义完整 group 描述时优先采用
        configured_group_lines = getattr(self, "comprehensive_group_lines", None)
        if configured_group_lines is not None:
            group_lines = list(configured_group_lines)
        else:
            group_lines = [group_descriptions[group] for group in
                           getattr(self.case, "report_groups", ())
                           if group in group_descriptions]
        group_lines.append("routing ports: proxy=%s; %s" % (
            self.listen_port,
            "; ".join("%s=%s" % (item["name"], item["port"])
                      for item in report_datasources),
        ))
        if getattr(self.case, "route_mode", None):
            group_lines.append("routing mode: %s" % self.case.route_mode)
        return [
            "测试拓扑: %s" % getattr(self.case, "topology", "-"),
            "控制台命令端口: %s" % self.listen_port,
            "手动启动命令: %s %s --console --log_to_stdout" % (
                shlex.quote(str(self.process.binary)),
                shlex.quote(str(self.active_conf or (self.workdir / (self.case.name + ".conf")))),
            ),
        ] + datasource_lines + group_lines

    def collect_failure_steps(self, status):
        """FAIL 时抓取 fbasecman 崩溃诊断（signal/core/backtrace）追加为报告步骤。"""
        self._crash_info = None
        if status != "FAIL":
            return []
        try:
            start_ts = self.started_at.timestamp() if hasattr(self.started_at, "timestamp") else time.time() - 3600
            self._crash_info = self.process.diagnose_crash(
                search_dirs=[self.workdir, self.run_root, self.logs_dir],
                since_time=start_ts,
            )
            if not (self._crash_info.get("is_crash") or self._crash_info.get("backtrace")):
                return []
            bt = self._crash_info.get("backtrace")
            if bt:
                try:
                    (self.run_root / "backtrace.txt").write_text(bt, encoding="utf-8")
                except Exception:
                    pass
            # 注意：ReportStep 没有 command 字段，命令文本放 execution 块
            return [ReportStep(
                title="🚨 进程崩溃诊断 (Crash Forensics)",
                execution=[{"label": "诊断命令",
                            "text": "gdb --batch -ex 'bt full' [binary] [core]"}],
                intermediate="崩溃信号: %s\nCore 文件: %s\n\n%s" % (
                    self._crash_info.get("signal") or "异常终止",
                    self._crash_info.get("core_file") or "未定位到 core 文件",
                    bt or "无调用栈信息",
                ),
                expected="fbasecman 正常执行，无 SIGSEGV/SIGABRT/SIGBUS 崩溃",
                actual="检测到异常崩溃退出并抓取到现场",
                result="FAIL",
            )]
        except Exception as crash_err:
            self.trace("[crash] failed to diagnose crash: %s" % crash_err)
            return []

    def summary_extra(self):
        """崩溃信息一并写入 summary.json，供 JUnit/Web 报告标记崩溃用例。"""
        info = self._crash_info
        if info and info.get("is_crash"):
            return {"crash_info": {
                "signal": info.get("signal"),
                "core_file": info.get("core_file"),
            }}
        return {}
