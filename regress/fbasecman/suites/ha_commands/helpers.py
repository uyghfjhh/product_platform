"""Shared helper utilities for HA commands suite."""

import time
import os
try:
    import fcntl
except ImportError:
    fcntl = None
import sys
import shlex
from pathlib import Path

from framework.configuration import load_regression_config
from suites.ha_commands.runtime import HaCommandFailure, HaCommandRuntime

__all__ = ['_add_30_cluster_datasources', '_add_34_mmr_groups', '_add_bulk_datasources', '_add_bulk_mmr_groups', '_add_groups_without_promoted', '_add_hash_inside_string', '_add_second_mmr_group', '_add_single_cluster_mmr_group', '_as_crlf', '_balance_read_only_transform', '_bulk_datasources_have_weight', '_bulk_groups_have', '_cluster_datasources_have_status', '_comprehensive_transform', '_datasource_block', '_group_fields_with_format', '_has_only_crlf', '_hint_transform', '_inject_after_start', '_mixed_topology_transform', '_node_has_weight', '_omit_group_defaults', '_pg3_as_single_line_block', '_port_transform', '_remove_test_path', '_rename_disk_datasource', '_route_user_scope', '_run_route_mode', '_run_sql_parse_heartbeat_bind_invalid', '_run_sql_parse_heartbeat_bind_normal', '_run_sql_parse_heartbeat_bind_unsupported', '_run_sql_parse_transactions', '_single_read_only', '_single_read_only_keep_scope', '_sql_parse_transform', '_status_with_format', '_wait_pg_cluster_ready', '_weight_with_format', '_without_final_newline', '_without_promoted']


def _add_second_mmr_group(content):
    marker = 'group "rep_group" {'
    duplicate = (
        'group "mmr_group_extra" {\n'
        '    group_mode "mmr"\n'
        '    storage_db "postgres"\n'
        '    backend_clusters "pg_cluster_1,pg_cluster_2"\n'
        '    write_cluster "pg_cluster_2"\n'
        '    promoted_cluster "pg_cluster_1"\n'
        '    check "auto"\n'
        '}\n'
    )
    content = content.replace(marker, duplicate + marker, 1)
    return content.replace(
        'group_names "mmr_group,rep_group,balance_group,single_group"',
        'group_names "mmr_group,mmr_group_extra,rep_group,balance_group,single_group"',
        1,
    )


def _balance_read_only_transform(rt):
    standby_port = rt.env.config["database"]["ports"]["mmr1_standby2"]
    system_identifier = rt._query_scalar(
        standby_port, "SELECT system_identifier FROM pg_control_system();",
        "balance_read_only_pg_5_system_identifier.log")

    def transform(content):
        group = '''
group "balance_read_only" {
    group_mode "balance"
    storage_db "postgres"
    access_mode "read_only"
    backend_clusters "pg_cluster_1"
    check "auto"
}
'''
        datasource = '''
datasources "pg_5" {
    host "%s"
    port %s
    cluster_name "pg_cluster_1"
    weight 10
    status "active"
    application_name "pg_241"
    system_identifier "%s"
    tls "disable"
}
''' % (rt.env.config["database"]["mmr_host"], standby_port, system_identifier)
        user = '''
user "balance_reader" {
    group_names "balance_read_only"
    authentication "none"
    storage_user "postgres"
    pool "transaction"
    rw_split_method "none"
}
'''
        content = content.replace('group "mmr_group" {', group + '\ngroup "mmr_group" {', 1)
        content = content.replace('user "postgres" {', datasource + '\n' + user + '\nuser "postgres" {', 1)
        return content

    return transform


def _has_only_crlf(data):
    return b"\r\n" in data and data.replace(b"\r\n", b"").find(b"\n") == -1


def _omit_group_defaults(content):
    content = content.replace('    storage_db "postgres"\n    access_mode "read_write"\n'
                              '    backend_clusters "pg_cluster_1,pg_cluster_2"',
                              '    storage_db "postgres"\n'
                              '    backend_clusters "pg_cluster_1,pg_cluster_2"', 1)
    return content.replace('    storage_db "postgres"\n    access_mode "read_write"\n'
                           '    backend_clusters "pg_cluster_1"',
                           '    storage_db "postgres"\n    backend_clusters "pg_cluster_1"', 1)


def _add_30_cluster_datasources(content):
    blocks = []
    for index in range(1, 31):
        blocks.extend(('datasources "cluster_ds_%02d" {' % index,
                       '    host "192.0.2.1"', '    port %d' % (57000 + index),
                       '    cluster_name "bulk_cluster"', '    weight 10',
                       '    status "active"', '    tls "disable"', '}', ''))
    return content + '\n' + '\n'.join(blocks)


