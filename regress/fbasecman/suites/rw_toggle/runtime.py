"""Runtime for migrated read/write routing cases."""

import re
import shutil
from pathlib import Path

from framework.execution.command import run_logged_command
from framework.reporting import ReportCheck, ReportStep
from suites.ha_commands.runtime import HaCommandFailure, HaCommandRuntime


class RwToggleRuntime(HaCommandRuntime):
    def __init__(self, root, case):
        super(RwToggleRuntime, self).__init__(root, case)
        self.driver_dir = self.workdir / "driver"
        self.driver_dir.mkdir()

    def render_conf(self, transform=None):
        def route_transform(content):
            group = "mmr_group" if self.case.topology == "mmr" else "rep_group"
            content = content.replace(
                'group_names "mmr_group,rep_group,balance_group,single_group"',
                'group_names "%s"' % group, 1)
            content = content.replace('    rw_split_method "none"',
                                      '    rw_split_method "%s"' % self.case.route_mode, 1)
            if self.case.route_mode == "port":
                content = content.replace('ports "%s"' % self.listen_port,
                                          'ports "%s,%s"' % (self.listen_port, self.read_port), 1)
                marker = 'group "%s" {\n' % group
                content = content.replace(marker, marker +
                                          '    write_port %s\n' % self.listen_port, 1)
            return transform(content) if transform else content
        return super(RwToggleRuntime, self).render_conf(transform=route_transform)

    def backend_ports(self):
        ports = self.env.config["database"]["ports"]
        if self.case.topology == "mmr":
            return {
                "write": str(ports["mmr2"]),
                "read": tuple(str(ports[key]) for key in
                               ("mmr1", "mmr1_standby1", "mmr2", "mmr2_standby1")),
            }
        return {
            "write": str(ports["mmr1"]),
            "read": tuple(str(ports[key]) for key in ("mmr1", "mmr1_standby1")),
        }

    def check(self, title, expected, actual, passed):
        """Write behavioral assertions as first-class report detection items."""
        self.record_step(title, "", None, None, None)
        self.steps[-1]["checks"] = [ReportCheck(
            title, expected, actual, "PASS" if passed else "FAIL")]
        if not passed:
            raise HaCommandFailure("%s: expected %s, actual %s" %
                                   (title, expected, actual))

    def assert_backend(self, output, kind, title):
        ports = self.backend_ports()[kind]
        observed = [match for match in re.findall(r"\|\s*(\d{4,5})\s*\|", output)]
        if isinstance(ports, str):
            passed = ports in observed
            expected = "后端端口=%s" % ports
        else:
            passed = any(port in observed for port in ports)
            expected = "后端端口属于 %s" % ",".join(ports)
        self.check(title, expected,
                   "观测后端端口=%s\n%s" % (observed or ["<none>"], output.strip()),
                   passed)

    def check_product_log(self):
        """Correlate the business result with fbasecman's route log."""
        text = self.proxy_log.read_text(encoding="utf-8", errors="replace") \
            if self.proxy_log.exists() else ""
        group = "mmr_group" if self.case.topology == "mmr" else "rep_group"
        allowed_nodes = ("pg_1", "pg_2", "pg_3", "pg_4") \
            if self.case.topology == "mmr" else ("pg_1", "pg_3")
        route_lines = [line.strip() for line in text.splitlines()
                       if "route(" in line and (".%s.postgres)" % group) in line]
        route_ok = any("route(%s.%s.postgres)" % (node, group) in line
                       for node in allowed_nodes for line in route_lines)
        level_lines = [line.strip() for line in text.splitlines()
                       if re.search(r"\b(error|fatal|panic|crash)\b", line, re.IGNORECASE)]
        passed = bool(route_lines) and route_ok and not level_lines
        actual = "路由日志行:\n%s\n负向日志行:\n%s" % (
            "\n".join(route_lines[-8:]) if route_lines else "<none>",
            "\n".join(level_lines[-8:]) if level_lines else "<none>",
        )
        self.check(
            "检查 fbasecman.log 的实际路由和负向日志",
            "存在目标 group 的实际 route(...) 日志，且无 error/fatal/panic/crash",
            actual, passed)

    def run_jdbc(self, mode):
        source = self.root / "suites" / "rw_toggle" / "assets" / "RwToggleJdbc.java"
        target = self.driver_dir / source.name
        shutil.copyfile(str(source), str(target))
        jar = self.root / self.env.config["local"]["jdbc_lib_dir"] / "postgresql-42.7.7.jar"
        self.run_command(["javac", "-cp", str(jar), str(target)],
                         self.logs_dir / "RwToggleJdbc.javac.log", cwd=self.driver_dir,
                         step_title="编译 rw_toggle JDBC driver")
        group = "mmr_group" if self.case.topology == "mmr" else "rep_group"
        url = "jdbc:postgresql://127.0.0.1:%s/%s?preferQueryMode=simple" % (self.listen_port, group)
        if mode == "port":
            read_url = "jdbc:postgresql://127.0.0.1:%s/%s?preferQueryMode=simple" % (
                self.read_port, group)
            command = ["java", "-cp", "%s:%s" % (self.driver_dir, jar),
                       "RwToggleJdbc", read_url, "postgres", "", mode, url]
        else:
            command = ["java", "-cp", "%s:%s" % (self.driver_dir, jar),
                       "RwToggleJdbc", url, "postgres", "", mode]
        rc, output = self.run_command(command, self.logs_dir / "RwToggleJdbc.log",
                                      cwd=self.driver_dir, check=False,
                                      step_title="执行 rw_toggle JDBC 读写时序", record=False)
        self.record_step("执行 rw_toggle JDBC 读写时序", " ".join(command),
                         "JDBC 程序返回读写后端端口并退出成功", output.strip() or "<empty>",
                         "PASS" if rc == 0 else "FAIL")
        if rc != 0:
            raise HaCommandFailure("JDBC driver failed rc=%s" % rc)
        expected = self.backend_ports()
        read_ok = any("READ_PORT=%s" % port in output for port in expected["read"])
        write_ok = "WRITE_PORT=%s" % expected["write"] in output
        reuse_ok = "READ_AGAIN=" in output and "WRITE_AGAIN=" in output
        heartbeat_ok = "HEARTBEAT=10086" in output
        self.check(
            "验证 JDBC 读写后端路由和连接复用",
            "READ/WRITE 命中目标，读写事务各自再次执行成功，并完成 heartbeat",
            output.strip(), read_ok and write_ok and reuse_ok and heartbeat_ok)

    def finish(self, status, reason=None):
        # Keep cleanup auditable in the same report as the route assertions.
        self.record_step("清理 fbasecman 进程", "停止当前 case 的 fbasecman 实例",
                         "实例退出且监听端口释放", "清理动作已提交", "PASS")
        super(RwToggleRuntime, self).finish(status, reason)
