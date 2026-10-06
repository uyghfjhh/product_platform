"""fbasecman ha_commands 套件的原生迁移层。

``HaRuntime`` 把 legacy ``FbasecmanCaseRuntime`` 的用例 API
（start/psql/psql_error/psql_business/psql_monitor/diff/备份断言/
配置语义 diff 闸门/远程节点停起）逐语义映射到平台 ``CaseContext``
原语；下方 ``exec_*`` 函数体与 legacy ``suites/ha_commands/executors/*``
逐句对齐，只替换运行时装配，不改断言文本与判定条件。
"""

from __future__ import annotations

import difflib
import re
import shlex
import time
from pathlib import Path

from platform_regress.sdk import Blocked, CaseContext
from platform_regress.clients.psql import assert_table_rows

from products.fbasecman.native import (
    render_config, _console_query, _console_expect, _business_query,
    _expect, _wait_mmr_routing)


# ---------------------------------------------------------------------------
# 配置语义 diff（复用平台层 platform_regress.evidence.config_diff）
# ---------------------------------------------------------------------------

from platform_regress.evidence.config_diff import (
    semantic_config_diff as _semantic_config_diff,
)


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


# ---------------------------------------------------------------------------
# HaRuntime：legacy rt.* API 到 CaseContext 的逐语义适配
# ---------------------------------------------------------------------------

