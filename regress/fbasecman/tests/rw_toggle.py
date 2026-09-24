import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from lib.rw_toggle_types import RwToggleCase
from framework.configuration import load_regression_config
from framework.reporting import ReportCheck, ReportDocument, ReportStep, render_report

RW_TOGGLE_CASE_ROOT = "tests/rw_toggle_cases"
RW_TOGGLE_EXPECTED_ROOT = "tests/rw_toggle_cases/expected"
RW_REPORT_STARTED_AT = {}


RW_TOGGLE_CASES = [
    RwToggleCase(
        name="mmr_hint_switch",
        rel_dir="tests/rw_toggle_cases/hint_toggle/mmr",
        script="test_mmr_hint_switch.sh",
        topology="mmr",
        route_mode="hint",
        driver="psql",
        summary="MMR hint 模式下切换读写路由并校验只读/非写主路径",
        batch=0,
        native_kind="hint_switch",
    ),
    RwToggleCase(
        name="mmr_hint_write",
        rel_dir="tests/rw_toggle_cases/hint_toggle/mmr",
        script="test_mmr_hint_write.sh",
        topology="mmr",
        route_mode="hint",
        driver="psql",
        summary="MMR hint 模式下写请求应稳定落到写节点",
        batch=0,
        native_kind="hint_write",
    ),
    RwToggleCase(
        name="mmr_hint_read",
        rel_dir="tests/rw_toggle_cases/hint_toggle/mmr",
        script="test_mmr_hint_read.sh",
        topology="mmr",
        route_mode="hint",
        driver="psql",
        summary="MMR hint 模式下只读请求应稳定落到只读路径",
        batch=0,
        native_kind="hint_read",
    ),
    RwToggleCase(
        name="mmr_hint_jdbc",
        rel_dir="tests/rw_toggle_cases/hint_toggle/mmr",
        script="test_mmr_hint_jdbc.sh",
        topology="mmr",
        route_mode="hint",
        driver="jdbc",
        summary="MMR hint 模式下 JDBC 读写切换行为保持正确",
        batch=0,
        runner="native_hint_jdbc",
        native_kind="hint_jdbc",
        native_driver="jdbc",
    ),
    RwToggleCase(
        name="rep_hint_switch",
        rel_dir="tests/rw_toggle_cases/hint_toggle/replication",
        script="test_rep_hint_switch.sh",
        topology="replication",
        route_mode="hint",
        driver="psql",
        summary="REP hint 模式下切换读写路由并覆盖两个只读节点",
        batch=0,
        native_kind="hint_switch",
    ),
    RwToggleCase(
        name="rep_hint_read",
        rel_dir="tests/rw_toggle_cases/hint_toggle/replication",
        script="test_rep_hint_read.sh",
        topology="replication",
        route_mode="hint",
        driver="psql",
        summary="REP hint 模式下只读请求应稳定分发到只读节点",
        batch=0,
        native_kind="hint_read",
    ),
    RwToggleCase(
        name="rep_hint_write",
        rel_dir="tests/rw_toggle_cases/hint_toggle/replication",
        script="test_rep_hint_write.sh",
        topology="replication",
        route_mode="hint",
        driver="psql",
        summary="REP hint 模式下写请求应稳定落到主节点",
        batch=0,
        native_kind="hint_write",
    ),
    RwToggleCase(
        name="rep_hint_jdbc",
        rel_dir="tests/rw_toggle_cases/hint_toggle/replication",
        script="test_rep_hint_jdbc.sh",
        topology="replication",
        route_mode="hint",
        driver="jdbc",
        summary="REP hint 模式下 JDBC 读写切换行为保持正确",
        batch=0,
        runner="native_hint_jdbc",
        native_kind="hint_jdbc",
        native_driver="jdbc",
    ),
    RwToggleCase(
        name="rep_read_port",
        rel_dir="tests/rw_toggle_cases/port_toggle/replication",
        script="test_rep_read_port.sh",
        topology="replication",
        route_mode="port",
        driver="psql",
        summary="REP 只读端口应覆盖两个只读节点",
        batch=1,
        runner="native_port_psql",
        native_kind="port_read",
    ),
    RwToggleCase(
        name="rep_write_port",
        rel_dir="tests/rw_toggle_cases/port_toggle/replication",
        script="test_rep_write_port.sh",
        topology="replication",
        route_mode="port",
        driver="psql",
        summary="REP 写端口应稳定落到主节点",
        batch=1,
        runner="native_port_psql",
        native_kind="port_write",
    ),
    RwToggleCase(
        name="rep_port_jdbc",
        rel_dir="tests/rw_toggle_cases/port_toggle/replication",
        script="test_rep_port_jdbc.sh",
        topology="replication",
        route_mode="port",
        driver="jdbc",
        summary="REP 端口模式下 JDBC 读写切换行为保持正确",
        batch=1,
    ),
    RwToggleCase(
        name="mmr_read_port",
        rel_dir="tests/rw_toggle_cases/port_toggle/mmr",
        script="test_mmr_read_port.sh",
        topology="mmr",
        route_mode="port",
        driver="psql",
        summary="MMR 只读端口应覆盖非写主与备机路径",
        batch=1,
        runner="native_port_psql",
        native_kind="port_read",
    ),
    RwToggleCase(
        name="mmr_write_port",
        rel_dir="tests/rw_toggle_cases/port_toggle/mmr",
        script="test_mmr_write_port.sh",
        topology="mmr",
        route_mode="port",
        driver="psql",
        summary="MMR 写端口应稳定落到写节点",
        batch=1,
        runner="native_port_psql",
        native_kind="port_write",
    ),
    RwToggleCase(
        name="mmr_port_jdbc",
        rel_dir="tests/rw_toggle_cases/port_toggle/mmr",
        script="test_mmr_port_jdbc.sh",
        topology="mmr",
        route_mode="port",
        driver="jdbc",
        summary="MMR 端口模式下 JDBC 读写切换行为保持正确",
        batch=1,
    ),
]


class RwToggleFailure(RuntimeError):
    pass


def case_items():
    return list(RW_TOGGLE_CASES)


def case_names():
    return [case.name for case in RW_TOGGLE_CASES]


def show():
    lines = ["rw_toggle"]
    for case in RW_TOGGLE_CASES:
        lines.append(
            "  - rw_toggle.%s batch=%s topology=%-11s route=%-5s driver=%-5s report=%s [enabled] %s"
            % (
                case.name,
                case.batch,
                case.topology,
                case.route_mode,
                case.driver,
                case.report_level,
                case.summary,
            )
        )
    lines.append("")
    lines.append("Enabled now: %s / %s" % (len(RW_TOGGLE_CASES), len(RW_TOGGLE_CASES)))
    lines.append("Advanced report level: %s / %s" % (len(RW_TOGGLE_CASES), len(RW_TOGGLE_CASES)))
    return "\n".join(lines)


