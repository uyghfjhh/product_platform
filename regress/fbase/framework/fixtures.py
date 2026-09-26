import errno
import re
import shutil
import socket
import time
from pathlib import Path

from framework.errors import ConfigError, OperationError, SafetyError


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_GUC_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def _identifier(value):
    if not _IDENTIFIER.match(value or ""):
        raise ConfigError("fixture 中包含非法 PostgreSQL 标识符: %s" % value)
    return '"%s"' % value.replace('"', '""')


def _literal(value):
    return "'%s'" % str(value).replace("'", "''")


class FixtureRegistry(object):
    def __init__(self):
        self._fixtures = {}

    def register(self, name):
        def register(callback):
            self._fixtures[name] = callback
            return callback
        return register

    @property
    def names(self):
        return set(self._fixtures)

    def setup_all(self, context, specs):
        for spec in specs:
            expanded = (context.expand(spec) if hasattr(context, "expand") else spec)
            options = {} if isinstance(expanded, str) else dict(expanded)
            name = expanded if isinstance(expanded, str) else options.pop("type", None)
            callback = self._fixtures.get(name)
            if not callback:
                raise ConfigError("未知 fixture: %s" % name)
            callback(context, options)


FIXTURES = FixtureRegistry()


@FIXTURES.register("cluster")
def _cluster(unused_context, unused_options):
    return None


@FIXTURES.register("node_running_guard")
def _node_running_guard(context, options):
    """Ensure a deliberately stopped member is brought back before reporting."""
    node = context.resolve_node(options.get("node", "primary"))
    status = context.manager._pg_ctl(node, "status", check=False)
    if status.returncode != 0:
        raise SafetyError("%s 初始未运行，拒绝执行成员连接失败场景" % node)

    def cleanup():
        node_config = context.manager.node(node)
        ready = context.command_runner.run(
            [context.manager.binary("pg_isready"), "-h", node_config["host"],
             "-p", str(node_config["port"]), "-d", "postgres", "-U", "postgres"],
            check=False, timeout=10)
        if ready.returncode != 0:
            context.manager._pg_ctl(node, "stop_immediate", check=False)
            context.manager._pg_ctl(node, "start")

    context.add_cleanup("恢复成员 %s 为可连接运行状态" % node, cleanup, priority=250)


@FIXTURES.register("faketime_node_guard")
def _faketime_node_guard(context, options):
    """Restore a member under the real host clock after a faketime start."""
    node = context.resolve_node(options.get("node", "primary"))
    if context.command_runner.run(["sh", "-c", "command -v faketime"],
                                  check=False).returncode != 0:
        raise SafetyError("缺少 faketime，无法在同机成员间制造可控时差")
    if context.manager._pg_ctl(node, "status", check=False).returncode != 0:
        raise SafetyError("%s 初始未运行，拒绝修改其进程时间" % node)

    def cleanup():
        status = context.manager._pg_ctl(node, "status", check=False)
        data_dir = Path(context.manager.node(node)["data_dir"])
        fake_clock = False
        pid_file = data_dir / "postmaster.pid"
        if status.returncode == 0 and pid_file.exists():
            try:
                pid = pid_file.read_text(encoding="utf-8").splitlines()[0]
                environment = Path("/proc") / pid / "environ"
                fake_clock = b"FAKETIME=" in environment.read_bytes()
            except (OSError, IndexError):
                # A failed case must favor a known-good normal restart.
                fake_clock = True
        if status.returncode != 0 or fake_clock:
            context.manager._pg_ctl(node, "stop", check=False)
            context.manager._pg_ctl(node, "start")

    context.add_cleanup("以真实主机时钟重启成员 %s" % node, cleanup, priority=250)


@FIXTURES.register("mmr_node_source_guard")
def _mmr_node_source_guard(context, options):
    """Restore the one metadata source_node_id changed by a document scenario."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    node_id = options.get("node_id")
    if not isinstance(node_id, int) or node_id <= 0:
        raise ConfigError("mmr_node_source_guard 的 node_id 必须为正整数")
    original = context.postgres.scalar(
        node, database,
        "SELECT source_node_id::text FROM fdd.mmr_node WHERE node_id=%s" % node_id)
    if original is None:
        raise SafetyError("未找到 node_id=%s 的 MMR 节点元数据" % node_id)

    def cleanup():
        context.postgres.execute_checked(
            node, "postgres", database,
            "UPDATE fdd.mmr_node SET source_node_id=%s WHERE node_id=%s" %
            (original, node_id))

    context.add_cleanup("恢复 MMR 节点 %s 的 source_node_id" % node_id, cleanup,
                        priority=150)


@FIXTURES.register("mmr_node_state_guard")
def _mmr_node_state_guard(context, options):
    """Capture one MMR node state and restore it even when a case aborts."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    node_id = options.get("node_id")
    expected_state = options.get("expected_state", "ACTIVE")
    if not isinstance(node_id, int) or node_id <= 0:
        raise ConfigError("mmr_node_state_guard 的 node_id 必须为正整数")
    if expected_state not in (
            "NONE", "CREATED", "JOIN_START", "DATASYNC", "CATCHUP", "ACTIVE",
            "PART_START", "PARTING", "PART_CATCHUP", "PARTED"):
        raise ConfigError("mmr_node_state_guard 的 expected_state 非法: %s" %
                          expected_state)

    state = context.postgres.scalar(
        node, database,
        "SELECT node_state::text FROM fdd.mmr_node WHERE node_id = %s" % node_id)
    if state != expected_state:
        raise SafetyError(
            "MMR 节点 %s 初始状态为 %s，预期为 %s，拒绝修改元数据" %
            (node_id, state, expected_state))

    def cleanup():
        context.postgres.execute_checked(
            node, "postgres", database,
            "UPDATE fdd.mmr_node SET node_state = %s::fdd.mmr_node_state "
            "WHERE node_id = %s" % (_literal(state), node_id))

    # Register before any case step can alter the row, so an assertion failure
    # cannot leave the shared MMR cluster in an inconsistent state.
    context.add_cleanup("恢复 MMR 节点 %s 的元数据状态" % node_id, cleanup,
                        priority=150)


@FIXTURES.register("mmr_check_node_conf_empty")
def _mmr_check_node_conf_empty(context, options):
    """Reserve empty per-node check configuration for one MMR case."""
    selectors = options.get("nodes") or []
    if not selectors:
        raise ConfigError("mmr_check_node_conf_empty 必须声明 nodes")
    database = options.get("database", "postgres")
    nodes = []
    for selector in selectors:
        node = context.resolve_node(selector)
        if node not in nodes:
            nodes.append(node)
    for node in nodes:
        count = context.postgres.scalar(
            node, database, "SELECT count(*) FROM fdd.mmr_check_node_conf")
        if count != "0":
            raise SafetyError(
                "%s 的 fdd.mmr_check_node_conf 非空，拒绝覆盖已有集群校验配置" %
                node)

    def cleanup():
        errors = []
        for node in nodes:
            try:
                context.postgres.execute_checked(
                    node, "postgres", database,
                    "TRUNCATE TABLE fdd.mmr_check_node_conf")
            except Exception as exc:
                errors.append("%s: %s" % (node, exc))
        if errors:
            raise OperationError("; ".join(errors))

    # All nodes are proven empty above. TRUNCATE restores that exact starting
    # state; DELETE is rejected here because the table has no replica identity
    # while the MMR publication includes all tables.
    context.add_cleanup("清空本用例的 MMR 集群校验配置", cleanup, priority=150)


@FIXTURES.register("mmr_node_failover_guard")
def _mmr_node_failover_guard(context, options):
    """Preserve one locally stored MMR failover flag through a case."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    node_name = options.get("node_name")
    expected_state = options.get("expected_state", "true")
    if not _IDENTIFIER.match(node_name or ""):
        raise ConfigError("mmr_node_failover_guard 的 node_name 非法: %s" % node_name)
    if expected_state not in ("true", "false"):
        raise ConfigError("mmr_node_failover_guard 的 expected_state 必须为 true 或 false")
    state = context.postgres.scalar(
        node, database,
        "SELECT failover::text FROM fdd.mmr_node WHERE node_name = %s" %
        _literal(node_name))
    if state != expected_state:
        raise SafetyError(
            "MMR 节点 %s 的初始 failover=%s，预期为 %s，拒绝修改" %
            (node_name, state, expected_state))

    def cleanup():
        context.postgres.execute_checked(
            node, "postgres", database,
            "SELECT fdd.alter_node_failover(%s, %s, false)" %
            (_literal(node_name), state))

    context.add_cleanup("恢复 MMR 节点 %s 的 failover" % node_name, cleanup,
                        priority=150)


@FIXTURES.register("mmr_global_failover_guard")
def _mmr_global_failover_guard(context, options):
    """Preserve a target node's failover flag on every MMR member."""
    controller = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    node_name = options.get("node_name")
    selectors = options.get("nodes") or []
    expected_state = options.get("expected_state", "true")
    if not _IDENTIFIER.match(node_name or ""):
        raise ConfigError("mmr_global_failover_guard 的 node_name 非法: %s" % node_name)
    if not selectors:
        raise ConfigError("mmr_global_failover_guard 必须声明 nodes")
    if expected_state not in ("true", "false"):
        raise ConfigError("mmr_global_failover_guard 的 expected_state 必须为 true 或 false")
    nodes = []
    for selector in selectors:
        node = context.resolve_node(selector)
        if node not in nodes:
            nodes.append(node)
    for node in nodes:
        state = context.postgres.scalar(
            node, database,
            "SELECT failover::text FROM fdd.mmr_node WHERE node_name = %s" %
            _literal(node_name))
        if state != expected_state:
            raise SafetyError(
                "%s 中 MMR 节点 %s 的初始 failover=%s，预期为 %s，拒绝修改" %
                (node, node_name, state, expected_state))

    def cleanup():
        context.postgres.execute_checked(
            controller, "postgres", database,
            "SELECT fdd.alter_node_failover(%s, %s, true)" %
            (_literal(node_name), expected_state))

    context.add_cleanup("全局恢复 MMR 节点 %s 的 failover" % node_name, cleanup,
                        priority=150)