class HaRuntime:
    """ha_commands 用例运行时（原生版）。

    生命周期由平台托管：``start`` 渲染配置（可选 transform）→
    ``context.start_process`` 就绪探针 → ``_wait_mmr_routing`` 收敛等待；
    每条 console 命令后自动做配置语义 diff 白名单校验（与 legacy 一致）。
    """

    def __init__(self, context: CaseContext):
        self.context = context
        self.env = context.environment
        self.workdir = context.output_dir
        self.psql_bin = self.env.get("psql_bin", "/usr/bin/psql")
        self.binary = self.env.get("fbasecman_bin")
        self.conf = None
        self.port = int(self.env.get("proxy_port", 0))
        self.proxy_log = None
        self._proc = None
        self._seq = 0

    def _key(self):
        self._seq += 1
        return "ha-%02d" % self._seq

    # -- 进程与配置 ------------------------------------------------------

    def start(self, transform=None):
        """渲染配置并启动 fbasecman；同一用例内重复调用先停旧进程。"""
        config = self.workdir / "fbasecman.conf"
        self._stop_existing()
        port = render_config(self.context, config, mode="none",
                             transform=transform)
        self.port = port
        self.conf = config
        self.proxy_log = config.with_suffix(".log")
        if self.proxy_log.exists():
            self.proxy_log.unlink()
        self._proc = self.context.start_process(
            [self.binary, str(config)],
            ready_host=self.context.environment.get("local_host", "127.0.0.1"), ready_port=port, timeout_seconds=90)
        _wait_mmr_routing(self.context, port, self.psql_bin)
        self.context.step(
            self._key(), "启动 fbasecman",
            details={"expected": "console 可连接且 group 配置加载成功",
                     "actual": "console ready (port=%s)" % port,
                     "config": str(config)})
        return config

    def _stop_existing(self):
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=10)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None

    def start_rejected(self, transform, title, expected, predicate):
        """验证非法配置会被启动期拒绝：预期启动失败且错误匹配 predicate。"""
        config = self.workdir / "fbasecman.conf"
        self._stop_existing()
        render_config(self.context, config, mode="none", transform=transform)
        self.conf = config
        self.proxy_log = config.with_suffix(".log")
        if self.proxy_log.exists():
            self.proxy_log.unlink()
        actual = ""
        passed = False
        try:
            result = self.context.command(
                [self.binary, str(config)], cwd=self.workdir,
                timeout_seconds=8, merge_stderr=True)
            actual = (result.stdout or "").strip()
            passed = result.returncode != 0 and predicate(actual)
            if result.returncode == 0:
                actual = "fbasecman unexpectedly exited rc=0: " + actual
        except TimeoutError as exc:
            actual = "fbasecman unexpectedly started: %s" % (
                getattr(exc, "partial_stdout", "") or "")
            passed = False
        if not actual and self.proxy_log.exists():
            actual = self.proxy_log.read_text(
                encoding="utf-8", errors="replace").strip()[-2000:]
            passed = predicate(actual)
        self.context.step(
            self._key(), title, status="PASS" if passed else "FAIL",
            details={"expected": expected, "actual": actual or "<empty>",
                     "config": str(config)})
        if not passed:
            raise AssertionError("%s: %s" % (title, actual or "unexpected start"))
        return config

    # -- console / business 断言 ------------------------------------------

    def psql(self, sql, title, expected, predicate):
        """console 命令 + 断言；命令后自动做配置语义 diff 白名单校验。"""
        config_before = (self.conf.read_text(encoding="utf-8")
                         if self.conf and self.conf.exists() else None)
        output = _console_expect(self.context, self.psql_bin, self.port, sql,
                                 self._key(), title, expected, predicate)
        if config_before is not None and self.conf.exists():
            config_after = self.conf.read_text(encoding="utf-8")
            if config_before != config_after:
                diff_text = "".join(difflib.unified_diff(
                    config_before.splitlines(True),
                    config_after.splitlines(True),
                    fromfile="config.before", tofile="config.after"))
                valid, semantic_diff = _semantic_config_diff(
                    config_before, config_after, sql.strip())
                self.context.step(
                    self._key(),
                    "%s：配置文件实际 diff（对应命令：%s）" % (title, sql.strip()),
                    status="PASS" if valid else "FAIL",
                    details={"expected": "只包含本次高可用命令预期修改",
                             "actual": "语义变化:\n%s\n\n原始文本 diff:\n%s" % (
                                 semantic_diff, diff_text.rstrip() or "<no differences>")})
                if not valid:
                    raise AssertionError(
                        "configuration persistence changed an unexpected object or field")
        return output

    def psql_error(self, sql, title, expected, predicate, compare_config=True):
        """执行 console 命令并断言被拒绝；可选校验配置未被污染。"""
        config_before = (self.conf.read_bytes()
                         if self.conf and self.conf.exists() else None)
        result = _console_query(self.context, self.psql_bin, self.port, sql)
        output = (result.stdout or "").rstrip() or "<empty>"
        passed = result.returncode != 0 and predicate(output)
        self.context.step(
            self._key(), title, status="PASS" if passed else "FAIL",
            details={"intent": "verify", "command": sql, "expected": expected,
                     "actual": "退出码=%s；错误输出：\n%s" % (result.returncode, output),
                     "analysis": "命令失败且错误内容满足期望" if passed else "命令未按声明错误条件被拒绝",
                     "output": output})
        if not passed:
            raise AssertionError("%s: returncode=%s" % (title, result.returncode))
        if config_before is not None and compare_config and self.conf.exists():
            unchanged = config_before == self.conf.read_bytes()
            self.context.step(
                self._key(), "%s：验证错误命令未修改活动配置" % title,
                status="PASS" if unchanged else "FAIL",
                details={"expected": "配置文件内容保持不变",
                         "actual": "config unchanged=%s" % unchanged})
            if not unchanged:
                raise AssertionError("错误命令修改了活动配置: %s" % sql)
        return output

    def psql_business(self, sql, title, expected, predicate,
                      group="mmr_group", port=None):
        return _expect(self.context, self._key(), title, expected, predicate,
                       lambda: _business_query(
                           self.context, self.psql_bin, port or self.port, sql,
                           group=group))

    def psql_business_error(self, sql, title, expected, predicate,
                            group="mmr_group", port=None, user="postgres"):
        result = _business_query(self.context, self.psql_bin, port or self.port,
                                 sql, group=group)
        output = (result.stdout or "").rstrip() or "<empty>"
        passed = result.returncode != 0 and predicate(output)
        self.context.step(
            self._key(), title, status="PASS" if passed else "FAIL",
            details={"intent": "verify", "command": sql, "expected": expected,
                     "actual": "退出码=%s；错误输出：\n%s" % (result.returncode, output),
                     "analysis": "命令失败且错误内容满足期望" if passed else "命令未按声明错误条件被拒绝",
                     "output": output})
        if not passed:
            raise AssertionError("%s: returncode=%s" % (title, result.returncode))
        return output

    def psql_monitor(self, sql, title, expected, predicate, retry_timeout=5):
        """expanded (-x) 形式的 console SHOW，字段断言精确到列。"""
        return _expect(
            self.context, self._key(), title, expected, predicate,
            lambda: self.context.command(
                [self.psql_bin, "-h", self.context.environment.get("local_host", "127.0.0.1"), "-p", str(self.port),
                 "-U", "admin", "-d", "console", "-x", "-c", sql],
                cwd=self.workdir, timeout_seconds=15, merge_stderr=True),
            retry_seconds=float(retry_timeout))

    def wait_node_monitor(self, title, expected_rows, retry_timeout=30):
        expected = "; ".join(
            "%s %s" % (node, ", ".join(
                "%s=%s" % (field, value)
                for field, value in sorted(fields.items())))
            for node, fields in sorted(expected_rows.items()))

        def matches(output):
            rows = _expanded_rows(output, "node_name")
            for node, fields in expected_rows.items():
                row = rows.get(node)
                if row is None or any(row.get(field) != value
                                      for field, value in fields.items()):
                    return False
            return True

        return self.psql_monitor("SHOW NODE_MONITOR;", title, expected,
                                 matches, retry_timeout=retry_timeout)

    def assert_table(self, sql, title, expected_rows, key="node_name",
                     retry_timeout=15):
        expected_desc = "; ".join(
            "%s [%s]" % (k, ", ".join("%s=%s" % (col, val)
                                     for col, val in v.items()))
            for k, v in expected_rows.items())
        deadline = time.monotonic() + max(0, retry_timeout)
        attempt = 0
        output = ""
        last_summary = ""
        while True:
            attempt += 1
            result = _console_query(self.context, self.psql_bin, self.port, sql)
            output = (result.stdout or "").rstrip() or "<empty>"
            passed, summary, _ = assert_table_rows(output, expected_rows, key=key)
            last_summary = summary
            if (result.returncode == 0 and passed) or time.monotonic() >= deadline:
                break
            time.sleep(0.3)
        self.context.step(
            self._key(), title,
            status="PASS" if (result.returncode == 0 and passed) else "FAIL",
            details={"expected": expected_desc, "actual": last_summary,
                     "output": output})
        if result.returncode != 0 or not passed:
            raise AssertionError("%s 失败:\n%s" % (title, last_summary))
        return output

    # -- 断言/文件 --------------------------------------------------------

    def check(self, title, expected, actual, passed):
        self.context.step(
            self._key(), title, status="PASS" if passed else "FAIL",
            details={"intent": "verify", "expected": expected, "actual": actual,
                     "analysis": "实际结果满足声明条件" if passed else "实际结果与声明条件不符"})
        if not passed:
            raise AssertionError("%s: %s" % (title, actual))

    def diff(self, before, after):
        result = self.context.command(
            ["diff", "-u", str(before), str(after)], cwd=self.workdir,
            timeout_seconds=10, merge_stderr=True)
        output = (result.stdout or "").rstrip() or "<no differences>"
        self.context.step(
            self._key(), "检查配置文件 diff（与测试开始时初始配置比较）",
            status="PASS" if result.returncode == 0 else "FAIL",
            details={"expected": "与初始配置无差异", "actual": output})
        if result.returncode != 0:
            raise AssertionError("configuration diff is not empty: %s" % output)

    def diff_contains(self, before, after, expected, title):
        result = self.context.command(
            ["diff", "-u", str(before), str(after)], cwd=self.workdir,
            timeout_seconds=10, merge_stderr=True)
        output = (result.stdout or "").rstrip() or "<no differences>"
        passed = result.returncode == 1 and all(
            item in output for item in expected)
        self.context.step(
            self._key(), title, status="PASS" if passed else "FAIL",
            details={"expected": "diff 包含: %s" % ", ".join(expected),
                     "actual": output})
        if not passed:
            raise AssertionError(
                "configuration diff did not contain expected change: %s" % output)

    @staticmethod
    def _backup_dir(config_path, backup_dir=None):
        return Path(backup_dir) if backup_dir is not None \
            else Path(config_path).parent / "conf-backup"

    def backup_checkpoint(self, config_path, backup_dir=None):
        directory = self._backup_dir(config_path, backup_dir)
        files = tuple(path.name for path in directory.iterdir()
                      if ".bak." in path.name) if directory.is_dir() else ()
        return (files, Path(config_path).read_bytes(), directory)

    def assert_backup_created(self, checkpoint, config_path,
                              title="验证配置备份文件"):
        files, config_content, directory = checkpoint
        current = set(path.name for path in directory.iterdir()
                      if ".bak." in path.name) if directory.is_dir() else set()
        created = sorted(current - set(files))
        content_matches = (len(created) == 1 and
                           (directory / created[0]).read_bytes() == config_content)
        actual = "新增备份=%s；备份数量 %d -> %d；内容与命令前配置%s" % (
            created[0] if len(created) == 1 else created,
            len(files), len(current),
            "一致" if content_matches else "不一致")
        self.context.step(
            self._key(), title, status="PASS" if (len(created) == 1 and content_matches) else "FAIL",
            details={"expected": "恰好新增一个备份，且内容等于命令执行前配置",
                     "actual": actual})
        if not (len(created) == 1 and content_matches):
            raise AssertionError("backup verification failed: %s" % actual)
        return created[0]

    def assert_no_backup_created(self, checkpoint, config_path,
                                 title="验证未创建配置备份"):
        files, _content, directory = checkpoint
        current = set(path.name for path in directory.iterdir()
                      if ".bak." in path.name) if directory.is_dir() else set()
        created = sorted(current - set(files))
        actual = "新增备份=%s；备份数量 %d -> %d" % (created, len(files), len(current))
        self.context.step(
            self._key(), title,
            status="PASS" if current == set(files) else "FAIL",
            details={"expected": "备份文件集合保持不变", "actual": actual})
        if current != set(files):
            raise AssertionError("unexpected backup created: %s" % actual)

    def run_command(self, argv, log_name=None, cwd=None, step_title=None,
                    check=False, timeout=60):
        result = self.context.command(
            [str(arg) for arg in argv], cwd=cwd or self.workdir,
            timeout_seconds=timeout, merge_stderr=True)
        if step_title:
            self.context.step(
                self._key(), step_title,
                status="PASS" if result.returncode == 0 else "FAIL",
                details={"intent": "action", "expected": "命令退出码为 0", "command": " ".join(str(a) for a in argv),
                         "actual": "退出码=%s" % result.returncode,
                         "output": (result.stdout or "")[-4000:]})
        if check and result.returncode != 0:
            raise AssertionError("command failed rc=%s" % result.returncode)
        return result.returncode, result.stdout or ""

    # -- 远程节点 ---------------------------------------------------------

    def postgres_node_action(self, pgdata, action, title):
        """ssh 远程 pg_ctl 停/起一个 PostgreSQL 夹具节点（对齐 legacy）。"""
        if action not in ("start", "stop"):
            raise ValueError("unsupported postgres node action: %s" % action)
        host = self.env.get("mmr_host")
        user = self.env.get("mmr_pg_user")
        bin_dir = self.env.get("mmr_bin_dir")
        if not host or not user or not bin_dir:
            raise Blocked("平台上下文缺少 mmr_host/mmr_pg_user/mmr_bin_dir")
        pgctl = "%s/pg_ctl" % bin_dir
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
        result = self.context.command(
            ["ssh", "-F", "/dev/null", "%s@%s" % (user, host), script],
            cwd=self.workdir, timeout_seconds=60, merge_stderr=True)
        self.context.step(
            self._key(), title,
            status="PASS" if result.returncode == 0 else "FAIL",
            details={"intent": "action", "expected": "节点已停止，pg_ctl status 检查失败" if action == "stop" else "节点已启动，pg_ctl status 检查成功", "action": action, "pgdata": str(pgdata),
                     "actual": "returncode=%s" % result.returncode,
                     "output": (result.stdout or "")[-2000:]})
        if result.returncode != 0:
            raise AssertionError("%s: rc=%s" % (title, result.returncode))
        return result

    def node_port(self, name):
        """legacy ports[...] 别名映射：mmr1/mmr2 → primaries，mmr*_standbyN → extras。"""
        nodes = self.env.get("nodes") or {}
        extras = self.env.get("extra_nodes") or {}
        if name in nodes:
            return int(nodes[name]["port"])
        aliases = {"mmr1_standby1": "pg_3", "mmr2_standby1": "pg_4"}
        if name in aliases and aliases[name] in extras:
            return int(extras[aliases[name]]["port"])
        raise Blocked("平台上下文缺少节点端口: %s" % name)