def _yaml_load(path):
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_config(root):
    """Use the framework loader so rw_toggle has the same local override rules."""
    return load_regression_config(root).config


def load_context(root):
    context_file = root / "output" / "env" / "test_context.yaml"
    if not context_file.exists():
        raise RwToggleFailure("missing %s, run ./run.sh env setup first" % context_file)
    return _yaml_load(context_file)


def _quote_array(values):
    return "(" + " ".join(['"%s"' % value for value in values]) + ")"


def _replacement_map(root, config, context):
    db = config["database"]
    ports = db["ports"]
    local = config["local"]
    fbasecman = config["fbasecman"]
    role = context.get("role_passwords", {})
    sysid = context.get("system_identifiers", {})
    cipher = context.get("ciphertexts", {})
    return {
        "MMR_HOST": db["mmr_host"],
        "REP_HOST": db.get("rep_host", db["mmr_host"]),
        "REP_PORT": str(ports.get("rep_primary", ports["mmr1"])),
        "REP_PG_USER": db.get("rep_pg_user", db["mmr_pg_user"]),
        "REP_S1_PORT": str(ports.get("rep_standby1", ports["mmr1_standby1"])),
        "REP_S2_PORT": str(ports.get("rep_standby2", ports["mmr1_standby2"])),
        "MMR1_PORT": str(ports["mmr1"]),
        "MMR2_PORT": str(ports["mmr2"]),
        "MMR_PG_USER": db["mmr_pg_user"],
        "MMR3_PORT": str(ports["mmr3"]),
        "MMR1_S1_PORT": str(ports["mmr1_standby1"]),
        "MMR2_S1_PORT": str(ports["mmr2_standby1"]),
        "FBASECMAN_IP": "127.0.0.1",
        "FBASECMAN_PORT": str(fbasecman["write_port"]),
        "OTHER_PORT": str(fbasecman["read_port"]),
        "G1_GROUP_UUID": context.get("group_uuid", {}).get("g1", ""),
        "MMR1_U22_ROLEPWD": role.get("mmr1_u22", ""),
        "MMR1_U11_ROLEPWD": role.get("mmr1_u11", ""),
        "MMR1_U3_ROLEPWD": role.get("mmr1_u3", ""),
        "MMR2_U22_ROLEPWD": role.get("mmr2_u22", ""),
        "MMR2_U11_ROLEPWD": role.get("mmr2_u11", ""),
        "MMR2_U3_ROLEPWD": role.get("mmr2_u3", ""),
        "LOG_DIRECTORY": str(root / "output" / "runs" / "rw_toggle" / "_logs"),
        "MMR1_SYSTEM_IDENTIFIER": sysid.get("mmr1", ""),
        "MMR2_SYSTEM_IDENTIFIER": sysid.get("mmr2", ""),
        "MMR3_SYSTEM_IDENTIFIER": sysid.get("mmr3", ""),
        "MMR1_S1_SYSTEM_IDENTIFIER": sysid.get("mmr1_standby1", ""),
        "MMR2_S1_SYSTEM_IDENTIFIER": sysid.get("mmr2_standby1", ""),
        "REP_SYSTEM_IDENTIFIER": sysid.get("rep_primary", ""),
        "REP_S1_SYSTEM_IDENTIFIER": sysid.get("rep_standby1", ""),
        "REP_S2_SYSTEM_IDENTIFIER": sysid.get("rep_standby2", ""),
        "MD5_CIPHERTEXT_USER_1": cipher.get("md5_user_1", ""),
        "MD5_CIPHERTEXT_USER_2": cipher.get("md5_user_2", ""),
        "SCRAM256_CIPHERTEXT_USER_1": cipher.get("scram256_user_1", ""),
        "SCRAM256_CIPHERTEXT_USER_2": cipher.get("scram256_user_2", ""),
    }


def kill_fbasecman():
    cmd = "ps aux | grep -E 'fbasecman.*\\.conf' | grep -v grep | awk '{print $2}'"
    process = subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    stdout, _ = process.communicate()
    pids = [pid.strip() for pid in stdout.splitlines() if pid.strip()]
    if pids:
        subprocess.call(["kill", "-9"] + pids)


def _run_cmd(cmd, cwd=None):
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
    )
    stdout, _ = process.communicate()
    return process.returncode, stdout


def _wait_for_port(port, timeout=15.0):
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        rc, out = _run_cmd(
            [
                "bash",
                "-lc",
                "psql -h localhost -p %s -U postgres -d postgres -c 'select 1' >/dev/null 2>&1" % port,
            ]
        )
        if rc == 0:
            return True, out
        last = out
        time.sleep(0.5)
    return False, last


def _start_fbasecman(root, conf_path):
    fbasecman_bin = load_config(root)["fbasecman"]["fbasecman_bin"]
    return _run_cmd([fbasecman_bin, str(conf_path)], cwd=conf_path.parent)


def _stop_fbasecman(root, conf_path):
    fbasecman_bin = load_config(root)["fbasecman"]["fbasecman_bin"]
    return _run_cmd([fbasecman_bin, str(conf_path), "--stop"], cwd=conf_path.parent)


def _psql_file(root, port, sql_file):
    postgres_dir = load_config(root)["local"]["postgres_dir"]
    cmd = [
        str(Path(postgres_dir) / "bin" / "psql"),
        "-h",
        "localhost",
        "-p",
        str(port),
        "-U",
        "postgres",
        "-d",
        "postgres",
        "-f",
        str(sql_file),
    ]
    return _run_cmd(cmd, cwd=root)


def _detect_port_and_host(output):
    port = None
    host = None
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "port":
            for follow in lines[index + 1 : index + 4]:
                value = follow.strip().strip("|").strip()
                if value.isdigit():
                    port = int(value)
                    break
        if "inet_server_addr" in line:
            for follow in lines[index + 1 : index + 4]:
                value = follow.strip().strip("|").strip()
                if value and value != "------------------":
                    host = value
                    break
    return port, host