@FIXTURES.register("mmr_streaming_parallel_guard")
def _mmr_streaming_parallel_guard(context, options):
    """Reserve an all-parallel MMR streaming topology and restore it by UDF."""
    controller = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    selectors = options.get("nodes") or []
    node_name = options.get("node_name")
    if not _IDENTIFIER.match(node_name or ""):
        raise ConfigError("mmr_parallel_guard 的 node_name 非法: %s" %
                          node_name)
    if not selectors:
        raise ConfigError("mmr_parallel_guard 必须声明 nodes")
    nodes = []
    for selector in selectors:
        node = context.resolve_node(selector)
        if node not in nodes:
            nodes.append(node)
    for node in nodes:
        row = context.postgres.scalar(
            node, database,
            "SELECT count(*)::text || '|' || bool_and(streaming = 'p')::text "
            "FROM fdd.mmr_node")
        expected = "%s|true" % len(nodes)
        if row != expected:
            raise SafetyError(
                "%s 的 MMR streaming 初始状态为 %s，预期为 %s；拒绝覆盖已有配置" %
                (node, row, expected))

    def cleanup():
        context.postgres.execute_checked(
            controller, "postgres", database,
            "SELECT fdd.alter_node_info('streaming', %s, 'parallel', true)" %
            _literal(node_name))
        # The UDF restores metadata and substream, but an apply worker stopped
        # for streaming=off can remain disconnected.  Cycling only fmmr_
        # subscriptions uses PostgreSQL's supported worker restart path.
        for node in nodes:
            context.postgres.execute_checked(
                node, "postgres", database,
                "DO $$ DECLARE item record; BEGIN "
                "FOR item IN SELECT subname FROM pg_subscription "
                "WHERE subname LIKE 'fmmr_%%' LOOP "
                "EXECUTE format('ALTER SUBSCRIPTION %I DISABLE', item.subname); "
                "EXECUTE format('ALTER SUBSCRIPTION %I ENABLE', item.subname); "
                "END LOOP; END $$")
        # alter_node_info returns before the restarted apply workers have
        # reconnected and their fddoutput slots become active again.  Do not
        # let the next shared-MMR case observe that transient state.
        deadline = time.time() + 30
        last = ""
        while time.time() < deadline:
            last = context.postgres.scalar(
                controller, database,
                "SELECT count(*)::text FROM fdd.show_node_info(true,false) "
                "WHERE is_abnormal <> 'OK'")
            if last == "0":
                return
            time.sleep(1)
        raise OperationError("恢复 MMR streaming=parallel 后 30 秒仍未健康，异常数=%s" % last)

    # The UDF changes both fdd.mmr_node and every affected pg_subscription.
    # Replaying it restores the exact all-parallel state reserved above.
    context.add_cleanup("全局恢复 MMR streaming=parallel", cleanup, priority=150)


@FIXTURES.register("mmr_streaming_mode")
def _mmr_streaming_mode(context, options):
    """Temporarily set every member to one documented streaming mode."""
    controller = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    node_name = options.get("node_name")
    mode = options.get("mode")
    if not _IDENTIFIER.match(node_name or ""):
        raise ConfigError("mmr_mode 的 node_name 非法: %s" % node_name)
    if mode not in ("off", "on", "parallel"):
        raise ConfigError("mmr_mode 的 mode 必须为 off/on/parallel")
    original = context.postgres.scalar(
        controller, database,
        "SELECT CASE WHEN count(DISTINCT streaming) = 1 THEN "
        "CASE min(streaming) WHEN 'f' THEN 'off' WHEN 't' THEN 'on' "
        "WHEN 'p' THEN 'parallel' END ELSE '' END FROM fdd.mmr_node")
    if original not in ("off", "on", "parallel"):
        raise SafetyError("多活成员 streaming 初始值不一致，拒绝覆盖: %s" % original)

    def cleanup():
        context.postgres.execute_checked(
            controller, "postgres", database,
            "SELECT fdd.alter_node_info('streaming', %s, %s, true)" %
            (_literal(node_name), _literal(original)))

    context.add_cleanup("恢复 MMR streaming=%s" % original, cleanup, priority=150)
    context.postgres.execute_checked(
        controller, "postgres", database,
        "SELECT fdd.alter_node_info('streaming', %s, %s, true)" %
        (_literal(node_name), _literal(mode)))


@FIXTURES.register("mmr_subscriptions_enabled_guard")
def _mmr_subscriptions_enabled_guard(context, options):
    """Reserve enabled local MMR subscriptions and restore them through the UDF."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    expected_count = options.get("expected_count", 2)
    if not isinstance(expected_count, int) or expected_count <= 0:
        raise ConfigError("mmr_subscriptions_enabled_guard 的 expected_count 必须为正整数")
    metadata = context.postgres.scalar(
        node, database,
        "SELECT count(*)::text || '|' || bool_and(sub_enabled)::text "
        "FROM fdd.mmr_subscription WHERE sub_name LIKE 'fmmr_%%'")
    runtime = context.postgres.scalar(
        node, database,
        "SELECT count(*)::text || '|' || bool_and(subenabled)::text "
        "FROM pg_subscription WHERE subname LIKE 'fmmr_%%'")
    expected = "%s|true" % expected_count
    if metadata != expected or runtime != expected:
        raise SafetyError(
            "%s 的多活订阅初始状态不全为 enabled（元数据=%s，运行时=%s，预期=%s）" %
            (node, metadata, runtime, expected))

    def cleanup():
        context.postgres.execute_checked(
            node, "postgres", database, "SELECT fdd.alter_subscription_enable()")

    context.add_cleanup("恢复本节点全部 MMR 订阅为 enabled", cleanup, priority=150)


@FIXTURES.register("mmr_replication_sets_empty")
def _mmr_replication_sets_empty(context, options):
    """Reserve run-scoped replication-set names and drop partial creations."""
    controller = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    names = options.get("names") or []
    if not names or any(not _IDENTIFIER.match(name or "") for name in names):
        raise ConfigError("mmr_replication_sets_empty 必须声明合法 names")
    for name in names:
        count = context.postgres.scalar(
            controller, database,
            "SELECT count(*) FROM fdd.mmr_replication_set WHERE set_name = %s" %
            _literal(name))
        if count != "0":
            raise SafetyError("复制集 %s 已存在，拒绝覆盖" % name)

    def cleanup():
        errors = []
        for name in reversed(names):
            try:
                exists = context.postgres.scalar(
                    controller, database,
                    "SELECT count(*) FROM fdd.mmr_replication_set WHERE set_name = %s" %
                    _literal(name))
                if exists != "0":
                    context.postgres.execute_checked(
                        controller, "postgres", database,
                        "SELECT fdd.drop_replication_set(%s)" % _literal(name))
            except Exception as exc:
                errors.append("%s: %s" % (name, exc))
        if errors:
            raise OperationError("; ".join(errors))

    context.add_cleanup("删除多活复制集测试对象", cleanup, priority=150)


@FIXTURES.register("mmr_replication_set_table_bindings")
def _mmr_replication_set_table_bindings(context, options):
    """Remove run-scoped table bindings before subscription/set cleanup."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    set_name = options.get("set_name")
    session_tables = [
        "immediate_parallel_conflict",
        "immediate_parallel_target_column",
        "immediate_parallel_source_column",
    ]
    tables = list(dict.fromkeys(session_tables + (options.get("tables") or [])))
    if not _IDENTIFIER.match(set_name or ""):
        raise ConfigError("mmr_replication_set_table_bindings 的 set_name 非法")
    if not tables or any(not _IDENTIFIER.match(name or "") for name in tables):
        raise ConfigError("mmr_replication_set_table_bindings 必须声明合法 tables")

    def cleanup():
        errors = []
        for table in reversed(tables):
            try:
                set_exists = context.postgres.scalar(
                    node, database,
                    "SELECT EXISTS (SELECT 1 FROM fdd.mmr_replication_set "
                    "WHERE set_name=%s)::text" % _literal(set_name))
                table_exists = context.postgres.scalar(
                    node, database,
                    "SELECT (to_regclass('public.%s') IS NOT NULL)::text" % table)
                if set_exists == "true" and table_exists == "true":
                    # The product stores the initiator's relation OID in
                    # remote metadata.  Do not use a local-OID metadata probe
                    # here: it can incorrectly say the binding is absent.
                    context.postgres.execute_checked(
                        node, "postgres", database,
                        "SELECT fdd.replication_set_remove_table('public.%s'::regclass,%s,true)" %
                        (_identifier(table), _literal(set_name)))
            except Exception as exc:
                errors.append("%s: %s" % (table, exc))
        if errors:
            raise OperationError("; ".join(errors))

    # Remove publication bindings before the subscription guard (200), the
    # replication-set fixture (150), and the table fixture (100).
    context.add_cleanup("移除多活复制集测试表绑定", cleanup, priority=250)