# ---------------------------------------------------------------------------
# 通用 conf transform / 数据助手（移植自 legacy helpers.py）
# ---------------------------------------------------------------------------

def _node_has_weight(output, node, weight):
    return any(node in line and str(weight) in line
               for line in output.splitlines())


def _datasource_block(text, name):
    start = text.index('datasources "%s" {' % name)
    end = text.index('\n}\n', start) + 2
    return text[start:end]


def _cluster_datasources_have_status(text, status):
    return all('status "%s"' % status in _datasource_block(text, 'cluster_ds_%02d' % i)
               for i in range(1, 31))


def _add_30_cluster_datasources(content):
    blocks = []
    for index in range(1, 31):
        blocks.extend(('datasources "cluster_ds_%02d" {' % index,
                       '    host "192.0.2.1"', '    port %d' % (57000 + index),
                       '    cluster_name "bulk_cluster"', '    weight 10',
                       '    status "active"', '    tls "disable"', '}', ''))
    return content + '\n' + '\n'.join(blocks)


def _group_fields_with_format(content):
    content = content.replace(
        '    write_cluster "pg_cluster_2"',
        '\twrite_cluster    "pg_cluster_2"    # keep-write-format', 1)
    return content.replace(
        '    promoted_cluster "pg_cluster_1"',
        '      promoted_cluster\t"pg_cluster_1"    # keep-promoted-format', 1)


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