def _render_case_conf(root, config, case):
    db = config["database"]
    fbasecman = config["fbasecman"]
    ports = db["ports"]
    log_path = root / "output" / "runs" / "rw_toggle" / "_logs" / ("%s.log" % case.name)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if case.topology == "mmr":
        group_mode = "mmr"
        write_port = ports["mmr1"]
        read_ports = (ports["mmr2"], ports["mmr1_standby1"])
        user_port_mode = "port" if case.route_mode == "port" else "hint"
        datasources = [
            ("pg_220", db["mmr_host"], ports["mmr1"], "mmr_cluster_1", None),
            ("pg_230", db["mmr_host"], ports["mmr2"], "mmr_cluster_2", None),
            ("pg_240", db["mmr_host"], ports["mmr1_standby1"], "mmr_cluster_1", "pg_240"),
        ]
        group_lines = [
            '    storage_db "postgres"',
            '    backend_clusters "mmr_cluster_1,mmr_cluster_2"',
            '    write_cluster "mmr_cluster_1"',
            '    promoted_cluster "mmr_cluster_2"',
        ]
    else:
        group_mode = "replication"
        write_port = ports["mmr1"]
        read_ports = (ports["mmr1_standby1"], ports["mmr1_standby2"])
        user_port_mode = "port" if case.route_mode == "port" else "hint"
        datasources = [
            ("pg_220", db["mmr_host"], ports["mmr1"], "rep_cluster", None),
            ("pg_230", db["mmr_host"], ports["mmr1_standby1"], "rep_cluster", "pg_240"),
            ("pg_240", db["mmr_host"], ports["mmr1_standby2"], "rep_cluster", "pg_241"),
        ]
        group_lines = [
            '    storage_db "postgres"',
            '    backend_clusters "rep_cluster"',
        ]

    lines = [
        'pid_file "/tmp/fbasecman.pid"',
        "daemonize yes",
        'unix_socket_dir "/tmp"',
        'unix_socket_mode "0644"',
        'log_format "%p %t %l [%i %s] (%c) %m\\n"',
        'log_to_stdout no',
        'log_syslog no',
        'log_syslog_ident "odyssey"',
        'log_syslog_facility "daemon"',
        'log_debug yes',
        'log_config yes',
        'log_session yes',
        'log_query yes',
        'log_stats yes',
        'stats_interval 60',
        'promhttp_server_port %s' % (fbasecman["read_port"] + 2),
        'log_general_stats_prom yes',
        'log_route_stats_prom no',
        'workers "auto"',
        'resolvers 1',
        'readahead 8192',
        'cache_coroutine 0',
        'coroutine_stack_size 16',
        'nodelay yes',
        'locks_dir "/tmp/odyssey"',
        'graceful_die_on_errors yes',
        'enable_online_restart no',
        'bindwith_reuseport yes',
        'keepalive 15',
        'keepalive_keep_interval 75',
        'keepalive_probes 9',
        'keepalive_usr_timeout 0',
    ]
    if case.name in ("mmr_hint_read", "mmr_hint_write", "mmr_hint_switch", "mmr_hint_jdbc", "rep_hint_read", "rep_hint_write", "rep_hint_switch", "rep_hint_jdbc"):
        lines.append('heartbeat_request "select 10086"')
    lines.extend([
        'host "*"',
        'ports "%s,%s"' % (fbasecman["read_port"], fbasecman["write_port"]),
        'backlog 128',
        'compression yes',
        '',
        'group "postgres" {',
        '    group_mode "%s"' % group_mode,
    ])
    lines.extend(group_lines)
    lines.append('    check "auto"')
    if case.route_mode == "port":
        lines.append('    write_port %s' % fbasecman["write_port"])
    lines.append('}')
    for name, host, port, cluster_name, application_name in datasources:
        lines.extend([
            '',
            'datasources "%s" {' % name,
            '    host "%s"' % host,
            '    port %s' % port,
            '    cluster_name "%s"' % cluster_name,
            '    application_name "%s"' % application_name if application_name else '',
            '    weight 10',
            '    tls "disable"',
            '}',
        ])
    lines.extend([
        '',
        'user "postgres" {',
        '    group_names  "postgres"',
        '    rw_split_method "%s"' % user_port_mode,
        '    authentication "none"',
        '    storage_user "postgres"',
        '    pool "transaction"',
        '    pool_size 100',
        '    pool "transaction"',
        '    log_debug yes',
        '    pool_discard no',
        '    pool_reserve_prepared_statement yes',
        '    server_lifetime 3600',
        '    quantiles "0.99,0.95,0.5"',
        '    client_max 1007',
        '}',
        '',
        'user "admin" {',
        '    authentication "none"',
        '    pool "session"',
        '    role "admin"',
        '}',
        '',
        'log_file "%s"' % log_path,
        'log_min_messages "%s"' % fbasecman["log_level"],
        'license_dir "%s"' % fbasecman["license_dir"],
    ])
    conf_path = root / "output" / "runs" / "rw_toggle" / case.name / "workdir" / ("%s.conf" % case.name)
    conf_path.parent.mkdir(parents=True, exist_ok=True)
    conf_path.write_text("\n".join(line for line in lines if line) + "\n", encoding="utf-8")
    return conf_path


def _port_case_contract(case, config):
    db = config["database"]
    ports = db["ports"]
    host = db.get("rep_host", db["mmr_host"])
    rep_pri = ports.get("rep_primary", ports["mmr1"])
    rep_s1 = ports.get("rep_standby1", ports["mmr1_standby1"])
    rep_s2 = ports.get("rep_standby2", ports["mmr1_standby2"])
    if case.name == "rep_read_port":
        return {
            "port": config["fbasecman"]["read_port"],
            "sql": "%s/port_toggle/replication/read_port.sql" % RW_TOGGLE_CASE_ROOT,
            "targets": [
                {
                    "label": "standby1",
                    "port": rep_s1,
                    "host": host,
                    "expected": "%s/port/rep_read_node1.out" % RW_TOGGLE_EXPECTED_ROOT,
                },
                {
                    "label": "standby2",
                    "port": rep_s2,
                    "host": host,
                    "expected": "%s/port/rep_read_node2.out" % RW_TOGGLE_EXPECTED_ROOT,
                },
            ],
            "mode": "read",
        }
    if case.name == "rep_write_port":
        return {
            "port": config["fbasecman"]["write_port"],
            "sql": "%s/port_toggle/replication/write_port.sql" % RW_TOGGLE_CASE_ROOT,
            "targets": [
                {
                    "label": "primary",
                    "port": rep_pri,
                    "host": host,
                    "expected": "%s/port/rep_write_node.out" % RW_TOGGLE_EXPECTED_ROOT,
                }
            ],
            "mode": "write",
        }
    if case.name == "mmr_read_port":
        return {
            "port": config["fbasecman"]["read_port"],
            "sql": "%s/port_toggle/mmr/read_port.sql" % RW_TOGGLE_CASE_ROOT,
            "targets": [
                {
                    "label": "non_write_leader",
                    "port": ports["mmr2"],
                    "host": config["database"]["mmr_host"],
                    "expected": "%s/port/mmr_read_node1.out" % RW_TOGGLE_EXPECTED_ROOT,
                },
                {
                    "label": "standby",
                    "port": ports["mmr1_standby1"],
                    "host": config["database"]["mmr_host"],
                    "expected": "%s/port/mmr_read_node2.out" % RW_TOGGLE_EXPECTED_ROOT,
                },
            ],
            "mode": "read",
        }
    if case.name == "mmr_write_port":
        return {
            "port": config["fbasecman"]["write_port"],
            "sql": "%s/port_toggle/mmr/write_port.sql" % RW_TOGGLE_CASE_ROOT,
            "targets": [
                {
                    "label": "write_leader",
                    "port": ports["mmr1"],
                    "host": config["database"]["mmr_host"],
                    "expected": "%s/port/mmr_write_node.out" % RW_TOGGLE_EXPECTED_ROOT,
                }
            ],
            "mode": "write",
        }
    raise RwToggleFailure("unsupported native port case: %s" % case.name)