@FIXTURES.register("mmr_sub_repsets_guard")
def _mmr_sub_repsets_guard(context, options):
    """Preserve a node's replication-set subscription list through the UDF."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    original = context.postgres.scalar(
        node, database,
        "SELECT quote_literal(sub_repsets::text) FROM fdd.mmr_local_node")
    if not original:
        raise SafetyError("%s 的 sub_repsets 为空，拒绝覆盖订阅复制集配置" % node)

    def cleanup():
        context.postgres.execute_checked(
            node, "postgres", database,
            "SELECT fdd.alter_node_replication_sets(%s::text[])" % original)
        context.postgres.execute_checked(
            node, "postgres", database,
            "SELECT fdd.check_and_adjust_sub_repsets(%s::text[])" % original)

    # Restore before run-scoped replication sets are removed.  The adjustment
    # UDF updates pg_subscription without the default-repset copy-data path.
    context.add_cleanup("恢复本节点订阅复制集", cleanup, priority=200)


@FIXTURES.register("mmr_two_phase_disabled")
def _mmr_two_phase_disabled(context, options):
    """Require the document's non-two-phase MMR topology before table sync."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    enabled = context.postgres.scalar(
        node, database,
        "SELECT count(*) FROM fdd.mmr_node WHERE two_phase")
    if enabled != "0":
        raise SafetyError(
            "当前多活集群存在 %s 个 two_phase 节点；5.2 测试一的 "
            "replication_set_async_execute 默认复制集路径要求 two_phase=false" % enabled)


@FIXTURES.register("mmr_async_set_mode_recovery")
def _mmr_async_set_mode_recovery(context, options):
    """Use the product UDF to settle set_mode after a table-operation failure."""
    selectors = options.get("nodes") or []
    if not selectors:
        raise ConfigError("mmr_async_set_mode_recovery 必须声明 nodes")
    database = options.get("database", "postgres")
    nodes = []
    for selector in selectors:
        node = context.resolve_node(selector)
        if node not in nodes:
            nodes.append(node)

    def cleanup():
        errors = []
        for node in nodes:
            try:
                context.postgres.execute_checked(
                    node, "postgres", database, "SELECT fdd.check_async_record()")
            except Exception as exc:
                errors.append("%s: %s" % (node, exc))
        if errors:
            raise OperationError("; ".join(errors))

    # Run after table/configuration cleanups so the UDF sees no unfinished
    # asynchronous table records and can put local set_mode back to 'd'.
    context.add_cleanup("恢复 MMR 异步复制集状态", cleanup, priority=-100)


@FIXTURES.register("mmr_remote_sql_tables")
def _mmr_remote_sql_tables(context, options):
    """Reserve and remove run-scoped tables used by the remote SQL UDF case."""
    selectors = options.get("nodes") or []
    table_names = options.get("tables") or []
    database = options.get("database", "postgres")
    if not selectors:
        raise ConfigError("mmr_remote_sql_tables 必须声明 nodes")
    if not table_names:
        raise ConfigError("mmr_remote_sql_tables 必须声明 tables")
    tables = [(name, _identifier(name)) for name in table_names]
    nodes = []
    for selector in selectors:
        node = context.resolve_node(selector)
        if node not in nodes:
            nodes.append(node)
    for node in nodes:
        for name, unused_quoted_name in tables:
            exists = context.postgres.scalar(
                node, database,
                "SELECT (to_regclass(%s) IS NOT NULL)::text" %
                _literal("public.%s" % name))
            if exists != "false":
                raise SafetyError(
                    "%s 上测试表 public.%s 已存在，拒绝覆盖" % (node, name))

    def cleanup():
        errors = []
        for node in nodes:
            for name, quoted_name in tables:
                try:
                    context.postgres.execute_checked(
                        node, "postgres", database,
                        "DROP TABLE IF EXISTS public.%s" % quoted_name)
                except Exception as exc:
                    errors.append("%s.%s: %s" % (node, name, exc))
        if errors:
            raise OperationError("; ".join(errors))

    # Replication-set cleanup has priority 150.  Tables bound to a set cannot
    # be dropped until that product metadata is removed, including when a
    # case fails before its explicit teardown steps.
    context.add_cleanup("删除远程 SQL 用例测试表", cleanup, priority=100)


@FIXTURES.register("mmr_global_sequence_probe")
def _mmr_global_sequence_probe(context, options):
    """Own one run-scoped global sequence and restore all members on cleanup."""
    controller = context.resolve_node(options.get("node", "primary"))
    selectors = options.get("nodes") or []
    name = options.get("name")
    schema = options.get("schema", "public")
    database = options.get("database", "postgres")
    if not name:
        raise ConfigError("mmr_global_sequence_probe 缺少 name")
    if not selectors:
        raise ConfigError("mmr_global_sequence_probe 必须声明 nodes")
    quoted_name = _identifier(name)
    quoted_schema = schema if schema == "public" else _identifier(schema)
    regclass_name = "%s.%s" % (schema, name)
    nodes = []
    for selector in selectors:
        node = context.resolve_node(selector)
        if node not in nodes:
            nodes.append(node)
    for node in nodes:
        exists = context.postgres.scalar(
            node, database, "SELECT (to_regclass(%s) IS NOT NULL)::text" %
            _literal(regclass_name))
        if exists != "false":
            raise SafetyError(
                "%s 上测试序列 %s 已存在，拒绝覆盖" % (node, regclass_name))

    def cleanup():
        errors = []
        try:
            sequence_exists = [context.postgres.scalar(
                node, database,
                "SELECT (to_regclass(%s) IS NOT NULL)::text" % _literal(regclass_name))
                for node in nodes]
            if all(value == "true" for value in sequence_exists):
                metadata_exists = context.postgres.scalar(
                    controller, database,
                    "SELECT EXISTS (SELECT 1 FROM fdd.mmr_global_sequence "
                    "WHERE seq_name = %s::regclass)::text" %
                    _literal(regclass_name))
                if metadata_exists == "true":
                    context.postgres.execute_checked(
                        controller, "postgres", database,
                        "SELECT fdd.delete_global_seq(%s::regclass, true, true)" %
                        _literal(regclass_name))
            else:
                # A metadata-cleanup case deliberately removes a sequence on
                # one member.  The global delete UDF requires every member's
                # regclass to exist, so remove the remaining probes first and
                # use the product cleanup UDF locally on every member.
                for node in nodes:
                    context.postgres.execute_checked(
                        node, "postgres", database,
                        "DROP SEQUENCE IF EXISTS %s.%s" % (quoted_schema, quoted_name))
                    context.postgres.execute_checked(
                        node, "postgres", database,
                        "SELECT fdd.clean_invalid_global_seq()")
        except Exception as exc:
            errors.append("删除全局序列元数据: %s" % exc)
        for node in nodes:
            try:
                context.postgres.execute_checked(
                    node, "postgres", database,
                    "DROP SEQUENCE IF EXISTS %s.%s" % (quoted_schema, quoted_name))
            except Exception as exc:
                errors.append("%s.%s: %s" % (node, name, exc))
        if errors:
            raise OperationError("; ".join(errors))

    context.add_cleanup("删除全局序列测试对象 %s" % name, cleanup, priority=150)


@FIXTURES.register("mmr_global_sequences_empty")
def _mmr_global_sequences_empty(context, options):
    """Prevent a document-required all-sequence operation touching user data."""
    selectors = options.get("nodes") or []
    database = options.get("database", "postgres")
    if not selectors:
        raise ConfigError("mmr_global_sequences_empty 必须声明 nodes")
    nodes = []
    for selector in selectors:
        node = context.resolve_node(selector)
        if node not in nodes:
            nodes.append(node)
    for node in nodes:
        count = context.postgres.scalar(
            node, database, "SELECT count(*)::text FROM fdd.mmr_global_sequence")
        if count != "0":
            raise SafetyError(
                "%s 存在 %s 条全局序列元数据，拒绝执行 refresh_global_seq(NULL)" %
                (node, count))


@FIXTURES.register("mmr_schemas_empty")
def _mmr_schemas_empty(context, options):
    """Reserve disposable schemas on all MMR members and remove them on exit."""
    selectors = options.get("nodes") or []
    schemas = options.get("schemas") or []
    database = options.get("database", "postgres")
    if not selectors or not schemas:
        raise ConfigError("mmr_schemas_empty 必须声明 nodes 和 schemas")
    quoted_schemas = [(schema, _identifier(schema)) for schema in schemas]
    nodes = []
    for selector in selectors:
        node = context.resolve_node(selector)
        if node not in nodes:
            nodes.append(node)
    for node in nodes:
        for schema, unused_quoted in quoted_schemas:
            exists = context.postgres.scalar(
                node, database,
                "SELECT (to_regnamespace(%s) IS NOT NULL)::text" % _literal(schema))
            if exists != "false":
                raise SafetyError("%s 上测试 schema %s 已存在，拒绝覆盖" % (node, schema))

    def cleanup():
        errors = []
        for node in nodes:
            for schema, quoted_schema in quoted_schemas:
                try:
                    context.postgres.execute_checked(
                        node, "postgres", database,
                        "DROP SCHEMA IF EXISTS %s CASCADE" % quoted_schema)
                except Exception as exc:
                    errors.append("%s.%s: %s" % (node, schema, exc))
        if errors:
            raise OperationError("; ".join(errors))

    # Global-sequence cleanup runs first (priority 150), before schemas are
    # dropped, so regclass resolution remains valid on every failure path.
    context.add_cleanup("删除多活序列测试 schema", cleanup, priority=100)