def _port_transform(rt, group):
    def transform(content):
        content = _route_user_scope(content, group)
        content = content.replace('ports "%s"' % rt.listen_port,
                                  'ports "%s,%s"' %
                                  (rt.listen_port, rt.read_port), 1)
        marker = 'group "%s" {\n' % group
        content = content.replace(marker, marker + '    write_port %s\n' %
                                  rt.listen_port, 1)
        return content.replace('    rw_split_method "none"',
                               '    rw_split_method "port"', 1)
    return transform


def _pg3_as_single_line_block(content):
    marker = 'datasources "pg_3" {'
    start = content.index(marker)
    end = content.index('\n}', start) + 2
    block = content[start:end]
    single_line = " ".join(line.strip() for line in block.splitlines())
    return content[:start] + single_line + content[end:]


def _hint_transform(group):
    def transform(content):
        content = _route_user_scope(content, group)
        return content.replace('    rw_split_method "none"',
                               '    rw_split_method "hint"', 1)
    return transform


def _run_route_mode(rt, group, mode, sql, expected_text, predicate,
                    transform=None, port=None):
    title = "%s (%s)" % (group, mode)
    rt.start(transform=transform)
    rt.psql(
        'SHOW GROUP_ROUTING %s;' % group,
        "%s：查看运行态路由" % title,
        "%s group_mode=%s 且存在可用路由候选" % (group, mode),
        lambda output: group in output and mode in output,
    )
    rt.psql_business(sql, "%s：真实业务连接验证" % title,
                     expected_text, predicate, group=group, port=port)


def _run_sql_parse_heartbeat_bind_invalid(rt):
    conf = rt.start(transform=_sql_parse_transform("mmr_group"))
    config_text = conf.read_text(encoding="utf-8")
    rt.check(
        "确认异常 heartbeat Bind 测试配置",
        "mmr_group 启用 sql_parse、服务端 PreparedStatement 缓存及 select 1 heartbeat",
        "rw_split_method=sql_parse; pool_reserve_prepared_statement=yes; heartbeat_request=select 1",
        all(value in config_text for value in (
            'rw_split_method "sql_parse"',
            'pool_reserve_prepared_statement yes',
            'heartbeat_request "select 1"',
        )))
    probe = rt.root / "suites" / "ha_commands" / "assets" / "heartbeat_bind_probe.py"
    if not probe.exists():
        raise HaCommandFailure("missing heartbeat Bind probe: %s" % probe)
    rc, output = rt.run_command(
        [sys.executable, str(probe), str(rt.listen_port), "malformed"],
        rt.logs_dir / "heartbeat_bind_invalid.log", cwd=rt.workdir,
        step_title="执行 SQL_PARSE heartbeat 截断 Bind 流程", check=False)
    rt.check(
        "验证 SQL_PARSE heartbeat 异常 Bind 被拒绝",
        "截断 Bind 返回 ErrorResponse(E) 和 ReadyForQuery(Z)，不返回 DataRow",
        output, "HEARTBEAT_INVALID_BIND_REJECTED=OK" in output)
    rt.psql(
        "SHOW SERVER_PREP_STMTS;",
        "核对异常 heartbeat Bind 未部署到 PostgreSQL 后端",
        "后端 PreparedStatement 部署列表不包含 SELECT 1",
        lambda output: "SELECT 1" not in output,
    )


def _bulk_datasources_have_weight(text, weight):
    return all('weight %d' % weight in _datasource_block(text, 'bulk_ds_%02d' % index)
               for index in range(1, 31))


def _bulk_groups_have(text, write_cluster, promoted_cluster):
    for index in range(1, 31):
        block = _datasource_block(text.replace('group "', 'datasources "'),
                                  'bulk_mmr_%02d' % index)
        if ('write_cluster "%s"' % write_cluster not in block or
                'promoted_cluster "%s"' % promoted_cluster not in block):
            return False
    return True


def _without_promoted(content):
    return content.replace('    promoted_cluster "pg_cluster_1"\n', '', 1)


def _single_read_only(content):
    content = _route_user_scope(content, "single_group")
    return _single_read_only_keep_scope(content)


def _datasource_block(text, name):
    start = text.index('datasources "%s" {' % name)
    end = text.index('\n}\n', start) + 2
    return text[start:end]