def _hint_case_contract(case, config):
    db = config["database"]
    ports = db["ports"]
    host = db.get("rep_host", db["mmr_host"])
    rep_pri = ports.get("rep_primary", ports["mmr1"])
    rep_s1 = ports.get("rep_standby1", ports["mmr1_standby1"])
    rep_s2 = ports.get("rep_standby2", ports["mmr1_standby2"])
    if case.name in ("mmr_hint_read", "rep_hint_read"):
        if case.name == "mmr_hint_read":
            return {
                "port": config["fbasecman"]["write_port"],
                "sql": "%s/hint_toggle/mmr/read_test.sql" % RW_TOGGLE_CASE_ROOT,
                "targets": [
                    {"label": "non_write_leader", "port": config["database"]["ports"]["mmr2"], "host": config["database"]["mmr_host"]},
                    {"label": "standby", "port": config["database"]["ports"]["mmr1_standby1"], "host": config["database"]["mmr_host"]},
                ],
                "mode": "read",
            }
        return {
            "port": config["fbasecman"]["write_port"],
            "sql": "%s/hint_toggle/replication/read_test.sql" % RW_TOGGLE_CASE_ROOT,
            "targets": [
                {"label": "standby1", "port": rep_s1, "host": host},
                {"label": "standby2", "port": rep_s2, "host": host},
            ],
            "mode": "read",
        }
    if case.name in ("mmr_hint_write", "rep_hint_write"):
        if case.name == "mmr_hint_write":
            return {
                "port": config["fbasecman"]["write_port"],
                "sql": "%s/hint_toggle/mmr/write_test.sql" % RW_TOGGLE_CASE_ROOT,
                "targets": [
                    {"label": "write_leader", "port": config["database"]["ports"]["mmr1"], "host": config["database"]["mmr_host"]},
                ],
                "mode": "write",
            }
        return {
            "port": config["fbasecman"]["write_port"],
            "sql": "%s/hint_toggle/replication/write_test.sql" % RW_TOGGLE_CASE_ROOT,
            "targets": [
                {"label": "primary", "port": rep_pri, "host": host},
            ],
            "mode": "write",
        }
    if case.name in ("mmr_hint_switch", "rep_hint_switch"):
        if case.name == "mmr_hint_switch":
            return {
                "port": config["fbasecman"]["write_port"],
                "sql": "%s/hint_toggle/mmr/switch_test.sql" % RW_TOGGLE_CASE_ROOT,
                "mode": "switch",
            }
        return {
            "port": config["fbasecman"]["write_port"],
            "sql": "%s/hint_toggle/replication/switch_test.sql" % RW_TOGGLE_CASE_ROOT,
            "mode": "switch",
        }
    if case.name in ("mmr_hint_jdbc", "rep_hint_jdbc"):
        if case.name == "mmr_hint_jdbc":
            return {
                "port": config["fbasecman"]["write_port"],
                "sql": "%s/hint_toggle/mmr/jdbc_test.sql" % RW_TOGGLE_CASE_ROOT,
                "mode": "jdbc",
            }
        return {
            "port": config["fbasecman"]["write_port"],
            "mode": "jdbc",
        }
    raise RwToggleFailure("unsupported native hint case: %s" % case.name)


def _run_native_port_case(root, case, config, run_root):
    contract = _port_case_contract(case, config)
    conf_path = _render_case_conf(root, config, case)
    sql_path = root / contract["sql"]
    steps = []
    input_artifacts = _copy_case_inputs(root, run_root)

    kill_fbasecman()
    rc, output = _start_fbasecman(root, conf_path)
    steps.append({"title": "启动 fbasecman", "output": output.strip() or "<empty>", "rc": rc, "command": str(conf_path)})
    if rc != 0:
        _write_native_report(run_root, case, "FAIL", {"steps": steps, "verification": []}, input_artifacts)
        raise RwToggleFailure("start fbasecman failed for %s" % conf_path)

    ready_port = contract["port"]
    ok, ready_output = _wait_for_port(ready_port)
    steps.append({"title": "等待端口就绪", "port": ready_port, "output": ready_output.strip() or "<empty>", "rc": 0 if ok else 1})
    if not ok:
        _write_native_report(run_root, case, "FAIL", {"steps": steps, "verification": []}, input_artifacts)
        raise RwToggleFailure("%s port %s not ready" % (case.name, ready_port))

    try:
        if contract["mode"] == "read":
            seen = {}
            attempts = []
            for attempt in range(1, 31):
                rc, query_output = _psql_file(root, contract["port"], sql_path)
                port, host = _detect_port_and_host(query_output)
                attempts.append(
                    {
                        "attempt": attempt,
                        "port": port,
                        "host": host,
                        "output": query_output.rstrip(),
                        "rc": rc,
                    }
                )
                if rc == 0:
                    for target in contract["targets"]:
                        if port == target["port"] and host == target["host"]:
                            seen[target["label"]] = {
                                "port": port,
                                "host": host,
                                "output": query_output.rstrip(),
                            }
                if len(seen) == len(contract["targets"]):
                    break
            steps.append({"title": "执行只读端口探测", "attempts": attempts, "seen": seen})
            if len(seen) != len(contract["targets"]):
                missing = [item["label"] for item in contract["targets"] if item["label"] not in seen]
                raise RwToggleFailure("%s did not observe expected read targets: %s" % (case.name, ", ".join(missing)))
            verification = [
                {
                    "title": "只读端口覆盖目标节点",
                    "expected": "读端口请求应覆盖 %s" % " + ".join(item["label"] for item in contract["targets"]),
                    "actual": "\n".join(
                        "%s\n%s" % (label, seen[label]["output"]) for label in sorted(seen)
                    ),
                    "result": "PASS",
                }
            ]
        else:
            rc, query_output = _psql_file(root, contract["port"], sql_path)
            port, host = _detect_port_and_host(query_output)
            steps.append(
                {
                    "title": "执行写端口脚本",
                    "command": str(sql_path),
                    "port": port,
                    "host": host,
                    "output": query_output.rstrip(),
                    "rc": rc,
                }
            )
            target = contract["targets"][0]
            if rc != 0:
                raise RwToggleFailure("%s psql execution failed" % case.name)
            if port != target["port"] or host != target["host"]:
                raise RwToggleFailure("%s did not match expected write target/output" % case.name)
            verification = [
                {
                    "title": "写端口稳定命中目标节点",
                    "expected": "写端口请求应落到 %s" % target["label"],
                    "actual": query_output.rstrip(),
                    "result": "PASS",
                }
            ]
        return {"steps": steps, "verification": verification}
    except Exception as exc:
        _write_native_report(run_root, case, "FAIL", {"steps": steps, "verification": []}, input_artifacts)
        raise
    finally:
        stop_rc, stop_output = _stop_fbasecman(root, conf_path)
        steps.append({"title": "停止 fbasecman", "output": stop_output.strip() or "<empty>", "rc": stop_rc, "command": str(conf_path)})
        kill_fbasecman()