@FIXTURES.register("system_clock")
def _system_clock(context, unused_options):
    """Save the host clock and restore it if a case advances system time."""
    now = context.command_runner.run(["date", "+%s"]).stdout.strip()
    ntp = context.command_runner.run(
        ["timedatectl", "show", "-p", "NTP", "--value"], check=False).stdout.strip()
    if not now.isdigit():
        raise OperationError("无法读取当前系统时间")
    state = {"epoch": now, "ntp": ntp, "changed": False}
    context.values["system_clock"] = state

    def cleanup():
        if not state["changed"]:
            return
        context.command_runner.run(
            ["sudo", "-n", "timedatectl", "set-ntp", "false"])
        context.command_runner.run(
            ["sudo", "-n", "date", "-s", "@%s" % state["epoch"]])
        if state["ntp"] in ("yes", "true", "1"):
            context.command_runner.run(
                ["sudo", "-n", "timedatectl", "set-ntp", "true"])

    context.add_cleanup("恢复系统时间和 NTP 状态", cleanup, priority=200)


@FIXTURES.register("isolated_password_expiry")
def _isolated_password_expiry(context, options):
    """Remove the disposable server used by the password-cycle case."""
    data_dir = Path(options.get("data_dir", "/tmp/fbase_regress_password_expiry"))
    if not str(data_dir).startswith("/tmp/fbase_regress_"):
        raise ConfigError("isolated_password_expiry data_dir 必须位于 /tmp/fbase_regress_ 下")

    def cleanup():
        context.command_runner.run(
            [context.manager.binary("pg_ctl"), "-D", str(data_dir),
             "stop", "-m", "immediate"], check=False, timeout=30)
        shutil.rmtree(str(data_dir), ignore_errors=True)

    # Stop the server before system_clock restores host time.
    context.add_cleanup("删除密码周期隔离集群", cleanup, priority=300)


@FIXTURES.register("isolated_mmr_daemon")
def _isolated_mmr_daemon(context, options):
    """Stop and delete the disposable instance used for MMR worker lifecycle."""
    data_dir = Path(options.get("data_dir", "/tmp/fbase_regress_mmr_daemon"))
    declared_port = str(options.get("port", "15445"))
    if not str(data_dir).startswith("/tmp/fbase_regress_"):
        raise ConfigError("isolated_mmr_daemon data_dir 必须位于 /tmp/fbase_regress_ 下")
    _remove_stale_isolated_cluster_root(context, data_dir)
    _reserve_isolated_mmr_ports(context, [declared_port])
    allocated_port = context.values["isolated_mmr_port_mapping"][declared_port]
    _register_isolated_mmr_instance(context, allocated_port, data_dir)

    def cleanup():
        context.command_runner.run(
            [context.manager.binary("pg_ctl"), "-D", str(data_dir),
             "stop", "-m", "immediate"], check=False, timeout=30)
        shutil.rmtree(str(data_dir), ignore_errors=True)

    context.add_cleanup("删除多活后台进程隔离实例", cleanup, priority=300)


@FIXTURES.register("isolated_mmr_node_creation")
def _isolated_mmr_node_creation(context, options):
    """Remove disposable instances and expose their real test topology."""
    _isolated_cluster_root(
        context, options, "isolated_mmr_node_creation",
        "/tmp/fbase_regress_mmr_node_creation", "删除多活节点创建隔离实例")
    _reclaim_stale_isolated_mmr_listeners(context)
    _reserve_isolated_mmr_ports(context)
    if not context.case.get("test_topology"):
        topology = _infer_isolated_mmr_topology(context)
        if topology:
            context.values["test_topology"] = topology


def _infer_isolated_mmr_topology(context):
    """Derive temporary node endpoints from the case's actual init commands."""
    instances = _isolated_mmr_instances(context)
    names = {}
    for command in _isolated_mmr_commands(context):
        for name, port in re.findall(
                r"fdd\.create_node\('([^']+)'.*?\bport=(\d+)", command):
            names[port] = name
    nodes = []
    for port in sorted(instances, key=int):
        name = names.get(port, "instance@%s" % port)
        nodes.append({"name": name,
                      "role": "MMR primary" if port in names else "standalone PostgreSQL",
                      "host": "127.0.0.1",
                      "port": port, "data_dir": instances[port]})
    if not nodes:
        return None
    relations = []
    if len(nodes) > 1:
        relations.append("MMR 多活: %s" % " <-> ".join(node["name"] for node in nodes))
    relations.append("物理流复制: 无（本用例未声明物理备库）")
    return {
        "summary": "节点数=%s；隔离 MMR 对等节点；无独立普通逻辑复制。" % len(nodes),
        "nodes": nodes, "relations": relations,
    }


def _isolated_mmr_commands(context, expand=True):
    """Return shell commands that create the temporary MMR nodes."""
    commands = []
    for step in (getattr(context, "case", {}) or {}).get("steps") or []:
        if step.get("type") != "command":
            continue
        argv = step.get("argv") or []
        if not expand:
            argv = [str(item).replace("{run_id}", context.values.get("run_id", "{run_id}"))
                    for item in argv]
        commands.append(" ".join(
            str(item) for item in (context.expand(argv) if expand else argv)))
    return commands


def _isolated_mmr_instances(context):
    """Map each isolated MMR listener port to the data directory it owns."""
    values = getattr(context, "values", {})
    instances = dict(values.get("isolated_mmr_port_data_dirs") or {})
    for command in _isolated_mmr_commands(context):
        for initialized in re.finditer(
                r"(?:initdb|pg_basebackup)\b.*?\s-D\s+(?P<data_dir>\S+).*?"
                r"port\s*=\s*(?P<port>\d+)", command):
            instances[initialized.group("port")] = initialized.group("data_dir")
    return instances


def _register_isolated_mmr_instance(context, port, data_dir):
    """Register fixtures whose init/config/start actions are separate steps."""
    instances = context.values.setdefault("isolated_mmr_port_data_dirs", {})
    instances[str(port)] = str(data_dir)


def _declared_isolated_mmr_ports(context):
    """Return every temporary PostgreSQL listener declared before expansion."""
    ports = []
    for command in _isolated_mmr_commands(context, expand=False):
        # A temporary server can be initialized by initdb or cloned with
        # pg_basebackup.  Most commands write postgresql.conf, while compact
        # fixture commands and tests may declare the listener inline.
        if not re.search(r"(?:initdb|pg_basebackup)\b", command):
            continue
        ports.extend(re.findall(r"port\s*=\s*(\d+)", command))
    return list(dict.fromkeys(ports))


def _allocate_isolated_mmr_listener(reserved):
    """Bind one available loopback port and retain the socket until pg_ctl starts."""
    # Begin away from well-known services, but do not assume any particular
    # kernel ephemeral-port range.  The retained socket makes any selected
    # port safe even when it lies inside that range.
    for port in list(range(20000, 65536)) + list(range(1025, 20000)):
        if str(port) in reserved:
            continue
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as exc:
            listener.close()
            if exc.errno == errno.EADDRINUSE:
                continue
            raise SafetyError("无法探测隔离 MMR 端口 %s: %s" % (port, exc))
        return str(port), listener
    raise SafetyError("没有可用的隔离 MMR 监听端口")


def _reserve_isolated_mmr_ports(context, declared_ports=None):
    """Allocate and reserve listener ports for the whole disposable topology."""
    reservations = {}
    try:
        mapping = dict(context.values.get("isolated_mmr_port_mapping") or {})
        for declared in (declared_ports or _declared_isolated_mmr_ports(context)):
            if declared in mapping:
                continue
            allocated, listener = _allocate_isolated_mmr_listener(reservations)
            mapping[declared] = allocated
            reservations[allocated] = listener
        context.values["isolated_mmr_port_mapping"] = mapping
    except Exception:
        for listener in reservations.values():
            listener.close()
        raise

    context.values["isolated_mmr_port_reservations"] = reservations

    def cleanup():
        for listener in context.values.pop(
                "isolated_mmr_port_reservations", {}).values():
            listener.close()

    # The cluster cleanup (priority 90) must stop every postmaster before the
    # last reservation is released.
    context.add_cleanup("释放隔离 MMR 端口保留", cleanup, priority=80)


def _release_isolated_mmr_port(context, port):
    reservations = context.values.get("isolated_mmr_port_reservations") or {}
    listener = reservations.pop(str(port), None)
    if listener is not None:
        listener.close()


def release_isolated_mmr_port_for_command(context, argv):
    """Release exactly the reserved port owned by an imminent pg_ctl start."""
    reservations = (getattr(context, "values", {}).get(
        "isolated_mmr_port_reservations") or {})
    if not reservations:
        return
    command = " ".join(str(item) for item in argv)
    if not re.search(r"\bpg_ctl\s+-D\s+\S+.*?\bstart\b", command):
        return
    start_data_dirs = re.findall(r"\bpg_ctl\s+-D\s+(\S+)", command)
    instances = _isolated_mmr_instances(context)
    matches = [(port, data_dir) for port, data_dir in instances.items()
               if data_dir in start_data_dirs]
    if not matches:
        raise SafetyError(
            "隔离 MMR 启动实例未登记监听端口，拒绝使用固定端口: %s" % command)
    for port, unused_data_dir in matches:
        listener = reservations.pop(port, None)
        if listener is not None:
            listener.close()