def _run_sql_parse_heartbeat_bind_normal(rt):
    conf = rt.start(transform=_sql_parse_transform("mmr_group"))
    config_text = conf.read_text(encoding="utf-8")
    rt.check(
        "确认 heartbeat Bind 测试配置",
        "mmr_group 启用 sql_parse、服务端 PreparedStatement 缓存及 select 1 heartbeat",
        "rw_split_method=sql_parse; pool_reserve_prepared_statement=yes; heartbeat_request=select 1",
        all(value in config_text for value in (
            'rw_split_method "sql_parse"',
            'pool_reserve_prepared_statement yes',
            'heartbeat_request "select 1"',
        )))
    jar = rt.root / rt.env.config["local"]["jdbc_lib_dir"] / "postgresql-42.7.7.jar"
    source = rt.root / "suites" / "sql_parse" / "assets" / "HeartbeatBindNormal.java"
    if not jar.exists() or not source.exists():
        raise HaCommandFailure("missing JDBC heartbeat asset or jar: %s %s" % (source, jar))
    rt.run_command(
        ["javac", "-cp", str(jar), "-d", str(rt.workdir), str(source)],
        rt.logs_dir / "HeartbeatBindNormal.javac.log", cwd=rt.workdir,
        step_title="编译 SQL_PARSE heartbeat JDBC 测试")
    jdbc_url = ("jdbc:postgresql://127.0.0.1:%s/mmr_group?"
                "prepareThreshold=1&preferQueryMode=extended&"
                "binaryTransfer=false") % rt.listen_port
    _, output = rt.run_command(
        ["java", "-cp", "%s:%s" % (rt.workdir, jar),
         "HeartbeatBindNormal", jdbc_url, "postgres", ""],
        rt.logs_dir / "HeartbeatBindNormal.log", cwd=rt.workdir,
        step_title="执行 SQL_PARSE heartbeat JDBC Extended Bind 流程")
    rt.check(
        "验证 SQL_PARSE heartbeat 本地 Bind 正常流程",
        "客户端发送 Parse、Bind、Execute、Sync；服务端返回 BindComplete(2)、DataRow(D)、CommandComplete(C)、ReadyForQuery(Z)",
        output, "HEARTBEAT_JDBC_EXTENDED=OK" in output)
    rt.psql(
        "SHOW SERVER_PREP_STMTS;",
        "核对 heartbeat 未部署到 PostgreSQL 后端",
        "当前 heartbeat statement 不出现在后端 PreparedStatement 部署列表",
        lambda output: "SELECT 1" not in output,
    )


def _add_bulk_datasources(content):
    blocks = []
    for index in range(1, 31):
        blocks.extend((
            'datasources "bulk_ds_%02d" {' % index,
            '    host "192.0.2.1"',
            '    port %d' % (55000 + index),
            '    cluster_name "bulk_cluster"',
            '    weight 10',
            '    status "active"',
            '    tls "disable"',
            '}',
            '',
        ))
    return content + '\n' + '\n'.join(blocks)


def _add_single_cluster_mmr_group(content):
    marker = 'group "rep_group" {'
    group = (
        'group "mmr_group_one_cluster" {\n'
        '    group_mode "mmr"\n'
        '    storage_db "postgres"\n'
        '    backend_clusters "pg_cluster_1"\n'
        '    write_cluster "pg_cluster_1"\n'
        '    check "auto"\n'
        '}\n'
    )
    content = content.replace(marker, group + marker, 1)
    return content.replace(
        'group_names "mmr_group,rep_group,balance_group,single_group"',
        'group_names "mmr_group,mmr_group_one_cluster,rep_group,balance_group,single_group"',
        1,
    )


def _single_read_only_keep_scope(content):
    content = content.replace('group "single_group" {\n    group_mode "single"\n'
                              '    storage_db "postgres"\n    access_mode "read_write"',
                              'group "single_group" {\n    group_mode "single"\n'
                              '    storage_db "postgres"\n    access_mode "read_only"', 1)
    return content


def _group_fields_with_format(content):
    content = content.replace(
        '    write_cluster "pg_cluster_2"',
        '\twrite_cluster    "pg_cluster_2"    # keep-write-format', 1,
    )
    return content.replace(
        '    promoted_cluster "pg_cluster_1"',
        '      promoted_cluster\t"pg_cluster_1"    # keep-promoted-format', 1,
    )


def _add_bulk_mmr_groups(content):
    groups = []
    for index in range(1, 31):
        groups.extend((
            'group "bulk_mmr_%02d" {' % index,
            '    group_mode "mmr"',
            '    storage_db "postgres"',
            '    backend_clusters "pg_cluster_1,pg_cluster_2"',
            '    write_cluster "pg_cluster_2"',
            '    promoted_cluster "pg_cluster_1"',
            '    check "auto"',
            '}',
            '',
        ))
    content = content.replace('group "rep_group" {', '\n'.join(groups) + 'group "rep_group" {', 1)
    group_names = ','.join(['bulk_mmr_%02d' % index for index in range(1, 31)])
    return content.replace(
        'group_names "mmr_group,rep_group,balance_group,single_group"',
        'group_names "mmr_group,rep_group,balance_group,single_group,%s"' % group_names,
        1)