# ---------------------------------------------------------------------------
# executors/cluster.py 迁移（10 条）
# ---------------------------------------------------------------------------

def exec_refresh_cluster_syntax_errors(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW CLUSTERS;', "查看 REFRESH 语法错误前的运行态",
            'pg_cluster_1 为 VALID 且 current primary 为 pg_1',
            lambda output: all(v in output for v in ("pg_cluster_1", "VALID", "pg_1")))
    for sql, title in (
        ('REFRESH CLUSTER;', "执行缺少 cluster 名的 REFRESH 命令"),
        ('REFRESH NODE pg_1;', "执行错误关键字的 REFRESH 命令"),
        ('REFRESH CLUSTER pg_cluster_1 extra;', "执行带额外参数的 REFRESH 命令"),
    ):
        backup = rt.backup_checkpoint(conf)
        rt.psql_error(sql, title, '返回 ERROR 且命令被拒绝',
                      lambda output: "ERROR:" in output)
        rt.assert_no_backup_created(backup, conf, "验证 REFRESH 语法错误未创建备份")
        rt.diff(before, conf)
        rt.psql('SHOW CLUSTERS;', "验证 REFRESH 语法错误后的运行态",
                'pg_cluster_1 仍为 VALID 且 current primary 为 pg_1',
                lambda output: all(v in output for v in ("pg_cluster_1", "VALID", "pg_1")))


def exec_set_cluster_30_datasource_roundtrip(rt):
    conf = rt.start(transform=_add_30_cluster_datasources)
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    rt.psql('SHOW DATASOURCES;', "查看 30 datasource cluster 操作前状态",
            "cluster_ds_01 和 cluster_ds_30 均为 active",
            lambda output: all(v in output for v in ("cluster_ds_01", "cluster_ds_30", "active")))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET CLUSTER PARTED bulk_cluster;', "将 30 个 datasource 批量置为 PARTED",
            "返回 SET CLUSTER 且命令不报错",
            lambda output: "SET CLUSTER" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 30 datasource PARTED 备份")
    parted = _cluster_datasources_have_status(conf.read_text(encoding="utf-8"), "parted")
    rt.check("验证 cluster 展开完整覆盖 30 个 datasource", "30 个节点均为 parted",
             "30 datasources parted=%s" % parted, parted)
    rt.diff_contains(before, conf, ('-    status "active"', '+    status "parted"'),
                     "验证 30 datasource PARTED 配置 diff")
    rt.psql('SHOW DATASOURCES;', "查看 30 datasource PARTED 后状态",
            "cluster_ds_01 和 cluster_ds_30 均为 parted",
            lambda output: all(v in output for v in ("cluster_ds_01", "cluster_ds_30", "parted")))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET CLUSTER ACTIVE bulk_cluster;', "恢复 30 个 datasource 为 ACTIVE",
            "返回 SET CLUSTER 且命令不报错",
            lambda output: "SET CLUSTER" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 30 datasource ACTIVE 恢复备份")
    restored = _cluster_datasources_have_status(conf.read_text(encoding="utf-8"), "active")
    rt.check("验证 30 个 datasource 全部恢复", "30 个节点均恢复 active",
             "30 datasources active=%s" % restored, restored)
    rt.diff(before, conf)
    rt.psql('SHOW DATASOURCES;', "查看 30 datasource 恢复后状态",
            "cluster_ds_01 和 cluster_ds_30 均为 active",
            lambda output: all(v in output for v in ("cluster_ds_01", "cluster_ds_30", "active")))


def exec_set_cluster_invalid_commands(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW CLUSTERS;', "查看非法 SET CLUSTER 命令前的运行态",
            'pg_cluster_1 为 VALID 且成员为 active',
            lambda output: all(v in output for v in ("pg_cluster_1", "VALID")))
    for sql, title, predicate in (
        ('SET CLUSTER PARTED no_such_cluster;', "执行不存在 cluster 的 PARTED 命令",
         lambda output: all(v in output for v in ("ERROR:", "no_such_cluster", "does not exist"))),
        ('SET CLUSTER WEIGHT pg_cluster_1;', "执行非法 SET CLUSTER 动作",
         lambda output: "ERROR:" in output),
    ):
        backup = rt.backup_checkpoint(conf)
        rt.psql_error(sql, title, '返回 ERROR 且命令被拒绝', predicate)
        rt.assert_no_backup_created(backup, conf, "验证非法 SET CLUSTER 未创建备份")
        rt.diff(before, conf)
        rt.psql('SHOW CLUSTERS;', "验证非法 SET CLUSTER 命令后的运行态",
                'pg_cluster_1 仍为 VALID 且成员为 active',
                lambda output: all(v in output for v in ("pg_cluster_1", "VALID")))