def _run_native_hint_case(root, case, config, run_root):
    contract = _hint_case_contract(case, config)
    conf_path = _render_case_conf(root, config, case)
    sql_path = root / contract["sql"]
    steps = []
    input_artifacts = _copy_case_inputs(root, run_root)

    kill_fbasecman()
    rc, output = _start_fbasecman(root, conf_path)
    steps.append({"title": "启动 fbasecman", "output": output.strip() or "<empty>", "rc": rc, "command": str(conf_path)})
    if rc != 0:
        _write_native_report(run_root, case, "FAIL", {"steps": steps, "verification": []}, input_artifacts)
        raise RwToggleFailure("start fbasecman failed for %s" % conf_path)

    ready_port = config["fbasecman"]["write_port"]
    ok, ready_output = _wait_for_port(ready_port)
    steps.append({"title": "等待端口就绪", "port": ready_port, "output": ready_output.strip() or "<empty>", "rc": 0 if ok else 1})
    if not ok:
        _write_native_report(run_root, case, "FAIL", {"steps": steps, "verification": []}, input_artifacts)
        raise RwToggleFailure("%s port %s not ready" % (case.name, ready_port))

    try:
        if contract["mode"] in ("read", "write"):
            rc, query_output = _psql_file(root, ready_port, sql_path)
            port, host = _detect_port_and_host(query_output)
            steps.append({"title": "执行 hint 脚本", "command": str(sql_path), "port": port, "host": host, "output": query_output.rstrip(), "rc": rc})
            target = contract["targets"][0]
            if rc != 0:
                raise RwToggleFailure("%s psql execution failed" % case.name)
            if port != target["port"] or host != target["host"]:
                raise RwToggleFailure("%s did not match expected hint target/output" % case.name)
            verification = [
                {
                    "title": "hint 路由稳定命中目标节点",
                    "expected": "hint 请求应落到 %s" % target["label"],
                    "actual": query_output.rstrip(),
                    "result": "PASS",
                }
            ]
        elif contract["mode"] == "switch":
            rc, query_output = _psql_file(root, ready_port, sql_path)
            steps.append({"title": "执行 hint switch 脚本", "command": str(sql_path), "output": query_output.rstrip(), "rc": rc})
            if rc != 0:
                raise RwToggleFailure("%s switch script failed" % case.name)
            verification = [
                {
                    "title": "hint switch 脚本执行成功",
                    "expected": "hint switch 脚本成功完成",
                    "actual": query_output.rstrip(),
                    "result": "PASS",
                }
            ]
        else:
            rc, query_output = _psql_file(root, ready_port, sql_path)
            steps.append({"title": "执行 hint jdbc 脚本", "command": str(sql_path), "output": query_output.rstrip(), "rc": rc})
            if rc != 0:
                raise RwToggleFailure("%s jdbc script failed" % case.name)
            verification = [
                {
                    "title": "hint jdbc 脚本执行成功",
                    "expected": "hint jdbc 脚本成功完成",
                    "actual": query_output.rstrip(),
                    "result": "PASS",
                }
            ]
        return {"steps": steps, "verification": verification}
    except Exception:
        _write_native_report(run_root, case, "FAIL", {"steps": steps, "verification": []}, input_artifacts)
        raise
    finally:
        stop_rc, stop_output = _stop_fbasecman(root, conf_path)
        steps.append({"title": "停止 fbasecman", "output": stop_output.strip() or "<empty>", "rc": stop_rc, "command": str(conf_path)})
        kill_fbasecman()


def _run_native_hint_jdbc_case(root, case, config, run_root):
    contract = _hint_case_contract(case, config)
    conf_path = _render_case_conf(root, config, case)
    steps = []
    input_artifacts = _copy_case_inputs(root, run_root)

    kill_fbasecman()
    rc, output = _start_fbasecman(root, conf_path)
    steps.append({"title": "启动 fbasecman", "output": output.strip() or "<empty>", "rc": rc, "command": str(conf_path)})
    if rc != 0:
        _write_native_report(run_root, case, "FAIL", {"steps": steps, "verification": []}, input_artifacts)
        raise RwToggleFailure("start fbasecman failed for %s" % conf_path)

    ready_port = config["fbasecman"]["write_port"]
    ok, ready_output = _wait_for_port(ready_port)
    steps.append({"title": "等待端口就绪", "port": ready_port, "output": ready_output.strip() or "<empty>", "rc": 0 if ok else 1})
    if not ok:
        _write_native_report(run_root, case, "FAIL", {"steps": steps, "verification": []}, input_artifacts)
        raise RwToggleFailure("%s port %s not ready" % (case.name, ready_port))

    try:
        if case.name == "mmr_hint_jdbc":
            sql_path = root / "tests" / "rw_toggle_cases" / "hint_toggle" / "mmr" / "jdbc_test.sql"
            rc, query_output = _psql_file(root, ready_port, sql_path)
            steps.append({"title": "执行 hint jdbc 脚本", "command": str(sql_path), "output": query_output.rstrip(), "rc": rc})
            if rc != 0:
                raise RwToggleFailure("%s jdbc script failed" % case.name)
            verification = [
                {
                    "title": "hint jdbc 脚本执行成功",
                    "expected": "hint jdbc 脚本成功完成",
                    "actual": query_output.rstrip(),
                    "result": "PASS",
                }
            ]
        else:
            script_path = root / "tests" / "rw_toggle_cases" / "hint_toggle" / "replication" / "test_rep_hint_jdbc.sh"
            rc, query_output = _run_cmd(["bash", str(script_path)], cwd=script_path.parent)
            steps.append({"title": "执行 replication hint jdbc 脚本", "command": str(script_path), "output": query_output.rstrip(), "rc": rc})
            if rc != 0:
                raise RwToggleFailure("%s jdbc script failed" % case.name)
            verification = [
                {
                    "title": "replication hint jdbc 脚本执行成功",
                    "expected": "replication hint jdbc 脚本成功完成",
                    "actual": query_output.rstrip(),
                    "result": "PASS",
                }
            ]
        return {"steps": steps, "verification": verification}
    except Exception:
        _write_native_report(run_root, case, "FAIL", {"steps": steps, "verification": []}, input_artifacts)
        raise
    finally:
        stop_rc, stop_output = _stop_fbasecman(root, conf_path)
        steps.append({"title": "停止 fbasecman", "output": stop_output.strip() or "<empty>", "rc": stop_rc, "command": str(conf_path)})
        kill_fbasecman()