def _run_sql_parse_heartbeat_bind_unsupported(rt):
    conf = rt.start(transform=_sql_parse_transform("mmr_group"))
    config_text = conf.read_text(encoding="utf-8")
    rt.check(
        "确认不支持格式 heartbeat Bind 测试配置",
        "mmr_group 启用 sql_parse、服务端 PreparedStatement 缓存及 select 1 heartbeat",
        "rw_split_method=sql_parse; pool_reserve_prepared_statement=yes; heartbeat_request=select 1",
        all(value in config_text for value in (
            'rw_split_method "sql_parse"',
            'pool_reserve_prepared_statement yes',
            'heartbeat_request "select 1"',
        )))
    probe = rt.root / "suites" / "ha_commands" / "assets" / "heartbeat_bind_probe.py"
    _, output = rt.run_command(
        [sys.executable, str(probe), str(rt.listen_port), "binary"],
        rt.logs_dir / "heartbeat_bind_unsupported.log", cwd=rt.workdir,
        step_title="执行 SQL_PARSE heartbeat 二进制结果格式 Bind 回退流程")
    rt.check(
        "验证不支持格式退出本地 heartbeat bypass 并回退后端",
        "完整二进制结果格式 Bind 返回后端结果，不使用本地文本缓存",
        output, "HEARTBEAT_UNSUPPORTED_FORMAT_FALLBACK=OK" in output)
    rt.psql(
        "SHOW SERVER_PREP_STMTS;",
        "核对不支持格式的 heartbeat 已部署到 PostgreSQL 后端",
        "后端 PreparedStatement 部署列表包含 SELECT 1",
        lambda output: "SELECT 1" in output,
    )


def _rename_disk_datasource(content):
    return content.replace('datasources "pg_3" {', 'datasources "pg_3_disk" {', 1)


def _without_final_newline(content):
    return content.rstrip("\n")


