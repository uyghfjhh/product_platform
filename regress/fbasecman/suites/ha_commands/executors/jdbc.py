"""HA console command executors: JDBC."""

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
from suites.ha_commands.helpers import *

__all__ = ['_run_jdbc_console_ha_commands']


def _run_jdbc_console_ha_commands(rt):
    conf = rt.start(transform=_without_promoted)
    before = conf.read_bytes()
    jar = rt.root / rt.env.config["local"]["jdbc_lib_dir"] / "postgresql-42.7.7.jar"
    source = rt.root / "suites" / "ha_commands" / "assets" / "jdbc" / "HaConsoleCommands.java"
    if not jar.exists() or not source.exists():
        raise HaCommandFailure("missing JDBC asset or jar: %s %s" % (source, jar))
    rt.run_command(
        ["javac", "-cp", str(jar), "-d", str(rt.workdir), str(source)],
        rt.logs_dir / "HaConsoleCommands.javac.log", cwd=rt.workdir,
        step_title="编译 JDBC 控制台高可用命令 driver")
    jdbc_url = (
        "jdbc:postgresql://127.0.0.1:%s/console?preferQueryMode=simple" %
        rt.listen_port)
    business_url = (
        "jdbc:postgresql://127.0.0.1:%s/mmr_group?preferQueryMode=simple" %
        rt.listen_port)
    single_url = (
        "jdbc:postgresql://127.0.0.1:%s/single_group?preferQueryMode=simple" %
        rt.listen_port)
    snapshots = rt.workdir / "jdbc-config-snapshots"
    ports = rt.env.config["database"]["ports"]
    _, output = rt.run_command(
        ["java", "-cp", "%s:%s" % (rt.workdir, jar),
         "HaConsoleCommands", jdbc_url, "admin", "", str(conf),
         str(snapshots), business_url, single_url, str(ports["mmr1"]),
         str(ports["mmr2"]), str(ports["mmr1_standby1"])],
        rt.logs_dir / "HaConsoleCommands.log", cwd=rt.workdir,
        step_title="通过 JDBC 控制台执行全部高可用命令")
    markers = (
        "JDBC_CONNECT=OK", "SET_NODE_PARTED=OK", "SET_NODE_ACTIVE=OK",
        "SET_NODE_WEIGHT=OK", "SET_NODE_WRITE=OK", "SET_NODE_PROMOTED=OK",
        "SET_CLUSTER_PARTED=OK", "SET_CLUSTER_ACTIVE=OK",
        "REFRESH_CLUSTER=OK", "ALL_HA_COMMANDS=OK",
        "PARTED_FALLS_BACK_TO_PRIMARY=%s" % ports["mmr1"],
        # Depending on route-cache convergence, ACTIVE may select the restored
        # replica or the valid write-leader fallback.
        "ZERO_WEIGHT_FALLS_BACK_TO_PRIMARY=%s" % ports["mmr1"],
        "WRITE_ROUTE_CLUSTER_1=%s" % ports["mmr1"],
        "PROMOTED_KEEPS_WRITE_ROUTE=%s" % ports["mmr2"],
        "WRITE_ROUTE_CLUSTER_2=%s" % ports["mmr2"],
        "PARTED_ROUTE_PROMOTED_CLUSTER=%s" % ports["mmr1"],
        "ACTIVE_ROUTE_WRITE_CLUSTER=%s" % ports["mmr2"],
    )
    def fields(snapshot):
        objects, _ = rt._semantic_objects(
            (snapshots / snapshot).read_text(encoding="utf-8"))
        return objects

    checks = (
        ("01_node_parted.conf", ("datasources", "pg_3"), "status", '"parted"'),
        ("02_node_active.conf", ("datasources", "pg_3"), "status", '"active"'),
        ("03_weight_0.conf", ("datasources", "pg_3"), "weight", "0"),
        ("04_weight_10.conf", ("datasources", "pg_3"), "weight", "10"),
        ("05_promoted_cluster_1.conf", ("group", "mmr_group"), "promoted_cluster", '"pg_cluster_1"'),
        ("06_write_cluster_1.conf", ("group", "mmr_group"), "write_cluster", '"pg_cluster_1"'),
        ("07_write_cluster_2.conf", ("group", "mmr_group"), "write_cluster", '"pg_cluster_2"'),
        ("08_cluster_2_parted.conf", ("datasources", "pg_2"), "status", '"parted"'),
        ("08_cluster_2_parted.conf", ("datasources", "pg_4"), "status", '"parted"'),
        ("09_cluster_2_active.conf", ("datasources", "pg_2"), "status", '"active"'),
        ("09_cluster_2_active.conf", ("datasources", "pg_4"), "status", '"active"'),
    )
    check_results = []
    for snapshot, identity, field, expected in checks:
        actual = fields(snapshot).get(identity, {}).get(field)
        check_results.append((snapshot, identity, field, expected, actual))
    for marker_name in ("ACTIVE_RESTORES_SINGLE_ROUTE", "WEIGHT_RESTORES_SINGLE_ROUTE"):
        rt.check("验证 %s 路由结果" % marker_name,
                 "结果为 pg_cluster_1 的 primary 或 standby",
                 "marker output checked",
                 any((marker_name + "=%s" % port) in output
                     for port in (ports["mmr1"], ports["mmr1_standby1"])))
    rt.check(
        "验证每条 JDBC 持久化命令的配置快照",
        "各阶段只读快照中的 datasource/group 目标字段均为命令期望值",
        "\n".join("%s %s.%s expected=%s actual=%s" %
                  (item[0], item[1], item[2], item[3], item[4])
                  for item in check_results),
        all(item[3] == item[4] for item in check_results))
    rt.check(
        "验证 REFRESH CLUSTER 不修改配置",
        "REFRESH 前后配置字节完全一致",
        "refresh_config_unchanged=%s" %
        ((snapshots / "09_cluster_2_active.conf").read_bytes() ==
         (snapshots / "10_after_refresh.conf").read_bytes()),
        (snapshots / "09_cluster_2_active.conf").read_bytes() ==
        (snapshots / "10_after_refresh.conf").read_bytes())
    final_objects, _ = rt._semantic_objects(conf.read_text(encoding="utf-8"))
    initial_objects, _ = rt._semantic_objects(before.decode("utf-8"))
    initial_objects[("group", "mmr_group")]["promoted_cluster"] = '"pg_cluster_1"'
    rt.check(
        "验证 JDBC 高可用命令最终配置语义",
        "往返字段均恢复，仅保留 PROMOTED 命令新增的 promoted_cluster",
        "final_semantics_expected=%s" % (final_objects == initial_objects),
        final_objects == initial_objects)
    rt.check(
        "验证 JDBC 控制台命令及实际路由结果",
        "全部命令执行、内存状态和实际 backend 端口均符合预期",
        output, all(marker in output for marker in markers))