def exec_console_set_validation_toggle(rt):
    def with_validation_yes(content):
        return 'console_set_validation yes\n' + content

    conf = rt.start(transform=with_validation_yes)
    rt.check(
        "确认 console_set_validation 默认开启",
        "配置文件包含 console_set_validation yes",
        "console_set_validation yes=%s" %
        ('console_set_validation yes' in conf.read_text(encoding="utf-8")),
        'console_set_validation yes' in conf.read_text(encoding="utf-8"),
    )
    rt.psql('set a=1;', "校验开启时接受第一条 GUC 赋值", "返回 SET",
            lambda output: output.count("SET") >= 1 and "ERROR" not in output)
    rt.psql('b=1;', "校验开启时接受分号拆包后的第二条 GUC 赋值", "返回 SET",
            lambda output: output.count("SET") >= 1 and "ERROR" not in output)
    rt.psql('set a to 1;', "校验开启时接受第一条 TO 形式 GUC 赋值", "返回 SET",
            lambda output: output.count("SET") >= 1 and "ERROR" not in output)
    rt.psql('b to 1;', "校验开启时接受分号拆包后的第二条 TO 形式 GUC 赋值", "返回 SET",
            lambda output: output.count("SET") >= 1 and "ERROR" not in output)
    rt.psql_error('set parted ;', "校验开启时拒绝不完整 SET",
                  "返回 unsupported console SET command",
                  lambda output: "unsupported console SET command" in output)
    rt.psql_error('set pg_1 parted ;', "校验开启时拒绝非白名单 SET",
                  "返回 unsupported console SET command",
                  lambda output: "unsupported console SET command" in output)
    rt.psql("SET TIME ZONE 'UTC';", "校验开启时接受 SET TIME ZONE",
            "返回 SET 且不报错",
            lambda output: "SET" in output and "ERROR" not in output)

    text = conf.read_text(encoding="utf-8")
    conf.write_text(text.replace(
        "console_set_validation yes", "console_set_validation no", 1),
        encoding="utf-8")
    rt.psql('RELOAD;', "Reload 关闭 console_set_validation", "返回 RELOAD",
            lambda output: "RELOAD" in output and "ERROR" not in output)
    reload_log = rt.proxy_log.read_text(encoding="utf-8", errors="replace") \
        if rt.proxy_log and rt.proxy_log.exists() else ""
    rt.check("验证 Reload 日志记录 console_set_validation no",
             "日志包含 console_set_validation no",
             "console_set_validation no=%s" % ("console_set_validation no" in reload_log),
             "console_set_validation no" in reload_log)
    rt.psql('set parted ;', "关闭校验后接受任意 SET", "返回 SET 且不报错",
            lambda output: "SET" in output and "ERROR" not in output)
    rt.psql('set pg_1 parted ;', "关闭校验后接受独立续写 SET", "返回 SET 且不报错",
            lambda output: "SET" in output and "ERROR" not in output)

    text = conf.read_text(encoding="utf-8")
    conf.write_text(text.replace(
        "console_set_validation no", "console_set_validation yes", 1),
        encoding="utf-8")
    rt.psql('RELOAD;', "Reload 恢复 console_set_validation", "返回 RELOAD",
            lambda output: "RELOAD" in output and "ERROR" not in output)
    reload_log = rt.proxy_log.read_text(encoding="utf-8", errors="replace") \
        if rt.proxy_log and rt.proxy_log.exists() else ""
    rt.check("验证 Reload 日志记录 console_set_validation yes",
             "日志包含 console_set_validation yes",
             "console_set_validation yes=%s" % ("console_set_validation yes" in reload_log),
             "console_set_validation yes" in reload_log)
    rt.psql_error('set parted ;', "恢复校验后再次拒绝不完整 SET",
                  "返回 unsupported console SET command",
                  lambda output: "unsupported console SET command" in output)


def exec_set_node_promoted_write_cluster_conflict(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SET NODE PROMOTED pg_2 IN GROUP mmr_group;',
            "验证 PROMOTED 目标已是 write cluster 时幂等处理",
            '目标已是 write cluster 时命令幂等返回 NO CONFIG CHANGE，不修改配置',
            lambda output: ("SET NODE" in output or "NO CONFIG CHANGE" in output)
            and "ERROR" not in output)
    rt.diff(before, conf)