def _comprehensive_transform(rt):
    """Add production-style formatting and legal pool/method combinations."""
    def transform(content):
        db = rt.env.config["database"]
        ports = db["ports"]
        node_specs = (
            ("pg_1", "mmr1", "pg_cluster_1", ""),
            ("pg_3", "mmr1_standby1", "pg_cluster_1", "pg_240"),
            ("pg_5", "mmr1_standby2", "pg_cluster_1", "pg_241"),
            ("pg_6", "mmr1_standby3", "pg_cluster_1", "pg_242"),
            ("pg_2", "mmr2", "pg_cluster_2", ""),
            ("pg_4", "mmr2_standby1", "pg_cluster_2", "pg_250"),
            ("pg_7", "mmr2_standby2", "pg_cluster_2", "pg_251"),
            ("pg_8", "mmr2_standby3", "pg_cluster_2", "pg_252"),
        )
        node_specs = list(node_specs)
        for cluster, prefix, cluster_name, app_base in (
            ("mmr1", "pg", "pg_cluster_1", 239),
            ("mmr2", "pg", "pg_cluster_2", 249),
        ):
            standby_ports = db["ports"].get("%s_standbys" % cluster, ())
            for index in range(4, len(standby_ports) + 1):
                node_specs.append(("%s_%d" % (prefix, 5 + index if cluster == "mmr1" else 8 + index),
                                   "%s_standby%d" % (cluster, index), cluster_name,
                                   "pg_%d" % (app_base + index)))
        def configured_port(port_name):
            if port_name in ports:
                return ports[port_name]
            cluster, index = port_name.rsplit("_standby", 1)
            return ports["%s_standbys" % cluster][int(index) - 1]
        metadata = []
        for name, port_name, cluster, app_name in node_specs:
            host = db["mmr_host"]
            user = db["mmr_pg_user"]
            identifier = rt._query_scalar(
                configured_port(port_name), "SELECT system_identifier FROM pg_control_system();",
                "comprehensive_%s_system_identifier.log" % name)
            metadata.append({"name": name, "host": host, "port": configured_port(port_name),
                             "cluster": cluster, "application_name": app_name or "(default)",
                             "system_identifier": identifier, "status": "active"})
        rt.datasource_metadata = metadata
        content = content.replace(
            'ports "%s"' % rt.listen_port,
            'ports "%s,%s"' % (rt.listen_port, rt.read_port), 1)
        content = content.replace(
            '    check "auto"',
            '    write_port %s\n    check "auto"' % rt.listen_port, 1)
        content = content.replace(
            'group "rep_group" {\n    group_mode "replication"\n    storage_db "postgres"\n    backend_clusters "pg_cluster_1"',
            'group "rep_group" {\n    group_mode "replication"\n    storage_db "postgres"\n'
            '    write_port %s\n    backend_clusters "pg_cluster_1"' % rt.listen_port, 1)
        group_additions = '''
# 线上共享物理节点的业务组；中文注释用于验证配置文件字符集
group "mmr_group_b" {
\tgroup_mode "mmr"       # MMR 组：主备混合部署
    storage_db    "postgres"      # 存储数据库
    backend_clusters "pg_cluster_1,pg_cluster_2"
      write_cluster "pg_cluster_1"  # 缩进不完全对齐是现场常见格式
    promoted_cluster "pg_cluster_2"

    check "auto"
}
group "mmr_group_c" {
    group_mode "mmr"
    storage_db "postgres"
    backend_clusters "pg_cluster_1,pg_cluster_2"
    write_cluster "pg_cluster_2"
    promoted_cluster "pg_cluster_1"
    check "auto"
}
group "mmr_group_d" {
    group_mode "mmr"
    storage_db "postgres"
    backend_clusters "pg_cluster_1,pg_cluster_2"
    write_cluster "pg_cluster_1"
    promoted_cluster "pg_cluster_2"
    check "auto"
}
group "mmr_group_e" {
    group_mode "mmr"
    storage_db "postgres"
    backend_clusters "pg_cluster_1,pg_cluster_2"
    write_cluster "pg_cluster_2"
    promoted_cluster "pg_cluster_1"
    check "auto"
}
group "mmr_group_f" {
    group_mode "mmr"
    storage_db "postgres"
    backend_clusters "pg_cluster_1,pg_cluster_2"
    write_cluster "pg_cluster_1"
    promoted_cluster "pg_cluster_2"
    check "auto"
}
group "mmr_group_g" {
    group_mode "mmr"
    storage_db "postgres"
    backend_clusters "pg_cluster_1,pg_cluster_2"
    write_cluster "pg_cluster_2"
    promoted_cluster "pg_cluster_1"
    check "auto"
}
group "mmr_group_h" {
    group_mode "mmr"
    storage_db "postgres"
    backend_clusters "pg_cluster_1,pg_cluster_2"
    write_cluster "pg_cluster_1"
    promoted_cluster "pg_cluster_2"
    check "auto"
}
group "rep_group_b" {
    group_mode "replication"
    storage_db "postgres"
    backend_clusters "pg_cluster_1"
    check "auto"
}
group "rep_group_c" {
    group_mode "replication"
    storage_db "postgres"
    backend_clusters "pg_cluster_1"
    check "auto"
}
group "balance_group_b" {
    group_mode "balance"
    storage_db "postgres"
    access_mode "read_only"
    backend_clusters "pg_cluster_1,pg_cluster_2"
    check "auto"
}
group "balance_group_c" {
    group_mode "balance"
    storage_db "postgres"
    access_mode "read_write"
    backend_clusters "pg_cluster_2,pg_cluster_1"
    check "auto"
}
group "single_group_b" {
    group_mode "single"
    storage_db "postgres"
    access_mode "read_only"
    backend_clusters "pg_cluster_1"
    check "auto"
}
group "single_group_c" {
    group_mode "single"
    storage_db "postgres"
    access_mode "read_only"
    backend_clusters "pg_cluster_2"
    check "auto"
}
'''
        content = content.replace('datasources "pg_1" {', group_additions + '\ndatasources "pg_1" {', 1)
        datasource_additions = []
        for name, port_name, cluster, app_name in node_specs:
            if name in ("pg_1", "pg_2", "pg_3", "pg_4"):
                continue
            item = next(value for value in metadata if value["name"] == name)
            lines = ['datasources "%s" {' % name, '    host "%s"' % item["host"],
                     '    port %s' % item["port"], '    cluster_name "%s"' % cluster,
                     '    weight 10', '    status "active"']
            if app_name:
                lines.append('    application_name "%s"' % app_name)
            lines.extend(['    system_identifier "%s"' % item["system_identifier"],
                          '    tls "disable"', '}'])
            datasource_additions.append('\n'.join(lines))
        content = content.replace('user "postgres" {',
                                  '\n\n'.join(datasource_additions) + '\n\nuser "postgres" {', 1)
        additions = '''
# production-style routing combinations; comments and alignment are intentional
user "ha_mmr_hint_tx" {
    group_names "mmr_group"
    authentication "none"      # hint requires transaction pooling
    storage_user "postgres"
    pool "transaction"
    rw_split_method "hint"
}
user "ha_mmr_port_tx" {
    group_names "mmr_group"
    authentication "none"
    storage_user "postgres"
    pool "transaction"
    rw_split_method "port"
}
user "ha_mmr_sql_tx" {
    group_names "mmr_group"
    authentication "none"
    storage_user "postgres"
    pool "transaction"
    pool_discard no
    pool_reserve_prepared_statement yes
    rw_split_method "sql_parse"
}
user "ha_mmr_session" {
    group_names "mmr_group"
    authentication "none"
    storage_user "postgres"
    pool "session"
    rw_split_method "none"
}
user "ha_rep_hint_tx" {
    group_names "rep_group"
    authentication "none"
    storage_user "postgres"
    pool "transaction"
    rw_split_method "hint"
}
user "ha_rep_port_tx" {
    group_names "rep_group"
    authentication "none"
    storage_user "postgres"
    pool "transaction"
    rw_split_method "port"
}
user "ha_rep_sql_tx" {
    group_names "rep_group"
    authentication "none"
    storage_user "postgres"
    pool "transaction"
    pool_discard no
    pool_reserve_prepared_statement yes
    rw_split_method "sql_parse"
}
user "ha_rep_session" {
    group_names "rep_group"
    authentication "none"
    storage_user "postgres"
    pool "session"
    rw_split_method "none"
}
user "ha_single_session" {
    group_names "single_group"
    authentication "none"
    storage_user "postgres"
    pool "session"
    rw_split_method "none"
}
user "ha_single_statement" {
    group_names "single_group"
    authentication "none"
    storage_user "postgres"
    pool "statement"
    rw_split_method "none"
}
'''
        content = content.replace('user "postgres" {', additions + '\nuser "postgres" {', 1)
        # Keep deliberately irregular production-style formatting in the fixture:
        # mixed indentation, tabs, Chinese comments, blank lines and a value with #.
        content = content.replace(
            'datasources "pg_5" {',
            '# 备库节点：中文注释、非对齐缩进和行尾注释\n'
            'datasources "pg_5" {', 1)
        content = content.replace(
            '    port %s\n' % ports["mmr1_standby2"],
            '\tport %s    # 端口配置，使用 Tab 和多个空格\n' % ports["mmr1_standby2"], 1)
        content = content.replace(
            '    tls "disable"\n}',
            '    tls "disable"    # 中文：保持 TLS 配置\n}', 1)
        content = content.replace(
            '    authentication "none"      # hint requires transaction pooling',
            '    authentication "none"      # 提示路由：事务池必须保留注释', 1)
        content = content.replace(
            'group_names "mmr_group,rep_group,balance_group,single_group"',
            'group_names "mmr_group,mmr_group_b,mmr_group_c,mmr_group_d,'
            'mmr_group_e,mmr_group_f,mmr_group_g,mmr_group_h,rep_group,'
            'rep_group_b,rep_group_c,balance_group,balance_group_b,'
            'balance_group_c,single_group,single_group_b,single_group_c"', 1)
        rt.comprehensive_group_lines = (
            'group mmr_group: mode=mmr backend_clusters=pg_cluster_1,pg_cluster_2 write_cluster=pg_cluster_2 promoted_cluster=pg_cluster_1 write_port=%s check=auto' % rt.listen_port,
            'group mmr_group_b: mode=mmr backend_clusters=pg_cluster_1,pg_cluster_2 write_cluster=pg_cluster_1 promoted_cluster=pg_cluster_2 check=auto',
            'group mmr_group_c: mode=mmr backend_clusters=pg_cluster_1,pg_cluster_2 write_cluster=pg_cluster_2 promoted_cluster=pg_cluster_1 check=auto',
            'group mmr_group_d: mode=mmr backend_clusters=pg_cluster_1,pg_cluster_2 write_cluster=pg_cluster_1 promoted_cluster=pg_cluster_2 check=auto',
            'group mmr_group_e: mode=mmr backend_clusters=pg_cluster_1,pg_cluster_2 write_cluster=pg_cluster_2 promoted_cluster=pg_cluster_1 check=auto',
            'group mmr_group_f: mode=mmr backend_clusters=pg_cluster_1,pg_cluster_2 write_cluster=pg_cluster_1 promoted_cluster=pg_cluster_2 check=auto',
            'group mmr_group_g: mode=mmr backend_clusters=pg_cluster_1,pg_cluster_2 write_cluster=pg_cluster_2 promoted_cluster=pg_cluster_1 check=auto',
            'group mmr_group_h: mode=mmr backend_clusters=pg_cluster_1,pg_cluster_2 write_cluster=pg_cluster_1 promoted_cluster=pg_cluster_2 check=auto',
            'group rep_group: mode=replication backend_clusters=pg_cluster_1 write_port=%s check=auto' % rt.listen_port,
            'group rep_group_b: mode=replication backend_clusters=pg_cluster_1 check=auto',
            'group rep_group_c: mode=replication backend_clusters=pg_cluster_1 check=auto',
            'group balance_group: mode=balance backend_clusters=pg_cluster_1,pg_cluster_2 access_mode=read_write check=auto',
            'group balance_group_b: mode=balance backend_clusters=pg_cluster_1,pg_cluster_2 access_mode=read_only check=auto',
            'group balance_group_c: mode=balance backend_clusters=pg_cluster_2,pg_cluster_1 access_mode=read_write check=auto',
            'group single_group: mode=single backend_clusters=pg_cluster_1 access_mode=read_write check=auto',
            'group single_group_b: mode=single backend_clusters=pg_cluster_1 access_mode=read_only check=auto',
            'group single_group_c: mode=single backend_clusters=pg_cluster_2 access_mode=read_only check=auto',
        )
        content = content.replace(
            "# production-style routing combinations; comments and alignment are intentional\n",
            "# production-style routing combinations; comments and alignment are intentional\r\n"
            "# 中文 CRLF 行：验证混合换行符\r\n", 1)
        return content.rstrip("\n")
    return transform


