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
import os
import re
from functools import partial
import shlex
import time
from pathlib import Path

from platform_regress.clients.psql import build_psql_command, parse_expanded_rows
from platform_regress.evidence.backup import (
    backup_content_matches,
    backup_dir_path,
    created_backups,
    snapshot_backup,
)
from platform_regress.evidence.config_diff import (
    command_mutation_scope,
    parse_semantic_objects,
    semantic_config_diff,
    strip_inline_comment,
)
from platform_regress.execution.command import run_logged_command
from platform_regress.execution.ports import free_port_block, port_is_free
from platform_regress.reporting.model import ReportStep
from platform_regress.sdk import ReportRuntime, ReportSpec

from products.fbasecman.process import FbasecmanProcess, FbasecmanProcessError


class FbasecmanCaseRuntime(ReportRuntime):
    """fbasecman 回归用例运行时：进程 + 配置模板 + console/业务断言助手。"""

    # 所有 fbasecman 用例共享同一批后端数据库，必须串行执行，共用一把锁。
    lock_name = "fbasecman_cases"

    def __init__(self, root, case, env, context_data=None):
        self.env = env
        self.case = case
        if context_data is None:
            import yaml
            path = env.test_context_file
            context_data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
        spec = ReportSpec(case.name, case.target, case.summary,
                          getattr(case, "suite_name", None) or getattr(case, "suite_id", "suite"),
                          tuple(getattr(case, "source_sections", ())))
        super().__init__(Path(root), spec, case_dir=env.output_dir, lock_dir=env.runtime_dir,
                         context_data=context_data or {})
        self.proxy_log = self.run_root / "fbasecman.log"
        self._port_seed = 1
        self.listen_port = free_port_block(1, 3)
        self.read_port = self.listen_port + 1
        self.pid_file = self.workdir / "fbasecman.pid"
        self.active_conf = None
        self._crash_info = None
        # Every HA console mutation gets a before/after console snapshot.
        # daemon 以用例 workdir 为 cwd：core dump 落进用例目录随归档清理，
        # 不会污染 products/<product>/regression 源码树。
        self.process = FbasecmanProcess(
            self.env.config["fbasecman"]["fbasecman_bin"],
            self.env.config["local"]["postgres_dir"], self.listen_port,
            self.listen_port + 2, self.pid_file, self.workdir / "locks",
            self.proxy_log, self.logs_dir,
            partial(self.run_command, cwd=self.workdir), self.trace,
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
            self.listen_port = free_port_block(self._port_seed, 3)
            self.read_port = self.listen_port + 1
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
            self.env.config["local"]["postgres_dir"], os.environ.get("FBCMAN_LOCAL_HOST", "127.0.0.1"),
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
            details=[("intent", "action"),
                     ("结果分析", "保存%s的运行态快照；业务结论由对应验证步骤判定。" % phase)],
        )
        if result.returncode != 0:
            raise self.failure_class("HA state query failed: %s" % state_sql)
        return output

    # ------------------------------------------------------------------
    # fbasecman.conf 语义级 diff（忽略格式差异，只比对 group/datasources 字段）
    # ------------------------------------------------------------------

    _strip_inline_comment = staticmethod(strip_inline_comment)
    _semantic_objects = staticmethod(parse_semantic_objects)
    _command_scope = staticmethod(command_mutation_scope)

    def _semantic_config_diff(self, before, after, sql):
        """对比命令前后配置：只有 _command_scope 白名单内的变化才算预期。"""
        return semantic_config_diff(before, after, sql)

    # ------------------------------------------------------------------
    # psql 执行助手（console 管理面 / business 业务面 两个入口）
    # ------------------------------------------------------------------

    def psql(self, sql, title, expected, predicate):
        """执行一条 console 命令并断言输出；命令后自动做配置语义 diff 校验。"""
        self.last_ha_sql = sql.strip()
        mutating = bool(re.match(r"(?is)^\s*(SET|REFRESH|RELOAD|ALTER)\b", sql))
        if mutating:
            self._record_ha_state(sql, "命令前运行态")
        config_before = self.active_conf.read_text(encoding="utf-8") if self.active_conf else None
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"], os.environ.get("FBCMAN_LOCAL_HOST", "127.0.0.1"),
            self.listen_port, "admin", "console", sql,
        )
        retry_enabled = sql.lstrip().upper().startswith("SHOW ")

        def judge(result, output, attempt, elapsed):
            return (result.returncode == 0 and predicate(output),
                    "returncode=%s attempts=%s elapsed=%.2fs" % (
                        result.returncode, attempt, elapsed))

        output, result = self.asserted_command(
            command, title, expected, judge,
            retry_timeout=30 if retry_enabled else 0, log_stem="psql")
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
            elif mutating:
                self.record_step(
                    "%s：配置文件未发生变化" % title,
                    "读取活动配置文件",
                    "该命令应保持配置不变",
                    "配置字节完全一致",
                    "PASS",
                    details=[("结果分析", "命令执行后配置文件未发生非预期修改。"),
                             ("intent", "verify")],
                )
        if mutating:
            self._record_ha_state(sql, "命令后生效状态")
        return output

    def psql_monitor(self, sql, title, expected, predicate, retry_timeout=5):
        """Run monitor SHOW in expanded form so field assertions are exact."""
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"], os.environ.get("FBCMAN_LOCAL_HOST", "127.0.0.1"), self.listen_port,
            "admin", "console", sql, expanded=True,
        )

        def judge(result, output, attempt, elapsed):
            return (result.returncode == 0 and predicate(output),
                    "returncode=%s attempts=%s elapsed=%.2fs" % (
                        result.returncode, attempt, elapsed))

        return self.asserted_command(
            command, title, expected, judge, retry_timeout=retry_timeout,
            log_stem="psql_monitor",
            failure=lambda t, _a, r: "%s: returncode=%s" % (t, r.returncode),
        )[0]

    @staticmethod
    def _expanded_rows(output, key):
        """Parse psql expanded output into rows indexed by one text column."""
        return parse_expanded_rows(output, key)

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

    def assert_table(self, sql, title, expected_rows, key="node_name",
                     retry_timeout=15, row_count=None, absent=()):
        """Execute a console query and assert table rows with structured diffs."""
        from platform_regress.clients.psql import assert_table_rows

        expected_parts = [
            "%s [%s]" % (k, ", ".join("%s=%s" % (col, val) for col, val in v.items()))
            for k, v in expected_rows.items()
        ]
        if row_count is not None:
            expected_parts.append("行总数=%d" % row_count)
        expected_desc = "; ".join(expected_parts)
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"], os.environ.get("FBCMAN_LOCAL_HOST", "127.0.0.1"), self.listen_port,
            "admin", "console", sql,
        )

        def judge(result, output, attempt, elapsed):
            passed, summary, _ = assert_table_rows(
                output, expected_rows, key=key,
                row_count=row_count, absent=absent)
            return result.returncode == 0 and passed, summary

        return self.asserted_command(
            command, title, expected_desc, judge,
            retry_timeout=retry_timeout, interval=0.3, log_stem="assert_table",
            failure=lambda t, a, _r: "%s 失败:\n%s" % (t, a),
        )[0]

    # ------------------------------------------------------------------
    # fbasecman console SHOW 的领域断言（列结构是产品知识，故在本层）
    # ------------------------------------------------------------------

    def assert_nodes(self, title, expected_rows, retry_timeout=15,
                     row_count=None, absent=()):
        """SHOW NODES 结构化断言：按 node_name 校验各行字段。

        expected_rows 形如 {"pg_1": {"weight": "10", "config_status":
        "active", "effective_role": "PRIMARY", "current_primary": "pg_1"}}；
        字段名取 SHOW NODES 输出列（cluster_name/host/port/storage_db/
        weight/config_status/effective_role/current_primary/
        effective_status/unavailable_reason）。row_count 校验总行数，
        absent 校验列出的节点不存在于表中。"""
        return self.assert_table("SHOW NODES;", title, expected_rows,
                                 key="node_name", retry_timeout=retry_timeout,
                                 row_count=row_count, absent=absent)

    def assert_groups(self, title, expected_rows, retry_timeout=15,
                      row_count=None, absent=()):
        """SHOW GROUPS 结构化断言：按 group_name 校验 group_mode/
        write_cluster/promoted_cluster/access_mode/backend_clusters 等列。"""
        return self.assert_table("SHOW GROUPS;", title, expected_rows,
                                 key="group_name", retry_timeout=retry_timeout,
                                 row_count=row_count, absent=absent)

    def assert_members(self, group, title, expected_rows, retry_timeout=15,
                       row_count=None, absent=(), user=None):
        """SHOW GROUP_MEMBERS 中指定 group 行的结构化断言（按 node_name）。

        SHOW GROUP_MEMBERS 是 (group, user, node) 三元投影——同一节点会按
        group 的每个用户重复出现。user 给出时先按用户过滤，使每个节点在
        断言范围内唯一；row_count 校验的也是过滤后的行数。"""
        from platform_regress.clients.psql import parse_psql_table

        expected_parts = [
            "%s [%s]" % (k, ", ".join("%s=%s" % (c, v) for c, v in f.items()))
            for k, f in expected_rows.items()]
        if user is not None:
            expected_parts.append("user=%s" % user)
        if row_count is not None:
            expected_parts.append("成员行数=%d" % row_count)
        expected_desc = "; ".join(expected_parts)
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"],
            os.environ.get("FBCMAN_LOCAL_HOST", "127.0.0.1"), self.listen_port,
            "admin", "console", "SHOW GROUP_MEMBERS;")

        def judge(result, output, attempt, elapsed):
            rows = [row for row in parse_psql_table(output)
                    if row.get("group_name") == group and
                    (user is None or row.get("user") == user)]
            row_map = {row.get("node_name"): row for row in rows}
            errors, matches = [], []
            for key_val, fields in expected_rows.items():
                row = row_map.get(key_val)
                if row is None:
                    errors.append("%s 未在 %s 成员中出现" % (key_val, group))
                    continue
                for col, want in fields.items():
                    got = row.get(col)
                    if got == want:
                        matches.append("%s.%s=%s" % (key_val, col, got))
                    else:
                        errors.append("%s.%s: 期望=%s 实际=%s"
                                      % (key_val, col, want, got))
            if row_count is not None and len(rows) != row_count:
                errors.append("成员数=%d，期望 %d" % (len(rows), row_count))
            for key_val in absent:
                if key_val in row_map:
                    errors.append("%s 不应是 %s 成员" % (key_val, group))
            passed = result.returncode == 0 and not errors
            summary = "; ".join(matches + ["❌ " + e for e in errors])
            return passed, summary

        return self.asserted_command(
            command, title, expected_desc or "成员投影符合预期", judge,
            retry_timeout=retry_timeout, interval=0.3,
            log_stem="assert_members",
            failure=lambda t, a, _r: "%s 失败:\n%s" % (t, a))[0]

    def assert_routing(self, group, title, write_cluster=None,
                       write_leader=None, users=None, retry_timeout=15):
        """SHOW GROUP_ROUTING 的结构化断言。

        write_cluster/write_leader：每个用户的 WRITE 候选行都必须指向
        该 cluster/节点，且 is_write_target=true、route_status=AVAILABLE；
        非 WRITE 候选行不得是写目标。users 给出时校验投影只覆盖这些用户。"""
        from platform_regress.clients.psql import parse_psql_table

        command = build_psql_command(
            self.env.config["local"]["postgres_dir"],
            os.environ.get("FBCMAN_LOCAL_HOST", "127.0.0.1"), self.listen_port,
            "admin", "console", "SHOW GROUP_ROUTING %s;" % group)

        def judge(result, output, attempt, elapsed):
            rows = [row for row in parse_psql_table(output)
                    if row.get("group_name") == group]
            errors = []
            if not rows:
                errors.append("无 %s 的路由投影行" % group)
            if users is not None:
                found = {row.get("user_name") for row in rows}
                missing = [u for u in users if u not in found]
                if missing:
                    errors.append("用户投影缺失: %s" % ",".join(missing))
            write_rows = [r for r in rows if r.get("candidate_type") == "WRITE"]
            if write_leader is not None or write_cluster is not None:
                if not write_rows:
                    errors.append("无 WRITE 候选行")
                for row in write_rows:
                    if write_cluster is not None and \
                            row.get("cluster_name") != write_cluster:
                        errors.append("WRITE 候选 cluster=%s，期望 %s"
                                      % (row.get("cluster_name"), write_cluster))
                    if write_leader is not None and \
                            row.get("candidate_node") != write_leader:
                        errors.append("WRITE 候选节点=%s，期望 %s"
                                      % (row.get("candidate_node"), write_leader))
                    if row.get("is_write_target") != "true":
                        errors.append("%s is_write_target=%s" % (
                            row.get("candidate_node"),
                            row.get("is_write_target")))
                    if row.get("route_status") != "AVAILABLE":
                        errors.append("%s route_status=%s" % (
                            row.get("candidate_node"),
                            row.get("route_status")))
            for row in rows:
                if row.get("candidate_type") != "WRITE" and \
                        row.get("is_write_target") == "true":
                    errors.append("非 WRITE 候选 %s 却是写目标"
                                  % row.get("candidate_node"))
            passed = result.returncode == 0 and not errors
            writes = {(r.get("candidate_node"), r.get("cluster_name"))
                      for r in write_rows}
            summary = "WRITE候选=%s；READ候选=%d行" % (
                sorted(writes) or "无",
                len(rows) - len(write_rows))
            if errors:
                summary += "；❌ " + "; ".join(errors)
            return passed, summary

        expected = []
        if write_cluster or write_leader:
            expected.append("WRITE 候选=%s@%s is_write_target=true "
                            "route_status=AVAILABLE" % (
                                write_leader or "*", write_cluster or "*"))
        if users is not None:
            expected.append("用户投影=%s" % ",".join(users))
        return self.asserted_command(
            command, title, "；".join(expected) or "路由投影有效", judge,
            retry_timeout=retry_timeout, interval=0.3,
            log_stem="assert_routing",
            failure=lambda t, a, _r: "%s 失败:\n%s" % (t, a))[0]

    def assert_business_route(self, sql, title, port=None, ports=None,
                              recovery=None, group="mmr_group",
                              user="postgres", frontend_port=None,
                              retry_timeout=0):
        """业务路由断言：解析输出表中 inet_server_port()/pg_is_in_recovery()
        的**列值**精确匹配，替代 ``str(port) in output`` 的弱断言。

        port 校验唯一后端端口；ports 校验端口属于给定候选集合
        （读路由在多个候选间分发时使用）；frontend_port 指定连接进
        fbasecman 的前端端口（默认 listen_port，port 分流方法用
        read_port）。"""
        from platform_regress.clients.psql import parse_psql_table

        command = build_psql_command(
            self.env.config["local"]["postgres_dir"],
            os.environ.get("FBCMAN_LOCAL_HOST", "127.0.0.1"),
            frontend_port or self.listen_port, user, group, sql)

        def judge(result, output, attempt, elapsed):
            rows = parse_psql_table(output)
            errors = []
            if not rows:
                errors.append("未解析到结果行")
                row = {}
            else:
                row = rows[-1]
            if port is not None:
                got = row.get("inet_server_port")
                if got != str(port):
                    errors.append("inet_server_port=%s，期望 %s" % (got, port))
            if ports is not None:
                valid = {str(p) for p in ports}
                got = row.get("inet_server_port")
                if got not in valid:
                    errors.append("inet_server_port=%s，期望属于 %s"
                                  % (got, sorted(valid)))
            if recovery is not None:
                want = "t" if recovery else "f"
                got = row.get("pg_is_in_recovery")
                if got != want:
                    errors.append("pg_is_in_recovery=%s，期望 %s" % (got, want))
            passed = result.returncode == 0 and not errors
            summary = "命中行=%s" % (row or "<无>") + \
                ("；❌ " + "; ".join(errors) if errors else "")
            return passed, summary

        expected = []
        if port is not None:
            expected.append("inet_server_port=%s" % port)
        if ports is not None:
            expected.append("inet_server_port∈%s"
                            % sorted(str(p) for p in ports))
        if recovery is not None:
            expected.append("pg_is_in_recovery=%s" % ("t" if recovery else "f"))
        return self.asserted_command(
            command, title, "；".join(expected) or "业务路由有效", judge,
            retry_timeout=retry_timeout, log_stem="assert_business_route",
            failure=lambda t, a, _r: "%s 失败:\n%s" % (t, a))[0]

    def psql_business(self, sql, title, expected, predicate, group="mmr_group",
                      port=None, user="postgres", retry_timeout=0):
        """Run a query through the configured MMR business route."""
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"], os.environ.get("FBCMAN_LOCAL_HOST", "127.0.0.1"),
            port or self.listen_port, user, group, sql,
        )

        def judge(result, output, attempt, elapsed):
            return (result.returncode == 0 and predicate(output),
                    "returncode=%s attempts=%s" % (result.returncode, attempt))

        return self.asserted_command(
            command, title, expected, judge, retry_timeout=retry_timeout,
            log_stem="psql_business")[0]

    def psql_business_error(self, sql, title, expected, predicate,
                            group="mmr_group", port=None, user="postgres"):
        """执行业务 SQL 并断言它按预期失败（rc != 0 且输出匹配 predicate）。"""
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"], os.environ.get("FBCMAN_LOCAL_HOST", "127.0.0.1"),
            port or self.listen_port, user, group, sql,
        )

        def judge(result, output, attempt, elapsed):
            return (result.returncode != 0 and predicate(output),
                    "returncode=%s" % result.returncode)

        return self.asserted_command(
            command, title, expected, judge, log_stem="psql_business_error")[0]

    def psql_error(self, sql, title, expected, predicate, compare_config=True):
        """执行 console 命令并断言被拒绝；可选校验配置未被污染。"""
        config_before = self.active_conf.read_bytes() if self.active_conf else None
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"], os.environ.get("FBCMAN_LOCAL_HOST", "127.0.0.1"),
            self.listen_port, "admin", "console", sql,
        )

        def judge(result, output, attempt, elapsed):
            return (result.returncode != 0 and predicate(output),
                    "returncode=%s" % result.returncode)

        output, result = self.asserted_command(
            command, title, expected, judge, log_stem="psql_error")
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
        result = self.file_diff(before, after, "config_diff.log")
        output = result.output.rstrip() or "<no differences>"
        self.record_step("检查配置文件 diff（与测试开始时初始配置比较）", result.command,
                         "与测试开始时保存的初始配置无差异", output,
                         "PASS" if result.returncode == 0 else "FAIL")
        if result.returncode != 0:
            raise self.failure_class("configuration diff is not empty: %s" % output)

    def diff_contains(self, before, after, expected, title):
        """断言配置 diff 恰好包含 expected 中列出的变化内容。"""
        result = self.file_diff(
            before, after, "config_diff_%02d.log" % (self._step_order + 1))
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
        return backup_dir_path(config_path, backup_dir)

    def backup_checkpoint(self, config_path, backup_dir=None):
        """记录备份目录现状快照，供命令执行后对比新增备份文件。"""
        return snapshot_backup(config_path, backup_dir)

    def assert_backup_created(self, checkpoint, config_path, title="验证配置备份文件"):
        """断言恰好新增一个 .bak 备份且内容与命令前配置逐字节一致。"""
        backup_dir = checkpoint.backup_dir or backup_dir_path(config_path, None)
        created, current = created_backups(checkpoint, backup_dir)
        content_matches = len(created) == 1 and backup_content_matches(
            checkpoint, backup_dir, created[0])
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
        backup_dir = checkpoint.backup_dir or backup_dir_path(config_path, None)
        created, current = created_backups(checkpoint, backup_dir)
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
        binary_label = "fbasecman"
        config_label = "$CASE_DIR/%s.conf" % self.case.name
        return [
            "测试拓扑: %s" % getattr(self.case, "topology", "-"),
            "控制台命令端口: %s" % self.listen_port,
            "手动启动命令: %s %s --console --log_to_stdout" % (binary_label, config_label),
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