def exec_refresh_cluster_probe_edges(rt):
    mmr_root = rt.env.get("mmr_data_root")
    if not mmr_root:
        raise Blocked("平台上下文缺少 mmr_data_root")
    pg_1_dir = mmr_root + "/test_mmr1"
    pg_3_dir = mmr_root + "/test_mmr1_s1"

    def transform(content, enabled, period):
        content = content.replace("monitor_enabled yes\n",
                                  "monitor_enabled %s\n" % enabled, 1)
        content = content.replace("monitor_period 10\n",
                                  "monitor_period %s\n" % period, 1)
        return content.replace("monitor_recovery_period 10\n",
                               "monitor_recovery_period 1\n", 1)

    def node_field(output, node, field):
        marker = re.search(r"(?m)^node_name\s*\|\s*%s\s*$" % re.escape(node), output)
        if not marker:
            return None
        tail = output[marker.start():]
        next_record = re.search(r"(?m)^-\[ RECORD", tail[1:])
        row = tail[:next_record.start() + 1] if next_record else tail
        match = re.search(r"(?m)^%s\s*\|\s*(.*?)\s*$" % re.escape(field), row)
        return match.group(1).strip() if match else None

    def wait_monitor(title, expected, predicate, attempts=12):
        last = ""
        for attempt in range(attempts):
            last = rt.psql_monitor(
                "SHOW NODE_MONITOR;", "%s（轮询 %d）" % (title, attempt + 1),
                expected, lambda output: True)
            if predicate(last):
                return last
            time.sleep(1)
        rt.check(title, expected, "monitor 快照在 %d 次轮询内未达预期" % attempts,
                 False)

    def nodes_online(output):
        return (node_field(output, "pg_1", "connect_status") == "ONLINE"
                and node_field(output, "pg_3", "connect_status") == "ONLINE")

    def nodes_offline(output):
        return (node_field(output, "pg_1", "connect_status") == "OFFLINE"
                and node_field(output, "pg_3", "connect_status") == "OFFLINE")

    for enabled, period in (("no", 30), ("yes", 300)):
        conf = rt.start(transform=lambda content, e=enabled, p=period:
                        transform(content, e, p))
        before = rt.workdir / ("before-%s.conf" % enabled)
        before.write_bytes(conf.read_bytes())

        rt.psql('SHOW CLUSTERS;', "%s：查看 REFRESH 前的 cluster 运行态" % enabled,
                'pg_cluster_1 为 VALID 且 current primary 为 pg_1',
                lambda output: all(v in output for v in ("pg_cluster_1", "VALID", "pg_1")))
        for sql, name_desc in (
            ('REFRESH CLUSTER no_such_cluster;', "不存在的 cluster 名"),
            ('REFRESH CLUSTER pg_1;', "误传 datasource 名"),
            ('REFRESH CLUSTER mmr_group;', "误传 group 名"),
        ):
            backup = rt.backup_checkpoint(conf)
            rt.psql_error(sql, "%s：REFRESH %s 被拒绝" % (enabled, name_desc),
                          "返回 cluster does not exist",
                          lambda output: "does not exist" in output)
            rt.assert_no_backup_created(
                backup, conf, "%s：验证 %s 拒绝未创建备份" % (enabled, name_desc))
        rt.diff(before, conf)
        rt.psql('SHOW CLUSTERS;', "%s：验证名称拒绝后的 cluster 运行态" % enabled,
                'pg_cluster_1 仍为 VALID 且 current primary 为 pg_1',
                lambda output: all(v in output for v in ("pg_cluster_1", "VALID", "pg_1")))

        wait_monitor("%s：确认初始节点监控状态" % enabled,
                     "pg_1 与 pg_3 connect_status=ONLINE", nodes_online)
        try:
            rt.postgres_node_action(pg_1_dir, "stop",
                                    "%s：停止 pg_1（pg_cluster_1 主节点）" % enabled)
            rt.postgres_node_action(pg_3_dir, "stop",
                                    "%s：停止 pg_3（pg_cluster_1 备节点）" % enabled)
            rt.psql("REFRESH CLUSTER pg_cluster_1;",
                    "%s：cluster 全部节点离线时执行 REFRESH" % enabled,
                    "一次性探测轮完成仍返回 REFRESH CLUSTER",
                    lambda output: "REFRESH CLUSTER" in output and "ERROR" not in output)
            wait_monitor("%s：验证离线已被一次性探测确认" % enabled,
                         "pg_1 与 pg_3 connect_status=OFFLINE", nodes_offline)
        finally:
            rt.postgres_node_action(pg_1_dir, "start", "%s：恢复 pg_1" % enabled)
            rt.postgres_node_action(pg_3_dir, "start", "%s：恢复 pg_3" % enabled)
        rt.psql("REFRESH CLUSTER pg_cluster_1;",
                "%s：节点恢复后再次执行 REFRESH" % enabled,
                "返回 REFRESH CLUSTER 且不报错",
                lambda output: "REFRESH CLUSTER" in output and "ERROR" not in output)
        wait_monitor("%s：验证节点恢复后监控回到 ONLINE" % enabled,
                     "pg_1 与 pg_3 connect_status=ONLINE", nodes_online)
        rt.diff(before, conf)
        rt.psql('SHOW CLUSTERS;', "%s：查看本模式结束时的 cluster 运行态" % enabled,
                'pg_cluster_1 为 VALID',
                lambda output: "pg_cluster_1" in output and "VALID" in output)


def exec_set_cluster_write_promoted_roundtrip(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW GROUP_ROUTING mmr_group;',
            "查看 SET CLUSTER WRITE/PROMOTED 执行前的运行态",
            'write cluster 为 pg_cluster_2，promoted cluster 为 pg_cluster_1',
            lambda output: all(value in output for value in (
                "pg_cluster_2", "pg_cluster_1", "active")))
    rt.psql('SET CLUSTER WRITE pg_cluster_1;', "执行 SET CLUSTER WRITE 切换写中心",
            '返回 SET CLUSTER 且命令不报错',
            lambda output: ("SET CLUSTER" in output or "NO CONFIG CHANGE" in output)
            and "ERROR" not in output)
    rt.diff_contains(
        before, conf,
        ('-    write_cluster "pg_cluster_2"', '+    write_cluster "pg_cluster_1"',
         '-    promoted_cluster "pg_cluster_1"', '+    promoted_cluster "pg_cluster_2"'),
        "验证 SET CLUSTER WRITE 的 write/promoted 配置变更")
    rt.psql('SHOW GROUP_ROUTING mmr_group;',
            "验证 SET CLUSTER WRITE 后的运行态",
            'write cluster 为 pg_cluster_1，promoted cluster 为 pg_cluster_2',
            lambda output: all(value in output for value in (
                "pg_cluster_1", "pg_cluster_2", "active")))
    rt.psql_business(
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE; "
        "SELECT inet_server_port(), current_user;",
        "验证 SET CLUSTER WRITE 后的实际写路由",
        '业务连接应落到 pg_cluster_1 primary，并返回 postgres 用户',
        lambda output: str(rt.node_port("mmr1")) in output and "postgres" in output)

    rt.psql('SET CLUSTER PROMOTED pg_cluster_1 IN GROUP mmr_group;',
            "执行 SET CLUSTER PROMOTED IN GROUP 切换提升中心",
            '目标已是 write cluster，返回 NO CONFIG CHANGE',
            lambda output: ("SET CLUSTER" in output or "NO CONFIG CHANGE" in output)
            and "ERROR" not in output)
    rt.psql('SHOW GROUP_ROUTING mmr_group;',
            "验证 SET CLUSTER PROMOTED 后的运行态",
            'write cluster 为 pg_cluster_1，promoted cluster 仍为 pg_cluster_2',
            lambda output: all(value in output for value in (
                "pg_cluster_1", "pg_cluster_2", "active")))
    rt.psql('SET CLUSTER WRITE pg_cluster_2;', "恢复 SET CLUSTER WRITE 初始写中心",
            '返回 SET CLUSTER 且命令不报错',
            lambda output: "SET CLUSTER" in output and "ERROR" not in output)
    rt.diff(before, conf)
    rt.psql('SHOW GROUP_ROUTING mmr_group;',
            "验证 SET CLUSTER WRITE/PROMOTED 恢复后的运行态",
            'write cluster 为 pg_cluster_2，promoted cluster 为 pg_cluster_1',
            lambda output: all(value in output for value in (
                "pg_cluster_2", "pg_cluster_1", "active")))