def _remove_test_path(path):
    """Remove one stale test artifact without touching its parent directory."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        path.rmdir()


def _run_sql_parse_transactions(rt, group, mode, read_ports, write_port):
    rt.start(transform=_sql_parse_transform(group))
    rt.psql('SHOW GROUP_ROUTING %s;' % group,
            "%s sql_parse：查看运行态路由" % group,
            "%s group_mode=%s 且存在可用路由" % (group, mode),
            lambda output: group in output and mode in output)
    rt.psql_business(
        'SELECT inet_server_addr(), inet_server_port(), pg_is_in_recovery();',
        "%s sql_parse：验证只读 SELECT 路由" % group,
        "SELECT 落到允许的读候选端口",
        lambda output: any(str(port) in output for port in read_ports),
        group=group)
    rt.psql_business(
        'BEGIN; CREATE TEMP TABLE ha_sql_parse_probe(id int); '
        'SELECT inet_server_addr(), inet_server_port(), pg_is_in_recovery(); ROLLBACK;',
        "%s sql_parse：验证写事务路由" % group,
        "包含 DDL 的事务落到 primary/write-leader 端口并回滚",
        lambda output: str(write_port) in output and "CREATE TABLE" in output and "ROLLBACK" in output,
        group=group)


def _node_has_weight(output, node, weight):
    return any(node in line and str(weight) in line for line in output.splitlines())


def _status_with_format(content):
    marker = 'datasources "pg_3" {'
    start = content.index(marker)
    status = content.index('    status "active"', start)
    replacement = '\tstatus    "active"    # keep-status-format'
    return content[:status] + replacement + content[status + len('    status "active"'):]


def _wait_pg_cluster_ready(rt, cluster_name, primary, replicas=(), title=None):
    """等待 ACTIVE 触发的即时探测形成可信 cluster 路由投影。"""
    expected = {
        primary: {
            "config_status": "active", "probe_state": "READY",
            "connect_status": "ONLINE", "observed_role": "primary",
            "topology_state": "VALID",
        },
    }
    for replica in replicas:
        expected[replica] = {
            "config_status": "active", "probe_state": "READY",
            "connect_status": "ONLINE", "observed_role": "replica",
            "topology_state": "VALID", "effective_status": "READ_ONLY",
        }
    return rt.wait_node_monitor(
        title or "等待 %s ACTIVE 后 monitor 路由投影收敛" % cluster_name,
        expected, retry_timeout=30)


def _mixed_topology_transform(rt):
    def transform(content):
        content = content.replace(
            'user "postgres" {', groups + '\n' + users + '\nuser "postgres" {', 1)
        content = content.replace('ports "%s"' % rt.listen_port,
                                  'ports "%s,%s"' % (rt.listen_port, rt.read_port), 1)
        content = content.replace('write_port  35101', 'write_port %s' % rt.listen_port, 1)
        content = content.replace('write_port    35102', 'write_port %s' % rt.listen_port, 1)
        return content

    groups = '''
# mixed production-style groups: comments and alignment are intentional
group "mmr_hint_mix" {
    group_mode "mmr"
    storage_db    "postgres"        # shared storage database
    backend_clusters "pg_cluster_1,pg_cluster_2"
    write_cluster "pg_cluster_2"     # current write center
    promoted_cluster    "pg_cluster_1"
    write_port  35101
    check "auto"
}
group "mmr_sql_mix" {
    group_mode "mmr"
    storage_db "postgres"
    backend_clusters "pg_cluster_1,pg_cluster_2"
    write_cluster "pg_cluster_2"
    promoted_cluster "pg_cluster_1"  # same baseline for atomic batch restore
    check "auto"
}
group "rep_port_mix" {
    group_mode "replication"
    storage_db "postgres"
    backend_clusters "pg_cluster_1"
    write_port    35102       # aligned port field
    check "auto"
}
group "balance_read_mix" {
    group_mode "balance"
    storage_db "postgres"
    access_mode "read_only"
    backend_clusters "pg_cluster_1,pg_cluster_2"
    check "auto"
}
group "single_write_mix" {
    group_mode "single"
    storage_db "postgres"
    access_mode    "read_write"   # extra spaces are part of the fixture
    backend_clusters "pg_cluster_1"
    check "auto"
}
'''
    users = '''
user "mix_hint" {
    group_names "mmr_hint_mix"
    authentication "none"
    storage_user "postgres"
    pool "transaction"
    rw_split_method "hint"
}
user "mix_port" {
    group_names "rep_port_mix"
    authentication "none"
    storage_user "postgres"
    pool "transaction"
    rw_split_method "port"
}
user "mix_sql" {
    group_names "mmr_sql_mix"
    authentication "none"
    storage_user "postgres"
    pool "transaction"
    pool_discard no
    pool_reserve_prepared_statement yes
    rw_split_method "sql_parse"
}
user "mix_session" {
    group_names "single_write_mix"
    authentication "none"
    storage_user "postgres"
    pool "session"
    rw_split_method "none"
}
user "mix_balance" {
    group_names "balance_read_mix"
    authentication "none"
    storage_user "postgres"
    pool "transaction"
    rw_split_method "none"
}
'''
    return transform


def _weight_with_format(content):
    marker = 'datasources "pg_3" {'
    start = content.index(marker)
    weight = content.index('    weight 10', start)
    return content[:weight] + '\tweight    10    # keep-weight-format' + content[weight + len('    weight 10'):]


def _add_34_mmr_groups(content):
    blocks = []
    for index in range(1, 35):
        blocks.extend(('group "expand_mmr_%02d" {' % index,
                       '    group_mode "mmr"', '    storage_db "postgres"',
                       '    backend_clusters "pg_cluster_1,pg_cluster_2"',
                       '    write_cluster "pg_cluster_2"',
                       '    promoted_cluster "pg_cluster_1"', '    check "auto"', '}', ''))
    content = content.replace('group "rep_group" {', '\n'.join(blocks) + 'group "rep_group" {', 1)
    group_names = ','.join(['expand_mmr_%02d' % index for index in range(1, 35)])
    return content.replace(
        'group_names "mmr_group,rep_group,balance_group,single_group"',
        'group_names "mmr_group,rep_group,balance_group,single_group,%s"' % group_names,
        1)


def _as_crlf(content):
    return content.replace("\n", "\r\n")


def _inject_after_start(conf, suffix):
    original = conf.read_text(encoding="utf-8")
    conf.write_text(original.rstrip("\n") + "\n" + suffix + "\n", encoding="utf-8")


def _sql_parse_transform(group):
    def transform(content):
        content = _route_user_scope(content, group)
        content = content.replace('    pool_discard no',
                                  '    pool_discard no\n    pool_reserve_prepared_statement yes', 1)
        return content.replace('    rw_split_method "none"',
                               '    rw_split_method "sql_parse"', 1)
    return transform


def _add_groups_without_promoted(content):
    return _add_second_mmr_group(content).replace(
        '    promoted_cluster "pg_cluster_1"\n', '', 2)


def _route_user_scope(content, group):
    return content.replace(
        '    group_names "mmr_group,rep_group,balance_group,single_group"',
        '    group_names "%s"' % group, 1)


def _add_hash_inside_string(content):
    return content.replace('log_syslog_ident "fbasecman"',
                           'log_syslog_ident "fbase#inside-string"', 1)


def _cluster_datasources_have_status(text, status):
    return all('status "%s"' % status in _datasource_block(text, 'cluster_ds_%02d' % i)
               for i in range(1, 31))