def _postgres_data_dir(pid):
    """Read the postmaster data directory without trusting lsof process names."""
    try:
        argv = (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
    except OSError:
        return None
    for index, argument in enumerate(argv[:-1]):
        if argument == b"-D":
            return Path(argv[index + 1].decode("utf-8", "replace"))
    return None


def _reclaim_stale_isolated_mmr_listeners(context):
    """Stop only orphaned framework postmasters occupying this case's ports."""
    for port in _isolated_mmr_instances(context):
        pids = _isolated_mmr_listener_pids(context, port)
        for pid in pids:
            data_dir = _postgres_data_dir(pid)
            if data_dir is None:
                raise SafetyError(
                    "端口 %s 被 PID %s 占用，但无法确认其 PostgreSQL 数据目录" %
                    (port, pid))
            if (not str(data_dir).startswith("/tmp/fbase_regress_") or
                    not (data_dir / "PG_VERSION").is_file()):
                raise SafetyError(
                    "端口 %s 被非本框架隔离实例占用（PID %s，数据目录 %s），拒绝停止" %
                    (port, pid, data_dir))
            context.command_runner.run(
                [context.manager.binary("pg_ctl"), "-D", str(data_dir),
                 "stop", "-m", "immediate"], check=False, timeout=30)
            shutil.rmtree(str(data_dir), ignore_errors=True)
    _wait_for_isolated_mmr_ports_free(context)


def _isolated_mmr_listener_pids(context, port):
    listener = context.command_runner.run(
        ["lsof", "-nP", "-t", "-iTCP:%s" % port, "-sTCP:LISTEN"],
        check=False, timeout=10)
    return [line.strip() for line in (listener.stdout or "").splitlines()
            if line.strip().isdigit()]


def _wait_for_isolated_mmr_ports_free(context, timeout=10):
    """Wait for an immediate-stop listener to release a reused test port."""
    ports = tuple(_isolated_mmr_instances(context))
    if not ports:
        return
    deadline = time.monotonic() + timeout
    while True:
        occupied = {port: _isolated_mmr_listener_pids(context, port)
                    for port in ports}
        occupied = {port: pids for port, pids in occupied.items() if pids}
        if not occupied:
            return
        if time.monotonic() >= deadline:
            details = ", ".join("%s(pid=%s)" % (port, ",".join(pids))
                                for port, pids in sorted(occupied.items()))
            raise OperationError("隔离 MMR 端口在停止后 %ss 仍被监听: %s" %
                                 (timeout, details))
        time.sleep(0.1)


def _session_psql(context, port, statement, timeout=60):
    """Execute SQL against a disposable session node without config selectors."""
    return context.command_runner.run(
        [context.manager.binary("psql"), "-X", "-v", "ON_ERROR_STOP=1",
         "-h", "127.0.0.1", "-p", str(port), "-U", "postgres",
         "-d", "postgres", "-c", statement], timeout=timeout)


@FIXTURES.register("shared_mmr_conflict_topology")
def _shared_mmr_conflict_topology(context, options):
    """Create one reusable non-2PC streaming-conflict MMR topology per run."""
    root = Path(options.get("data_dir", "/tmp/fbase_regress_mmr_conflict_session"))
    source_port = str(options.get("source_port", "15651"))
    target_port = str(options.get("target_port", "15652"))
    if not str(root).startswith("/tmp/fbase_regress_"):
        raise ConfigError("shared_mmr_conflict_topology data_dir 必须位于 /tmp/fbase_regress_ 下")
    source, target = root / "node134", root / "node135"
    _remove_stale_isolated_cluster_root(context, root)
    _reserve_isolated_mmr_ports(context, [source_port, target_port])
    mapping = context.values["isolated_mmr_port_mapping"]
    source_port, target_port = mapping[source_port], mapping[target_port]

    def init_node(data_dir, port, debug_mode):
        context.command_runner.run(
            [context.manager.binary("initdb"), "-D", str(data_dir), "-U", "postgres",
             "--auth-local=trust", "--auth-host=trust"], timeout=40)
        license_file = Path("/home/postgres/license/license.dat")
        if not license_file.is_file():
            raise SafetyError("缺少共享 MMR 会话 license 文件: %s" % license_file)
        shutil.copy2(str(license_file), str(data_dir / "license.dat"))
        with (data_dir / "postgresql.conf").open("a", encoding="utf-8") as stream:
            stream.write("\nshared_preload_libraries = 'fdd_mmr'\n")
            stream.write("fdd.running_databases = 'postgres'\n")
            stream.write("wal_level = logical\ntrack_commit_timestamp = on\n")
            stream.write("max_worker_processes = 8\nmax_logical_replication_workers = 4\n")
            stream.write("max_replication_slots = 10\nmax_wal_senders = 10\n")
            stream.write("debug_logical_replication_streaming = '%s'\n" % debug_mode)
            stream.write("logical_decoding_work_mem = '64kB'\n")
            stream.write("listen_addresses = '127.0.0.1'\nport = %s\n" % port)
        _release_isolated_mmr_port(context, port)
        context.command_runner.run(
            [context.manager.binary("pg_ctl"), "-D", str(data_dir), "-l",
             str(data_dir / "start.log"), "-w", "start"], timeout=40)
        _session_psql(context, port, "CREATE EXTENSION fdd_mmr")
        _session_psql(context, port, "CREATE EXTENSION fb_license")

    try:
        root.mkdir(parents=True)
        init_node(source, source_port, "immediate")
        init_node(target, target_port, "buffered")
        _session_psql(context, source_port,
                      "CREATE TABLE public.streaming_join_probe(id int PRIMARY KEY)")
        _session_psql(context, target_port,
                      "CREATE TABLE public.streaming_join_probe(id int PRIMARY KEY)")
        _session_psql(context, source_port,
                      "SELECT fdd.create_node('node134', "
                      "'host=127.0.0.1 port=%s user=postgres dbname=postgres',true,'parallel',false)" % source_port)
        _session_psql(context, source_port, "SELECT fdd.create_group('g1')")
        _session_psql(context, target_port,
                      "SELECT fdd.create_node('node135', "
                      "'host=127.0.0.1 port=%s user=postgres dbname=postgres',true,'parallel',true)" % target_port)
        _session_psql(context, target_port,
                      "SELECT fdd.join_group('g1','host=127.0.0.1 port=%s user=postgres dbname=postgres',true,'all','table_exist_error')" % source_port,
                      timeout=90)
    except Exception:
        for data_dir in (target, source):
            context.command_runner.run(
                [context.manager.binary("pg_ctl"), "-D", str(data_dir),
                 "stop", "-m", "immediate"], check=False, timeout=30)
        shutil.rmtree(str(root), ignore_errors=True)
        raise

    def cleanup():
        for data_dir in (target, source):
            context.command_runner.run(
                [context.manager.binary("pg_ctl"), "-D", str(data_dir),
                 "stop", "-m", "immediate"], check=False, timeout=30)
        shutil.rmtree(str(root), ignore_errors=True)

    context.add_cleanup("删除共享 streaming 冲突隔离集群", cleanup, priority=300)


@FIXTURES.register("shared_mmr_conflict_reset")
def _shared_mmr_conflict_reset(context, options):
    """Reset case-owned objects while retaining a reusable MMR topology."""
    source_port = str(options.get("source_port", "15651"))
    target_port = str(options.get("target_port", "15652"))
    set_name = options.get("set_name", "set1")
    tables = options.get("tables") or []
    resolvers = {
        "delete_missing": "skip",
        "delete_recently_updated": "skip",
        "insert_exists": "update_if_newer",
        "update_missing": "skip",
        "update_pkey_exists": "update_if_newer",
    }
    resolvers.update(options.get("resolvers") or {})
    if not _IDENTIFIER.match(set_name):
        raise ConfigError("shared_mmr_conflict_reset set_name 非法: %s" % set_name)
    if any(not _IDENTIFIER.match(table) for table in tables):
        raise ConfigError("shared_mmr_conflict_reset tables 包含非法名称")
    if (not isinstance(resolvers, dict) or
            any(not _IDENTIFIER.match(conflict) or not _IDENTIFIER.match(resolver)
                for conflict, resolver in resolvers.items())):
        raise ConfigError("shared_mmr_conflict_reset resolvers 包含非法名称")
    exists = _session_psql(
        context, source_port,
        "SELECT count(*) FROM fdd.mmr_replication_set WHERE set_name=%s" %
        _literal(set_name)).stdout
    if re.search(r"(?m)^\s*1\s*$", exists):
        for port in (source_port, target_port):
            _session_psql(context, port, "SELECT fdd.check_async_record()")
        for table in tables:
            _session_psql(
                context, source_port,
                "SELECT fdd.replication_set_remove_table(to_regclass(%s),%s,true) "
                "WHERE to_regclass(%s) IS NOT NULL" %
                (_literal("public.%s" % table), _literal(set_name),
                 _literal("public.%s" % table)), timeout=60)
        for port in (source_port, target_port):
            _session_psql(context, port, "SELECT fdd.check_async_record()")
    else:
        _session_psql(context, source_port,
                      "SELECT fdd.create_replication_set(%s,true,true,true,true,false,false,false)" %
                      _literal(set_name))
    for table in tables:
        for port in (target_port, source_port):
            _session_psql(context, port,
                          "DROP TABLE IF EXISTS public.%s" % _identifier(table))
    for conflict, resolver in sorted(resolvers.items()):
        _session_psql(
            context, target_port,
            "SELECT fdd.alter_local_node_set_conflict_resolver(%s,%s)" %
            (_literal(conflict), _literal(resolver)))
    for port in (source_port, target_port):
        _session_psql(
            context, port,
            "DO $$ DECLARE item record; BEGIN "
            "FOR item IN SELECT subname FROM pg_subscription LOOP "
            "EXECUTE format('ALTER SUBSCRIPTION %I DISABLE',item.subname); "
            "EXECUTE format('ALTER SUBSCRIPTION %I ENABLE',item.subname); "
            "END LOOP; END $$")
    for port in (source_port, target_port):
        _session_psql(context, port, "TRUNCATE fdd.mmr_conflict_history")


@FIXTURES.register("mmr_forwarding_objects")
def _mmr_forwarding_objects(context, options):
    """Own the external publisher and disposable normal-forwarding objects."""
    source = context.resolve_node(options.get("source", "mmr:mmr1"))
    target = context.resolve_node(options.get("target", "mmr:mmr2"))
    table = options.get("table")
    subscription = options.get("subscription")
    all_nodes = bool(options.get("all_nodes", False))
    data_dir = Path(options.get("data_dir", "/tmp/fbase_regress_mmr_forward"))
    for label, value in (("table", table), ("subscription", subscription)):
        if not _IDENTIFIER.match(value or ""):
            raise ConfigError("mmr_forwarding_objects 的 %s 非法: %s" % (label, value))
    if not str(data_dir).startswith("/tmp/fbase_regress_"):
        raise ConfigError("mmr_forwarding_objects data_dir 必须位于 /tmp/fbase_regress_ 下")
    existing = context.postgres.scalar(
        source, "postgres",
        "SELECT count(*)::text FROM fdd.mmr_subscription "
        "WHERE origin_node_id=2 AND target_node_id=1 AND forward_origins IS NOT NULL")
    if existing != "0":
        raise SafetyError("MMR 转发配置已有 %s 条，拒绝覆盖" % existing)

    def cleanup():
        errors = []
        try:
            context.postgres.execute_checked(
                source, "postgres", "postgres",
                "SELECT fdd.alter_forward_subs((SELECT sub_id FROM fdd.mmr_subscription "
                "WHERE origin_node_id=2 AND target_node_id=1), NULL)")
        except Exception as exc:
            errors.append("清空 MMR 普通订阅转发配置: %s" % exc)
        try:
            context.postgres.execute_checked(
                target, "postgres", "postgres",
                "DROP SUBSCRIPTION IF EXISTS %s" % _identifier(subscription))
        except Exception as exc:
            errors.append("删除普通订阅: %s" % exc)
        if all_nodes:
            try:
                context.postgres.execute_checked(
                    source, "postgres", "postgres",
                    "SELECT fdd.run_on_all_nodes(%s)" % _literal(
                        "DROP TABLE IF EXISTS public.%s" % _identifier(table)))
            except Exception as exc:
                errors.append("删除所有 MMR 成员上的测试表: %s" % exc)
        else:
            for node in (source, target):
                try:
                    context.postgres.execute_checked(
                        node, "postgres", "postgres",
                        "DROP TABLE IF EXISTS public.%s" % _identifier(table))
                except Exception as exc:
                    errors.append("删除 %s 上测试表: %s" % (node, exc))
        context.command_runner.run(
            [context.manager.binary("pg_ctl"), "-D", str(data_dir),
             "stop", "-m", "immediate"], check=False, timeout=30)
        shutil.rmtree(str(data_dir), ignore_errors=True)
        if errors:
            raise OperationError("; ".join(errors))

    context.add_cleanup("删除普通逻辑复制转发测试对象", cleanup, priority=250)


@FIXTURES.register("isolated_password_log")
def _isolated_password_log(context, options):
    """Preserve server stderr captures from the password-masking case."""
    data_dir = Path(options.get("data_dir", "/tmp/fbase_regress_password_log"))
    if not str(data_dir).startswith("/tmp/fbase_regress_"):
        raise ConfigError("isolated_password_log data_dir 必须位于 /tmp/fbase_regress_ 下")

    def cleanup():
        context.command_runner.run(
            [context.manager.binary("pg_ctl"), "-D", str(data_dir),
             "stop", "-m", "immediate"], check=False, timeout=30)
        for name in ("console_capture.log", "file_capture.log"):
            source = data_dir / name
            if source.is_file():
                destination = context.output_dir / name
                shutil.copy2(str(source), str(destination))
                context.values.setdefault("case_evidence", []).append(name)
        shutil.rmtree(str(data_dir), ignore_errors=True)

    context.add_cleanup("删除密码日志隔离集群", cleanup, priority=90)


def _isolated_cluster_root(context, options, fixture_name, default_data_dir,
                           cleanup_name):
    """Register cleanup for a root containing one or more disposable clusters."""
    data_dir = Path(options.get("data_dir", default_data_dir))
    if not str(data_dir).startswith("/tmp/fbase_regress_"):
        raise ConfigError("%s data_dir 必须位于 /tmp/fbase_regress_ 下" % fixture_name)
    _remove_stale_isolated_cluster_root(context, data_dir)

    def cleanup():
        clusters = set()
        if data_dir.is_dir() and (data_dir / "PG_VERSION").is_file():
            clusters.add(data_dir)
        for child in data_dir.iterdir() if data_dir.is_dir() else []:
            if child.is_dir() and (child / "PG_VERSION").is_file():
                clusters.add(child)
        # Commands may have expanded the fixture's data_dir placeholder before
        # startup.  The registered topology is therefore authoritative even
        # when filesystem enumeration misses a still-running postmaster.
        for cluster in _isolated_mmr_instances(context).values():
            cluster = Path(cluster)
            if (cluster / "PG_VERSION").is_file():
                clusters.add(cluster)
        for cluster in clusters:
            for source_name in ("start.log", "postgresql.log", "postgresql.csv"):
                source = cluster / source_name
                evidence_name = "%s.%s" % (cluster.name, source_name)
                if source.is_file():
                    shutil.copyfile(str(source), str(context.output_dir / evidence_name))
                    context.values.setdefault("case_evidence", []).append(evidence_name)
            context.command_runner.run(
                [context.manager.binary("pg_ctl"), "-D", str(cluster),
                 "stop", "-m", "immediate"], check=False, timeout=30)
        # pg_ctl can return while the postmaster still owns its listener.  The
        # next isolated MMR case reuses fixed ports, so do not return until the
        # kernel has released every port declared by this case.
        _wait_for_isolated_mmr_ports_free(context)
        for cluster in clusters:
            shutil.rmtree(str(cluster), ignore_errors=True)
        shutil.rmtree(str(data_dir), ignore_errors=True)

    context.add_cleanup(cleanup_name, cleanup, priority=90)


def _remove_stale_isolated_cluster_root(context, data_dir):
    """Stop and remove a prior interrupted run under a framework-owned root."""
    data_dir = Path(data_dir)
    command_clusters = [Path(cluster)
                        for cluster in _isolated_mmr_instances(context).values()]
    if not data_dir.exists() and not any(cluster.exists()
                                        for cluster in command_clusters):
        return
    if not str(data_dir).startswith("/tmp/fbase_regress_"):
        raise SafetyError("拒绝清理非框架隔离目录: %s" % data_dir)
    clusters = []
    if (data_dir / "PG_VERSION").is_file():
        clusters.append(data_dir)
    for child in data_dir.iterdir() if data_dir.is_dir() else []:
        if child.is_dir() and (child / "PG_VERSION").is_file():
            clusters.append(child)
    # The case command may expand {run_id} after fixture registration.  Use
    # the parsed instance paths as well so an interrupted run cannot leave a
    # nonempty data directory that makes the next initdb fail.
    for cluster in command_clusters:
        if (cluster / "PG_VERSION").is_file() and cluster not in clusters:
            clusters.append(cluster)
    for cluster in clusters:
        context.command_runner.run(
            [context.manager.binary("pg_ctl"), "-D", str(cluster),
             "stop", "-m", "immediate"], check=False, timeout=30)
        shutil.rmtree(str(cluster), ignore_errors=True)
    shutil.rmtree(str(data_dir), ignore_errors=True)


@FIXTURES.register("isolated_sm3_auth")
def _isolated_sm3_auth(context, options):
    """Remove the disposable clusters used by the SM3 authentication case."""
    _isolated_cluster_root(context, options, "isolated_sm3_auth",
                           "/tmp/fbase_regress_sm3_auth",
                           "删除 SM3 认证隔离集群")


@FIXTURES.register("isolated_license")
def _isolated_license(context, options):
    """Remove disposable clusters created to exercise license startup paths."""
    _isolated_cluster_root(context, options, "isolated_license",
                           "/tmp/fbase_regress_license",
                           "删除 license 隔离集群")


@FIXTURES.register("isolated_tlcp_transfer")
def _isolated_tlcp_transfer(context, options):
    """Remove source/target clusters and certificate files from TLCP transfer."""
    _isolated_cluster_root(context, options, "isolated_tlcp_transfer",
                           "/tmp/fbase_regress_tlcp_transfer",
                           "删除 TLCP 导出导入隔离集群")


@FIXTURES.register("isolated_tlcp_session")
def _isolated_tlcp_session(context, options):
    """Own the one temporary server shared by TLCP metadata cases."""
    _isolated_cluster_root(context, options, "isolated_tlcp_session",
                           "/tmp/fbase_regress_tlcp_session",
                           "删除共享 TLCP 元数据隔离集群")


@FIXTURES.register("isolated_tlcp_audit")
def _isolated_tlcp_audit(context, options):
    """Remove the disposable cluster used to audit TLCP UDFs."""
    _isolated_cluster_root(context, options, "isolated_tlcp_audit",
                           "/tmp/fbase_regress_tlcp_audit",
                           "删除 TLCP 审计隔离集群")


@FIXTURES.register("isolated_tlcp_handshake")
def _isolated_tlcp_handshake(context, options):
    """Remove the disposable cluster and certificates used for TLCP testing."""
    _isolated_cluster_root(context, options, "isolated_tlcp_handshake",
                           "/tmp/fbase_regress_tlcp_handshake",
                           "删除 TLCP 双向认证隔离集群")


@FIXTURES.register("isolated_ssl")
def _isolated_ssl(context, options):
    """Remove the disposable server and certificates used by SSL testing."""
    _isolated_cluster_root(context, options, "isolated_ssl",
                           "/tmp/fbase_regress_ssl",
                           "删除 SSL 隔离集群")


@FIXTURES.register("isolated_tde")
def _isolated_tde(context, options):
    """Register cleanup for a TDE cluster created by reportable case steps."""
    data_dir = Path(options.get("data_dir", "/tmp/fbase_regress_tde"))
    key_file = Path(options.get("key_file", "/tmp/fbase_regress_tde.txt"))
    if not str(data_dir).startswith("/tmp/fbase_regress_"):
        raise ConfigError("isolated_tde data_dir 必须位于 /tmp/fbase_regress_ 下")

    def cleanup():
        context.command_runner.run(
            [context.manager.binary("pg_ctl"), "-D", str(data_dir),
             "stop", "-m", "immediate"], check=False, timeout=30)
        shutil.rmtree(str(data_dir), ignore_errors=True)
        try:
            key_file.unlink()
        except OSError:
            pass
    context.add_cleanup("删除隔离 TDE 集群", cleanup, priority=90)


@FIXTURES.register("certificate_store_empty")
def _certificate_store_empty(context, options):
    """Reserve an empty certificate store for a cross-role TLCP lifecycle test."""
    node = context.resolve_node(options.get("node", "primary"))
    # Backup and archive tables intentionally retain lifecycle history.  The
    # transfer document only requires the active metadata store to be empty.
    tables = ["certs_info", "key_meta_data"]
    for table in tables:
        count = context.postgres.scalar(
            node, "postgres", "SELECT count(*) FROM fdb_mac.%s" % table)
        if count != "0":
            raise SafetyError(
                "TLCP 证书元数据表 fdb_mac.%s 非空，拒绝清理或覆盖已有证书" % table)

    # The product does not grant DBA/SSO DELETE on archived certificate metadata.
    # A nonempty active store is therefore a hard precondition, not something
    # the framework may erase behind the caller's back.


@FIXTURES.register("logical_slot")
def _logical_slot(context, options):
    name = options.get("name")
    if not _IDENTIFIER.match(name or ""):
        raise ConfigError("logical_slot fixture 包含非法槽名: %s" % name)
    node = context.resolve_node(options.get("node", "primary"))

    def cleanup():
        exists = context.postgres.scalar(
            node, "postgres", "SELECT count(*) FROM pg_replication_slots WHERE slot_name = %s" % _literal(name))
        if exists != "0":
            context.postgres.execute_checked(
                node, "postgres", "postgres", "SELECT pg_drop_replication_slot(%s)" % _literal(name))
    context.add_cleanup("删除逻辑复制槽 %s" % name, cleanup, priority=80)


@FIXTURES.register("logical_replication_objects")
def _logical_replication_objects(context, options):
    """Own a dedicated native logical publication/subscription and its tables."""
    source = context.resolve_node(options.get("source", "primary"))
    target = context.resolve_node(options.get("target", "subscriber"))
    database = options.get("database", "postgres")
    values = {
        "source_table": options.get("source_table"),
        "target_table": options.get("target_table", options.get("source_table")),
        "publication": options.get("publication"),
        "subscription": options.get("subscription"),
    }
    for label, value in values.items():
        if not _IDENTIFIER.match(value or ""):
            raise ConfigError("logical_replication_objects 的 %s 非法: %s" %
                              (label, value))
    source_ref = "public.%s" % _identifier(values["source_table"])
    target_ref = "public.%s" % _identifier(values["target_table"])

    def cleanup():
        context.postgres.execute_checked(
            target, "postgres", database,
            "DROP SUBSCRIPTION IF EXISTS %s" % _identifier(values["subscription"]))
        context.postgres.execute_checked(
            source, "postgres", database,
            "DROP PUBLICATION IF EXISTS %s" % _identifier(values["publication"]))
        context.postgres.execute_checked(
            target, "postgres", database, "DROP TABLE IF EXISTS %s" % target_ref)
        context.postgres.execute_checked(
            source, "postgres", database, "DROP TABLE IF EXISTS %s" % source_ref)

    context.add_cleanup("删除专属普通逻辑复制对象", cleanup, priority=200)


@FIXTURES.register("failover_delay_objects")
def _failover_delay_objects(context, options):
    """Remove the dedicated publication, subscription, slot, and tables.

    The delayed-commit case must not reuse the environment's all-table
    publication: fbase_mac metadata can legitimately differ between the two
    clusters.  Its dedicated publication contains only the disposable table.
    """
    database = options.get("database", "postgres")
    source_table = options.get("source_table")
    target_table = options.get("target_table", source_table)
    publication = options.get("publication")
    subscription = options.get("subscription")
    slot = options.get("slot")
    for label, value in (
            ("source_table", source_table), ("target_table", target_table),
            ("publication", publication), ("subscription", subscription),
            ("slot", slot)):
        if not _IDENTIFIER.match(value or ""):
            raise ConfigError("failover_delay_objects 的 %s 非法: %s" %
                              (label, value))

    primary = context.resolve_node(options.get("primary", "primary"))
    subscriber = context.resolve_node(options.get("subscriber", "subscriber"))
    source_ref = "public.%s" % _identifier(source_table)
    target_ref = "public.%s" % _identifier(target_table)

    def cleanup():
        context.postgres.execute_checked(
            subscriber, "postgres", database,
            "DROP SUBSCRIPTION IF EXISTS %s" % _identifier(subscription))
        exists = context.postgres.scalar(
            primary, database,
            "SELECT count(*) FROM pg_replication_slots WHERE slot_name = %s" %
            _literal(slot), user="postgres")
        if exists != "0":
            context.postgres.execute_checked(
                primary, "postgres", database,
                "SELECT pg_drop_replication_slot(%s)" % _literal(slot))
        context.postgres.execute_checked(
            primary, "postgres", database,
            "DROP PUBLICATION IF EXISTS %s" % _identifier(publication))
        context.postgres.execute_checked(
            subscriber, "postgres", database,
            "DROP TABLE IF EXISTS %s" % target_ref)
        context.postgres.execute_checked(
            primary, "postgres", database,
            "DROP TABLE IF EXISTS %s" % source_ref)

    # Settings use priority 100.  Tear down replication while its temporary
    # synchronous-replication and authentication settings still apply.
    context.add_cleanup("删除故障转移槽延迟提交测试对象", cleanup, priority=200)




@FIXTURES.register("hba_password_auth")
def _hba_password_auth(context, options):
    """Require password authentication for one disposable local role only."""
    role = options.get("role")
    if not _IDENTIFIER.match(role or ""):
        raise ConfigError("hba_password_auth fixture 包含非法角色名: %s" % role)
    node = context.resolve_node(options.get("node", "primary"))
    node_config = context.manager.node(node)
    if not context.manager.is_local(node_config["host"]):
        raise ConfigError("hba_password_auth 目前只支持本地节点")
    path = Path(node_config["data_dir"]) / "pg_hba.conf"
    original = path.read_text(encoding="utf-8")
    # The mac regression cluster stores passwords with SM3.  PostgreSQL's
    # ``password`` HBA method validates that format, unlike scram-sha-256.
    rule = ("# fbase_regress temporary password-auth rule for %s\n"
            "host all %s 127.0.0.1/32 password\n"
            "host all %s ::1/128 password\n") % (role, role, role)

    def reload_hba():
        context.postgres.execute_checked(
            node, "postgres", "postgres", "SELECT pg_reload_conf()")

    def cleanup():
        path.write_text(original, encoding="utf-8")
        reload_hba()

    context.add_cleanup("恢复 %s 的 pg_hba.conf" % role, cleanup, priority=90)
    path.write_text(rule + original, encoding="utf-8")
    reload_hba()


@FIXTURES.register("topology")
def _topology(context, options):
    groups = options.get("groups") or []
    missing = [name for name in groups if name not in context.manager.groups]
    if missing:
        raise ConfigError("cluster 缺少关系组: %s" % ",".join(missing))


@FIXTURES.register("roles")
def _roles(context, options):
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    setup = options.get("setup", True)
    cleanup_priority = options.get("cleanup_priority", 0)
    for role in options.get("create", []):
        if isinstance(role, str):
            role = {"name": role}
        name = role["name"]
        attributes = role.get("attributes", "LOGIN")
        password = role.get("password")
        login_user = role.get("login", False)
        def cleanup(role_name=name, node_name=node, db=database):
            context.postgres.execute_checked(
                node_name, "postgres", db,
                "DROP ROLE IF EXISTS %s" % _identifier(role_name))
        context.add_cleanup("删除角色 %s" % name, cleanup, priority=cleanup_priority)
        if setup:
            if login_user and not attributes and password is None:
                sql = "CREATE USER %s" % _identifier(name)
            else:
                sql = "CREATE ROLE %s %s" % (_identifier(name), attributes)
            if password is not None:
                sql += " PASSWORD %s" % _literal(password)
            context.postgres.execute_checked(node, "postgres", database, sql)


@FIXTURES.register("database")
def _database(context, options):
    name = options.get("name")
    if not name:
        return
    node = context.resolve_node(options.get("node", "primary"))
    if options.get("setup", True):
        sql = "CREATE DATABASE %s" % _identifier(name)
        if options.get("template"):
            sql += " TEMPLATE %s" % _identifier(options["template"])
        if options.get("encoding"):
            sql += " ENCODING %s" % _literal(options["encoding"])
        if options.get("locale"):
            sql += " LOCALE %s" % _literal(options["locale"])
        context.postgres.execute_checked(node, "postgres", "postgres", sql)

    def cleanup():
        context.postgres.execute_checked(
            node, "postgres", "postgres",
            "DROP DATABASE IF EXISTS %s WITH (FORCE)" % _identifier(name))
    context.add_cleanup("删除数据库 %s" % name, cleanup)


@FIXTURES.register("table")
def _table(context, options):
    """Create or only clean up a disposable table."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    schema = options.get("schema", "public")
    name = options.get("name")
    if not name:
        raise ConfigError("table fixture 缺少 name")
    table_ref = "%s.%s" % (_identifier(schema), _identifier(name))

    def cleanup():
        context.postgres.execute_checked(
            node, "postgres", database, "DROP TABLE IF EXISTS %s" % table_ref)
    context.add_cleanup("删除测试表 %s" % name, cleanup,
                        priority=options.get("cleanup_priority", 0))

    if options.get("setup", True):
        columns = options.get("columns", "id integer PRIMARY KEY")
        context.postgres.execute_checked(
            node, "postgres", database,
            "CREATE TABLE %s (%s)" % (table_ref, columns))


@FIXTURES.register("sequence")
def _sequence(context, options):
    """Create or only clean up a disposable PostgreSQL sequence."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    schema = options.get("schema", "public")
    name = options.get("name")
    if not name:
        raise ConfigError("sequence fixture 缺少 name")
    sequence_ref = "%s.%s" % (_identifier(schema), _identifier(name))

    def cleanup():
        context.postgres.execute_checked(
            node, "postgres", database,
            "DROP SEQUENCE IF EXISTS %s" % sequence_ref)
    context.add_cleanup("删除测试序列 %s" % name, cleanup,
                        priority=options.get("cleanup_priority", 0))

    if options.get("setup", True):
        context.postgres.execute_checked(
            node, "postgres", database, "CREATE SEQUENCE %s" % sequence_ref)


@FIXTURES.register("table_grants")
def _table_grants(context, options):
    """Grant disposable table privileges required as a prior test state."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    schema = options.get("schema", "public")
    table = options.get("table")
    if not table:
        raise ConfigError("table_grants fixture 缺少 table")
    table_ref = "%s.%s" % (_identifier(schema), _identifier(table))
    for role, privileges in (options.get("grants") or {}).items():
        context.postgres.execute_checked(
            node, "postgres", database, "GRANT %s ON %s TO %s" %
            (privileges, table_ref, _identifier(role)))


@FIXTURES.register("audit_rules")
def _audit_rules(context, options):
    """Remove named audit rules created by a case, including partial runs."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    for rule in options.get("rules", []):
        owner = rule["owner"]
        name = rule["name"]
        kind = rule["kind"]

        def cleanup(rule_owner=owner, rule_name=name, rule_kind=kind):
            source = "fdb_audit.audit_rule" if rule_owner == "sao" else "fdb_audit.audit_rules"
            count = context.postgres.scalar(
                node, database,
                "SELECT count(*) FROM %s WHERE rule_name = %s" %
                (source, _literal(rule_name)), user=rule_owner)
            if count != "0":
                function = "cancel_audit_stmt" if rule_kind == "stmt" else "cancel_audit_object"
                context.postgres.execute_checked(
                    node, rule_owner, database,
                    "SELECT fdb_audit.%s(%s)" % (function, _literal(rule_name)))
        context.add_cleanup("删除审计规则 %s" % name, cleanup)


@FIXTURES.register("mac_policy")
def _mac_policy(context, options):
    """Manage cleanup for a disposable fbase_mac policy and table.

    ``setup=false`` is used when a transfer case must expose each policy
    operation as a reportable test step.  The fixture then only registers
    idempotent cleanup callbacks.
    """
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    schema = options.get("schema", "public")
    table = options.get("table", "fbase_regress_mac_guard")
    policy = options.get("policy", "fbase_regress_mac_policy")
    column = options.get("column", "fbase_regress_mac_label")
    setup = options.get("setup", True)
    create_table = options.get("create_table", True)
    apply_policy = options.get("apply", True)
    levels = options.get("levels", [("L1", 10)])
    compartments = options.get("compartments", [("C1", 10)])
    labels = options.get("labels", [("L1:C1", 11)])
    table_columns = options.get("table_columns", "id integer PRIMARY KEY, payload text")
    table_ref = "%s.%s" % (_identifier(schema), _identifier(table))

    def drop_table():
        context.postgres.execute_checked(
            node, "postgres", database, "DROP TABLE IF EXISTS %s" % table_ref)

    def drop_policy():
        count = context.postgres.scalar(
            node, database,
            "SELECT count(*) FROM fdb_mac.policy WHERE policy_name = %s" %
            _literal(policy), user="sso")
        if count != "0":
            context.postgres.execute_checked(
                node, "sso", database,
                "SELECT fdb_mac.drop_policy(%s, true)" % _literal(policy))

    def remove_table_policy():
        count = context.postgres.scalar(
            node, database,
            "SELECT count(*) FROM fdb_mac.table_policy t "
            "JOIN pg_class c ON c.oid = t.relid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relname = %s" % (
                _literal(schema), _literal(table)), user="sso")
        if count != "0":
            context.postgres.execute_checked(
                node, "sso", database,
                "SELECT fdb_mac.remove_table_policy(%s, %s, %s, true)" % (
                    _literal(policy), _literal(schema), _literal(table)))

    # Register in creation order.  TestContext cleans callbacks in LIFO order:
    # restore settings (priority 100), then remove binding, policy, and table.
    context.add_cleanup("删除 MAC 测试表 %s" % table, drop_table)
    context.add_cleanup("删除 MAC 测试策略 %s" % policy, drop_policy)
    context.add_cleanup("移除 MAC 测试表策略 %s" % policy, remove_table_policy)

    if not setup:
        return

    if create_table:
        context.postgres.execute_checked(
            node, "postgres", database,
            "CREATE TABLE %s (%s)" % (table_ref, table_columns))
    context.postgres.execute_checked(
        node, "sso", database,
        "SELECT fdb_mac.create_policy(%s, %s)" % (_literal(policy), _literal(column)))
    for name, identifier in levels:
        context.postgres.execute_checked(
            node, "sso", database,
            "SELECT fdb_mac.create_level(%s, %s, %s)" %
            (_literal(policy), _literal(name), int(identifier)))
    for name, identifier in compartments:
        context.postgres.execute_checked(
            node, "sso", database,
            "SELECT fdb_mac.create_compartment(%s, %s, %s)" %
            (_literal(policy), _literal(name), int(identifier)))
    for label, identifier in labels:
        context.postgres.execute_checked(
            node, "sso", database,
            "SELECT fdb_mac.create_label(%s, %s, %s)" %
            (_literal(policy), _literal(label), int(identifier)))
    if apply_policy:
        if not create_table:
            raise ConfigError("mac_policy apply=true 时必须创建测试表")
        context.postgres.execute_checked(
            node, "sso", database,
            "SELECT fdb_mac.apply_table_policy(%s, %s, %s, false)" % (
                _literal(policy), _literal(schema), _literal(table)))


@FIXTURES.register("settings")
def _settings(context, options):
    selectors = options.get("nodes") or [options.get("node", "primary")]
    values = options.get("values") or {}
    action = options.get("apply", "reload")
    user = options.get("user", "postgres")
    setup = options.get("setup", True)
    restore_runtime_value = options.get("restore_runtime_value", False)
    if action not in ("reload", "restart"):
        raise ConfigError("settings fixture apply 只支持 reload 或 restart")
    saved = []

    def cleanup():
        for node, name, old, auto_value in saved:
            sql = ("ALTER SYSTEM RESET %s" % name if auto_value is None else
                   "ALTER SYSTEM SET %s = %s" % (name, _literal(auto_value)))
            context.postgres.execute_checked(node, user, "postgres", sql)
        if saved:
            getattr(context.manager, action)(quiet=True)

    cleanup_registered = False
    for selector in selectors:
        node = context.resolve_node(selector)
        for name, value in values.items():
            if not _GUC_NAME.match(name):
                raise ConfigError("非法 PostgreSQL 配置名: %s" % name)
            old = context.postgres.scalar(
                node, "postgres",
                "SELECT CASE WHEN current_setting(%s) = '' THEN "
                "'__FBASE_REGRESS_EMPTY_SETTING__' ELSE current_setting(%s) END" %
                (_literal(name), _literal(name)), user=user)
            if old == "__FBASE_REGRESS_EMPTY_SETTING__":
                old = ""
            auto_result = context.postgres.execute(
                node, "postgres", "postgres",
                "SELECT setting FROM pg_file_settings "
                "WHERE name = %s AND sourcefile LIKE '%%/postgresql.auto.conf' "
                "ORDER BY seqno DESC LIMIT 1" % _literal(name), structured=True)
            if auto_result.returncode != 0:
                raise OperationError(auto_result.output.strip() or
                                     "无法读取 ALTER SYSTEM 原配置")
            auto_value = auto_result.rows[0][0] if auto_result.rows else None
            if restore_runtime_value:
                auto_value = old
            saved.append((node, name, old, auto_value))
            if not cleanup_registered:
                context.add_cleanup("恢复 PostgreSQL 配置", cleanup, priority=100)
                cleanup_registered = True
            if setup:
                context.postgres.execute_checked(
                    node, user, "postgres",
                    "ALTER SYSTEM SET %s = %s" % (name, _literal(value)))
    if saved and setup:
        getattr(context.manager, action)(quiet=True)
        details = context.values.setdefault("postgresql_settings", [])
        purpose = options.get("purpose", "用例临时 PostgreSQL 配置")
        for node, name, old, unused_auto_value in saved:
            if setup:
                detail = context.postgres.check_setting(node, {
                    "source": "fixture", "name": name,
                    "equals": str(values[name]), "purpose": purpose,
                })
                detail["previous"] = old
                details.append(detail)
                if not detail["matched"]:
                    raise OperationError(
                        "PostgreSQL 参数 %s 未生效（实际=%s，要求=%s）" %
                        (name, detail["actual"], detail["requirement"]))