def exec_set_cluster_parted_active_roundtrip(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW DATASOURCES;', "查看 cluster 隔离前的节点状态",
            'pg_1、pg_3 均为 active',
            lambda output: all(v in output for v in ("pg_1", "pg_3", "active")))
    rt.psql('SET CLUSTER PARTED pg_cluster_1;', "隔离 pg_cluster_1",
            '返回 SET CLUSTER',
            lambda output: "SET CLUSTER" in output and "ERROR" not in output)
    text = conf.read_text(encoding="utf-8")
    rt.check("验证 cluster 的两个 datasource 完整落盘",
             "pg_cluster_1 的 pg_1、pg_3 均为 status parted",
             "status parted occurrences=%d" % text.count('    status "parted"\n'),
             text.count('    status "parted"\n') == 2)
    rt.psql_business_error(
        'SELECT inet_server_port();',
        "验证 cluster PARTED 后 Single 业务路由不可用",
        "single_group 唯一 backend cluster 已隔离，业务连接明确失败",
        lambda output: "ERROR" in output or "server" in output.lower(),
        group="single_group")
    rt.diff_contains(before, conf, ('+    status "parted"',),
                     "验证 SET CLUSTER PARTED 的配置 diff")
    rt.psql('SHOW DATASOURCES;', "验证 cluster 隔离后的运行态",
            'pg_1、pg_3 均显示 parted',
            lambda output: all(v in output for v in ("pg_1", "pg_3", "parted")))
    rt.psql('SET CLUSTER ACTIVE pg_cluster_1;', "恢复 pg_cluster_1",
            '返回 SET CLUSTER',
            lambda output: "SET CLUSTER" in output and "ERROR" not in output)
    _wait_pg_cluster_ready(
        rt, "pg_cluster_1", "pg_1", ("pg_3",),
        "等待 cluster ACTIVE 的即时探测和路由投影完成")
    rt.psql_business(
        'SELECT inet_server_port(), current_user;',
        "验证 cluster ACTIVE 后 Single 业务路由恢复",
        "single_group 重新命中 pg_cluster_1 primary",
        lambda output: str(rt.node_port("mmr1")) in output and "postgres" in output,
        group="single_group")
    rt.diff(before, conf)
    rt.psql('SHOW DATASOURCES;', "验证 cluster 恢复后的运行态",
            'pg_1、pg_3 均恢复 active',
            lambda output: all(v in output for v in ("pg_1", "pg_3", "active")))


def exec_refresh_cluster(rt):
    mmr_root = rt.env.get("mmr_data_root")
    if not mmr_root:
        raise Blocked("平台上下文缺少 mmr_data_root")
    node_dir = mmr_root + "/test_mmr1_s1"

    def transform(content, enabled, period):
        content = content.replace("monitor_enabled yes\n",
                                  "monitor_enabled %s\n" % enabled, 1)
        content = content.replace("monitor_period 10\n",
                                  "monitor_period %s\n" % period, 1)
        return content.replace("monitor_recovery_period 10\n",
                               "monitor_recovery_period 1\n", 1)

    def fields(output, datasource, endpoint=False):
        key = "node_name" if endpoint else "name"
        match = re.search(r"(?m)^%s\s*\|\s*%s\s*$" % (key, re.escape(datasource)), output)
        if not match:
            return {}
        block = output[match.start():]
        next_record = re.search(r"(?m)^-\[ RECORD", block[1:])
        block = block[:next_record.start() + 1] if next_record else block
        return dict(re.findall(r"(?m)^([a-z_]+)\s*\|\s*(.*?)\s*$", block))

    def check_endpoint(output, datasource, failed, prior_seq):
        row = fields(output, datasource, endpoint=True)
        return (row.get("probe_state") == "READY" and
                int(row.get("probe_seq", "0")) > prior_seq and
                row.get("connect_status") == ("OFFLINE" if failed else "ONLINE") and
                row.get("topology_state") in ("VALID", "VALID_DEGRADED") and
                int(row.get("fault_count", "0") or 0) >= (1 if failed else 0) and
                ("CONNECT_FAILED" in row.get("fault_flags", "") if failed
                 else row.get("fault_flags") == "{}"))

    def check_node(output, datasource, failed):
        expected_connectivity = "OFFLINE" if failed else "ONLINE"
        expected_topology = "VALID_DEGRADED" if failed else "VALID"
        expected_effective = "OFFLINE" if failed else "READ_ONLY"
        marker = re.search(r"(?m)^node_name\s*\|\s*%s\s*$" % re.escape(datasource), output)
        if not marker:
            return False
        tail = output[marker.start():]
        next_record = re.search(r"(?m)^-\[ RECORD", tail[1:])
        row_text = tail[:next_record.start() + 1] if next_record else tail
        return all(re.search(r"(?m)^%s\s*\|\s*%s\s*$" % (field, re.escape(value)), row_text)
                   for field, value in (("probe_state", "READY"),
                                        ("connect_status", expected_connectivity),
                                        ("topology_state", expected_topology),
                                        ("effective_status", expected_effective)))

    def wait_endpoint(title, expected, predicate, attempts=12):
        last = ""
        for attempt in range(attempts):
            last = rt.psql_monitor(
                "SHOW ENDPOINT_MONITOR;", "%s（轮询 %d）" % (title, attempt + 1),
                expected, lambda output: True)
            if predicate(last):
                return last
            time.sleep(1)
        rt.check(title, expected, "monitor 快照在 %ss 内未达到预期" % attempts, False)
        return last

    for enabled, period in (("no", 30), ("yes", 300)):
        conf = rt.start(transform=lambda content, e=enabled, p=period:
                        transform(content, e, p))
        before = rt.workdir / ("before-%s.conf" % enabled)
        before.write_bytes(conf.read_bytes())
        try:
            initial = wait_endpoint(
                "%s：刷新前端点监控快照" % enabled,
                "pg_3 probe_state=READY、connect_status=ONLINE、topology_state=VALID",
                lambda output: check_endpoint(output, "pg_3", False, -1))
            initial_row = fields(initial, "pg_3", endpoint=True)
            initial_seq = int(initial_row.get("probe_seq", "0"))
            rt.postgres_node_action(node_dir, "stop", "%s：停止 pg_3 standby" % enabled)
            time.sleep(1.0)
            rt.psql("REFRESH CLUSTER pg_cluster_1;",
                    "%s：节点停止后执行 REFRESH CLUSTER" % enabled,
                    "返回 REFRESH CLUSTER", lambda output: "REFRESH CLUSTER" in output)
            wait_endpoint("%s：确认端点故障已被显式刷新发现" % enabled,
                          "pg_3 probe_seq 递增、OFFLINE、fault_count 增加",
                          lambda output, s=initial_seq: check_endpoint(output, "pg_3", True, s))
            rt.psql_monitor("SHOW NODE_MONITOR;", "%s：确认节点故障已被显式刷新发现" % enabled,
                            "pg_3 connect_status 非 ONLINE 且 topology_state=VALID",
                            lambda output: check_node(output, "pg_3", True))
            failed_output = rt.psql_monitor(
                "SHOW ENDPOINT_MONITOR;", "%s：记录故障端点基准" % enabled,
                "保存 pg_3 故障后的 probe_seq",
                lambda output: check_endpoint(output, "pg_3", True, initial_seq))
            failed_seq = int(fields(failed_output, "pg_3", endpoint=True)["probe_seq"])
        finally:
            rt.postgres_node_action(node_dir, "start", "%s：恢复 pg_3 standby" % enabled)
        time.sleep(1.1)
        rt.psql("REFRESH CLUSTER pg_cluster_1;",
                "%s：节点恢复后再次 REFRESH CLUSTER" % enabled,
                "返回 REFRESH CLUSTER", lambda output: "REFRESH CLUSTER" in output)
        wait_endpoint(
            "%s：确认端点恢复已生效" % enabled,
            "pg_3 probe_seq 递增、ONLINE、fault_flags={}、fault_count=0",
            lambda output, s=failed_seq: check_endpoint(output, "pg_3", False, s))
        rt.psql_monitor("SHOW NODE_MONITOR;", "%s：确认节点恢复已生效" % enabled,
                        "pg_3 connect_status=ONLINE、topology_state=VALID、effective_status=ONLINE",
                        lambda output: check_node(output, "pg_3", False))
        rt.diff(before, conf)