def _jdbc_version_number(version):
    major, minor, patch = [int(item) for item in version.split(".")]
    return major * 10000 + minor * 100 + patch


def _pg_log_dirs(config):
    db = config["database"]
    return [
        Path(db["mmr_postgres_dir"]) / "test_mmr1" / "pg_log",
        Path(db["mmr_postgres_dir"]) / "test_mmr2" / "pg_log",
        Path(db["mmr_postgres_dir"]) / "test_mmr3" / "pg_log",
        Path(db["mmr_postgres_dir"]) / "test_mmr1_s1" / "pg_log",
        Path(db["mmr_postgres_dir"]) / "test_mmr2_s1" / "pg_log",
        Path(db["rep_postgres_dir"]) / "test_rep" / "pg_log",
        Path(db["rep_postgres_dir"]) / "test_rep_s1" / "pg_log",
        Path(db["rep_postgres_dir"]) / "test_rep_s2" / "pg_log",
    ]


def _collect_pg_log_hits(config, needle):
    checked = []
    matched = []
    for directory in _pg_log_dirs(config):
        checked.append(str(directory))
        if directory.exists():
            for log_file in sorted(directory.rglob("*")):
                if log_file.is_file():
                    try:
                        content = log_file.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        continue
                    if needle.lower() in content.lower():
                        matched.append(str(log_file))
    return checked, matched


def _validate_jdbc_output(output, required, forbidden=()):
    missing = [text for text in required if text not in output]
    present = [text for text in forbidden if text in output]
    if missing or present:
        details = []
        if missing:
            details.append("missing: %s" % ", ".join(missing))
        if present:
            details.append("unexpected: %s" % ", ".join(present))
        return False, "; ".join(details)
    return True, ""


def _run_rep_jdbc_version_case(root, case, config, run_root, version, java_class):
    jdbc_jar = root / "lib_jdbc" / ("postgresql-%s.jar" % version)
    workdir = run_root / "workdir" / "rep_hint_jdbc" / version
    workdir.mkdir(parents=True, exist_ok=True)
    (run_root / "result").mkdir(parents=True, exist_ok=True)
    output_path = run_root / "result" / ("rep_hint_jdbc_%s.out" % _jdbc_version_number(version))
    source_path = root / "tests" / "rw_toggle_cases" / "hint_toggle" / "replication" / ("%s.java" % java_class)
    javac_cmd = ["javac", "-cp", ".:%s" % jdbc_jar, str(source_path)]
    db = config["database"]
    ports = db["ports"]
    host = db.get("rep_host", db["mmr_host"])
    rep_pri = ports.get("rep_primary", ports["mmr1"])
    rep_s1 = ports.get("rep_standby1", ports["mmr1_standby1"])
    rep_s2 = ports.get("rep_standby2", ports["mmr1_standby2"])
    java_cmd = [
        "java",
        "-cp",
        ".:%s" % jdbc_jar,
        java_class,
        "%s:%s" % (host, rep_s1),
        "%s:%s" % (host, rep_s2),
        "%s:%s" % (host, rep_pri),
        "jdbc:postgresql://localhost:%s/postgres?prepareThreshold=1" % config["fbasecman"]["write_port"],
        "postgres",
        "",
    ]
    compile_rc, compile_out = _run_cmd(javac_cmd, cwd=source_path.parent)
    if compile_rc != 0:
        raise RwToggleFailure("javac failed for %s" % java_class)
    rc, out = _run_cmd(java_cmd, cwd=source_path.parent)
    output_path.write_text(out, encoding="utf-8")
    if rc != 0:
        raise RwToggleFailure("java failed for %s" % java_class)
    checked, matched = _collect_pg_log_hits(config, "select 10086")
    actual = out.rstrip()
    expected = (
        "JDBC %s 必须完成读写切换与 heartbeat；读请求落在 REP 备库，"
        "写请求落在主库，且输出不含异常。" % version
    )
    write_target = "%s:%s" % (host, rep_pri)
    ok, reason = _validate_jdbc_output(
        actual,
        ("读节点切换成功", "写节点切换成功", write_target,
         "=== heartTest start ===", "=== heartTest end ===", "query result: 10086"),
        ("Exception", "ERROR", "未处于正确", "服务端连接不同"),
    )
    if not ok:
        raise RwToggleFailure("%s JDBC %s validation failed: %s" % (case.name, version, reason))
    steps = [
        {"title": "编译 JDBC driver", "command": " ".join(javac_cmd), "output": compile_out.strip() or "<empty>", "rc": compile_rc},
        {"title": "执行 JDBC driver", "command": " ".join(java_cmd), "output": out.strip() or "<empty>", "rc": rc},
    ]
    verification = [
        {
            "title": "JDBC 输出与期望一致",
            "expected": expected,
            "actual": actual,
            "result": "PASS",
        },
        {
            "title": "PG 日志检查",
            "expected": "检查相关 pg_log 目录，确认心跳 SQL 轨迹符合预期",
            "actual": "checked=%s\nmatched=%s" % ("\n".join(checked), "\n".join(matched) if matched else "<none>"),
            "result": "PASS",
        },
    ]
    return {"steps": steps, "verification": verification}


def _run_native_hint_switch_case(root, case, config, run_root):
    return _run_native_hint_case(root, case, config, run_root)


def _run_native_port_jdbc_case(root, case, config, run_root):
    input_artifacts = _copy_case_inputs(root, run_root)
    steps = []
    conf_path = _render_case_conf(root, config, case)
    if case.topology == "mmr":
        java_class = "test_mmr_write_port" if case.name == "mmr_write_port" else "test_mmr_read_port"
        java_source = root / "tests" / "rw_toggle_cases" / "port_toggle" / "mmr" / ("%s.java" % java_class)
        jdbc_args = [
            "jdbc:postgresql://localhost:%s/postgres?prepareThreshold=1" % config["fbasecman"]["read_port"],
            "postgres",
            "",
        ]
    else:
        java_class = "test_rep_write_port" if case.name == "rep_write_port" else "test_rep_read_port"
        java_source = root / "tests" / "rw_toggle_cases" / "port_toggle" / "replication" / ("%s.java" % java_class)
        jdbc_args = [
            "jdbc:postgresql://localhost:%s/postgres?prepareThreshold=1" % config["fbasecman"]["read_port"],
            "postgres",
            "",
        ]
    kill_fbasecman()
    rc, output = _start_fbasecman(root, conf_path)
    steps.append({"title": "启动 fbasecman", "command": str(conf_path), "output": output.strip() or "<empty>", "rc": rc})
    if rc != 0:
        _write_native_report(run_root, case, "FAIL", {"steps": steps, "verification": []}, input_artifacts)
        raise RwToggleFailure("start fbasecman failed for %s" % conf_path)
    ready_port = config["fbasecman"]["write_port"] if case.route_mode == "port" else config["fbasecman"]["read_port"]
    ok, ready_output = _wait_for_port(ready_port)
    steps.append({"title": "等待端口就绪", "port": ready_port, "output": ready_output.strip() or "<empty>", "rc": 0 if ok else 1})
    if not ok:
        _write_native_report(run_root, case, "FAIL", {"steps": steps, "verification": []}, input_artifacts)
        raise RwToggleFailure("%s port %s not ready" % (case.name, ready_port))
    try:
        jdbc_jar = root / "lib_jdbc" / ("postgresql-%s.jar" % load_config(root)["local"]["jdbc_versions"][0])
        compile_rc, compile_out = _run_cmd(["javac", "-cp", ".:%s" % jdbc_jar, str(java_source)], cwd=java_source.parent)
        steps.append({"title": "编译 JDBC driver", "command": "javac -cp .:%s %s" % (jdbc_jar, java_source), "output": compile_out.strip() or "<empty>", "rc": compile_rc})
        if compile_rc != 0:
            raise RwToggleFailure("javac failed for %s" % java_class)
        java_cmd = ["java", "-cp", ".:%s" % jdbc_jar, java_class] + jdbc_args
        ports = config["database"]["ports"]
        expected_ports = (
            (ports["mmr1_standby1"], ports["mmr2"])
            if case.topology == "mmr"
            else (ports.get("rep_standby1", ports["mmr1_standby1"]),
                  ports.get("rep_standby2", ports["mmr1_standby2"]))
        )
        seen = {}
        attempts = []
        for attempt in range(1, 31):
            rc, out = _run_cmd(java_cmd, cwd=java_source.parent)
            actual = out.rstrip()
            steps.append({"title": "执行 JDBC driver", "command": " ".join(java_cmd),
                          "output": actual or "<empty>", "rc": rc})
            if rc != 0:
                raise RwToggleFailure("java failed for %s" % java_class)
            ok, reason = _validate_jdbc_output(
                actual, ("pg_is_in_recovery: true",),
                ("Exception", "ERROR", "未处于正确"),
            )
            matched = [port for port in expected_ports if
                       "port: %s" % port in actual or "PORT: %s" % port in actual]
            attempts.append({"attempt": attempt, "matched": matched, "reason": reason,
                             "output": actual})
            if not ok or len(matched) != 1:
                raise RwToggleFailure("%s JDBC read output is invalid: %s" % (case.name, reason or actual))
            seen[matched[0]] = actual
            if len(seen) == len(expected_ports):
                break
        if len(seen) != len(expected_ports):
            raise RwToggleFailure("%s did not observe expected JDBC read ports: %s" % (
                case.name, ", ".join(str(port) for port in expected_ports if port not in seen),
            ))
        verification = [{
            "title": "JDBC 只读端口覆盖目标节点",
            "expected": "JDBC 读请求应覆盖 %s" % " + ".join(str(port) for port in expected_ports),
            "actual": "\n".join("port %s\n%s" % (port, seen[port]) for port in sorted(seen)),
            "result": "PASS",
        }]
        return {"steps": steps, "verification": verification}
    finally:
        stop_rc, stop_output = _stop_fbasecman(root, conf_path)
        steps.append({"title": "停止 fbasecman", "command": str(conf_path), "output": stop_output.strip() or "<empty>", "rc": stop_rc})
        kill_fbasecman()


def _run_rep_hint_jdbc_case(root, case, config, run_root):
    input_artifacts = _copy_case_inputs(root, run_root)
    steps = []
    conf_path = _render_case_conf(root, config, case)
    kill_fbasecman()
    rc, output = _start_fbasecman(root, conf_path)
    steps.append({"title": "启动 fbasecman", "output": output.strip() or "<empty>", "rc": rc, "command": str(conf_path)})
    if rc != 0:
        _write_native_report(run_root, case, "FAIL", {"steps": steps, "verification": []}, input_artifacts)
        raise RwToggleFailure("start fbasecman failed for %s" % conf_path)
    ready_port = config["fbasecman"]["write_port"]
    ok, ready_output = _wait_for_port(ready_port)
    steps.append({"title": "等待端口就绪", "port": ready_port, "output": ready_output.strip() or "<empty>", "rc": 0 if ok else 1})
    if not ok:
        _write_native_report(run_root, case, "FAIL", {"steps": steps, "verification": []}, input_artifacts)
        raise RwToggleFailure("%s port %s not ready" % (case.name, ready_port))
    try:
        versions = list(load_config(root)["local"]["jdbc_versions"])
        outputs = []
        for version in versions:
            java_class = "test_rep_hint_high_version" if _jdbc_version_number(version) > 420208 else "test_rep_hint_low_version"
            details = _run_rep_jdbc_version_case(root, case, config, run_root, version, java_class)
            outputs.append((version, details))
        verification = []
        for version, details in outputs:
            verification.append(
                {
                    "title": "JDBC %s 输出与期望一致" % version,
                    "expected": details["verification"][0]["expected"],
                    "actual": details["verification"][0]["actual"],
                    "result": details["verification"][0]["result"],
                }
            )
            verification.append(
                {
                    "title": "JDBC %s PG 日志检查" % version,
                    "expected": details["verification"][1]["expected"],
                    "actual": details["verification"][1]["actual"],
                    "result": details["verification"][1]["result"],
                }
            )
        steps.append({"title": "执行 JDBC driver", "output": "versions=%s" % ", ".join(versions), "rc": 0})
        return {"steps": steps, "verification": verification}
    finally:
        stop_rc, stop_output = _stop_fbasecman(root, conf_path)
        steps.append({"title": "停止 fbasecman", "output": stop_output.strip() or "<empty>", "rc": stop_rc, "command": str(conf_path)})
        kill_fbasecman()