def exec_write_cluster_format_preservation(rt):
    conf = rt.start(transform=_group_fields_with_format)
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW GROUP_ROUTING mmr_group;', "查看格式保持 WRITE 前的运行态",
            'pg_cluster_2，pg_cluster_1',
            lambda output: all(v in output for v in ("pg_cluster_2", "pg_cluster_1")))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WRITE pg_1 IN GROUP mmr_group;', "切换带特殊格式的 WRITE 字段",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 WRITE 格式切换的配置备份")
    text = conf.read_text(encoding="utf-8")
    write_line = '\twrite_cluster    "pg_cluster_1"    # keep-write-format'
    promoted_line = '      promoted_cluster\t"pg_cluster_2"    # keep-promoted-format'
    rt.check("验证 WRITE 两字段周边格式保持",
             "两个字段分别保留原缩进、空格、tab 和行尾注释",
             "write格式=%s；promoted格式=%s" % (write_line in text, promoted_line in text),
             write_line in text and promoted_line in text)
    rt.psql('SHOW GROUP_ROUTING mmr_group;', "查看格式保持 WRITE 切换后的运行态",
            'pg_cluster_1，pg_cluster_2，pg_1 为 write-leader',
            lambda output: all(v in output for v in
                               ("pg_cluster_1", "pg_cluster_2", "pg_1", "write-leader")))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WRITE pg_2 IN GROUP mmr_group;', "恢复带特殊格式的 WRITE 字段",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 WRITE 格式恢复的配置备份")
    rt.diff(before, conf)
    rt.psql('SHOW GROUP_ROUTING mmr_group;', "验证格式保持 WRITE 恢复后的运行态",
            'pg_cluster_2，pg_cluster_1，pg_2 为 write-leader',
            lambda output: all(v in output for v in
                               ("pg_cluster_2", "pg_cluster_1", "pg_2", "write-leader")))


HA_CLUSTER_EXECUTORS = {
    "refresh_cluster": exec_refresh_cluster,
    "refresh_cluster_probe_edges": exec_refresh_cluster_probe_edges,
    "refresh_cluster_syntax_errors": exec_refresh_cluster_syntax_errors,
    "set_cluster_30_datasource_roundtrip": exec_set_cluster_30_datasource_roundtrip,
    "set_cluster_invalid_commands": exec_set_cluster_invalid_commands,
    "console_set_validation_toggle": exec_console_set_validation_toggle,
    "set_cluster_parted_active_roundtrip": exec_set_cluster_parted_active_roundtrip,
    "set_cluster_write_promoted_roundtrip": exec_set_cluster_write_promoted_roundtrip,
    "set_node_promoted_write_cluster_conflict": exec_set_node_promoted_write_cluster_conflict,
    "write_cluster_format_preservation": exec_write_cluster_format_preservation,
}


# 后续 batch/node/persistence 等域的 executor 并入统一注册表
HA_EXECUTORS = dict(HA_CLUSTER_EXECUTORS)


class HaCommandsCase:
    """ha_commands 原生用例分发器：按用例名调用对应 executor。"""

    def __init__(self, name):
        self.name = name

    def run(self, context: CaseContext):
        if self.name not in HA_EXECUTORS:
            raise Blocked("ha_commands 用例尚未迁移: %s" % self.name)
        rt = HaRuntime(context)
        HA_EXECUTORS[self.name](rt)
        return True