def _copy_result_artifacts(root, run_root):
    source = root / "result"
    if not source.exists():
        return []
    target = run_root / "result"
    if target.exists():
        shutil.rmtree(str(target))
    shutil.copytree(str(source), str(target))
    return [str(path.relative_to(run_root)) for path in sorted(target.rglob("*")) if path.is_file()]


def _copy_case_inputs(root, run_root):
    copied = []
    input_root = run_root / "inputs"
    input_root.mkdir(parents=True, exist_ok=True)

    file_targets = [
        root / "regress.yaml",
    ]
    for source in file_targets:
        if not source.exists():
            continue
        target = input_root / source.name
        shutil.copy2(str(source), str(target))
        copied.append(str(target.relative_to(run_root)))

    dir_targets = [
        (root / "tests" / "rw_toggle_cases", input_root / "rw_toggle_cases"),
    ]
    for source, target in dir_targets:
        if not source.exists():
            continue
        if target.exists():
            shutil.rmtree(str(target))
        shutil.copytree(str(source), str(target))
        copied.extend(str(path.relative_to(run_root)) for path in sorted(target.rglob("*")) if path.is_file())

    return copied


def _write_native_report(run_root, case, status, details, input_artifacts):
    report_path = run_root / "report.txt"
    steps = []
    for step in details.get("steps", []):
        step_details = []
        if "command" in step:
            step_details.append(("命令", step["command"]))
        if "attempts" in step:
            attempt_lines = []
            for attempt in step["attempts"]:
                attempt_lines.append("第 %d 次: port=%s host=%s matched=%s rc=%s" % (
                    attempt["attempt"],
                    attempt.get("port"),
                    attempt.get("host"),
                    attempt.get("matched_expected") or "<none>",
                    attempt.get("rc"),
                ))
            if step.get("seen"):
                attempt_lines.append("命中目标:")
                for label in sorted(step["seen"]):
                    attempt_lines.append("  %s" % label)
                    for raw in step["seen"][label]["output"].splitlines():
                        attempt_lines.append("    %s" % raw)
            step_details.append(("执行记录", "\n".join(attempt_lines)))
        else:
            if "port" in step:
                step_details.append(("端口", step["port"]))
            if "host" in step:
                step_details.append(("主机", step["host"]))
            if "output" in step:
                step_details.append(("实际输出", step["output"] or "<empty>"))
            if "rc" in step:
                step_details.append(("退出码", step["rc"]))
        steps.append(ReportStep(step["title"], details=step_details))

    checks = [
        ReportCheck(item["title"], item["expected"], item["actual"], item["result"])
        for item in details.get("verification", [])
    ]
    if checks:
        steps.append(ReportStep("验证读写路由结果", checks=checks))

    normalized_status = "PASS" if status == "SUCCESS" else status
    started_at = RW_REPORT_STARTED_AT.get(str(run_root), datetime.now())
    document = ReportDocument(
        target="rw_toggle.%s" % case.name,
        status=normalized_status,
        started_at=started_at.strftime("%Y-%m-%d %H:%M:%S"),
        finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        purpose=case.summary,
        config_lines=[
            "topology=%s route=%s driver=%s batch=%s runner=%s"
            % (case.topology, case.route_mode, case.driver, case.batch, case.runner)
        ],
        overview_steps=[step["title"] for step in details.get("steps", [])],
        steps=steps,
        pass_reason="关键步骤与读写路由检测项均通过。" if normalized_status == "PASS" else None,
        failure_reason="原生测试步骤未完成。" if normalized_status == "FAIL" else None,
    )
    report_path.write_text(render_report(document), encoding="utf-8")


def run_case(root, case, config, context):
    run_root = root / "output" / "runs" / "rw_toggle" / case.name
    if run_root.exists():
        shutil.rmtree(str(run_root))
    run_root.mkdir(parents=True, exist_ok=True)
    RW_REPORT_STARTED_AT[str(run_root)] = datetime.now()
    input_artifacts = _copy_case_inputs(root, run_root)

    if case.runner == "native_port_psql":
        details = _run_native_port_case(root, case, config, run_root)
        _write_native_report(run_root, case, "SUCCESS", details, input_artifacts)
        return
    if case.name in ("mmr_port_jdbc", "rep_port_jdbc"):
        details = _run_native_port_jdbc_case(root, case, config, run_root)
        _write_native_report(run_root, case, "SUCCESS", details, input_artifacts)
        return
    if case.native_kind in ("hint_read", "hint_write"):
        details = _run_native_hint_case(root, case, config, run_root)
        _write_native_report(run_root, case, "SUCCESS", details, input_artifacts)
        return
    if case.name == "rep_hint_jdbc":
        details = _run_rep_hint_jdbc_case(root, case, config, run_root)
        _write_native_report(run_root, case, "SUCCESS", details, input_artifacts)
        return
    if case.native_kind in ("hint_switch", "hint_jdbc"):
        details = _run_native_hint_case(root, case, config, run_root)
        _write_native_report(run_root, case, "SUCCESS", details, input_artifacts)
        return
    raise AssertionError("unsupported rw_toggle runner")


def _select_cases(target=None):
    if target is None:
        return list(RW_TOGGLE_CASES)
    normalized = target
    if normalized.startswith("test_"):
        normalized = normalized[5:]
    selected = [
        case for case in RW_TOGGLE_CASES if case.name == normalized or case.script == target or case.script[:-3] == target
    ]
    if not selected:
        raise RwToggleFailure("unknown rw_toggle case: %s\navailable cases:\n  %s" % (target, "\n  ".join(case_names())))
    return selected


def run(root, target=None):
    config = load_config(root)
    context = load_context(root)
    selected_cases = _select_cases(target)
    print("------------------------rw_toggle---------------------------------")
    success = 0
    failed = []
    for case in selected_cases:
        try:
            print("rw_toggle.%s ... " % case.name, end="")
            run_case(root, case, config, context)
            success += 1
            print("SUCCESS")
        except RwToggleFailure as exc:
            failed.append("rw_toggle.%s: %s" % (case.name, exc))
            print("FAIL")
            print("    reason: %s" % exc)
    print("-------------------------------------------------------------------------------------")
    print("Total:")
    print("        SUCCESS:%d" % success)
    print("        FAIL:%d" % len(failed))
    if failed:
        raise RwToggleFailure("rw_toggle failures:\n  - " + "\n  - ".join(failed))
