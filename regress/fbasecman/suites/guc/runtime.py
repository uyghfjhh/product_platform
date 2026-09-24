"""Runtime and high-quality report support for GUC regression test cases."""

import re
import shlex
from datetime import datetime
from pathlib import Path

from framework.persistence.atomic import atomic_write_text
from framework.reporting import ReportCheck, ReportDocument, ReportStep, render_report
from suites.ha_commands.runtime import HaCommandFailure, HaCommandRuntime, _json_write


class GucRuntime(HaCommandRuntime):
    """Runtime tailored for GUC synchronization, parsing, and session reuse tests."""

    def __init__(self, root, case):
        super(GucRuntime, self).__init__(root, case)
        self.coverage_items = list(case.notes)
        self.coverage_mapping = []
        self.overview_steps = []
        self.guc_steps = []
        self.all_checks = []

    def render_conf(self, transform=None):
        def apply_guc_settings(content):
            # Ensure rw_split_method matches the case's specified route mode
            rw_split = getattr(self.case, "rw_split_method", "sql_parse")
            content = re.sub(
                r'rw_split_method\s+"[^"]+"',
                'rw_split_method "%s"' % rw_split,
                content,
            )
            # When rw_split_method is not 'none', single/balance groups are not supported
            if rw_split != "none":
                content = content.replace(
                    'group_names "mmr_group,rep_group,balance_group,single_group"',
                    'group_names "mmr_group,rep_group"',
                )
            # Ensure enable_guc_sync is enabled
            if "enable_guc_sync yes" not in content:
                content = "enable_guc_sync yes\n" + content
            if transform is not None:
                content = transform(content)
            return content

        return super(GucRuntime, self).render_conf(transform=apply_guc_settings)

    def extract_conf_evidence(self, keys=None):
        """Extract configuration snippets from the active fbasecman.conf as evidence."""
        conf_path = self.active_conf or (self.workdir / (self.case.name + ".conf"))
        if not conf_path.exists():
            return "配置文件未找到: %s" % conf_path
        if keys is None:
            keys = ["enable_guc_sync", "rw_split_method", "pool", "pool_discard", "pool_size"]
        lines = []
        try:
            content = conf_path.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                line_clean = line.strip()
                if any(k in line_clean for k in keys):
                    lines.append(line_clean)
        except Exception as e:
            return "读取配置文件异常: %s" % e
        return "\n".join(lines) if lines else "配置文件中未找到匹配的关键字"

    def extract_guc_log_evidence(self, patterns=None, max_lines=8):
        """Extract relevant log snippets from fbasecman.log for evidence."""
        if not self.proxy_log.exists():
            return "日志文件尚未生成"

        if patterns is None:
            patterns = [
                r"guc-sync",
                r"ParameterStatus",
                r"search_path",
                r"fb_hint_parse_guc_batch",
                r"fb_guc_deploy",
                r"fb_sql_parse_normalize_report_guc",
            ]

        regexes = [re.compile(p, re.IGNORECASE) for p in patterns]
        matched = []
        try:
            with self.proxy_log.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line_clean = line.strip()
                    if any(rx.search(line_clean) for rx in regexes):
                        matched.append(line_clean)
        except Exception as e:
            return "读取日志异常: %s" % e

        if not matched:
            return "无匹配的 GUC 同步或解析日志（未触发异常或日志级别为常规）"

        # Return the most recent matching lines up to max_lines
        recent = matched[-max_lines:]
        return "\n".join(recent)

    def add_guc_step(self, title, execution=None, intermediate=None, evidence=None,
                     expected=None, actual=None, result="PASS", checks=None,
                     coverage=None, coverage_check=None):
        """Record a structured step aligned with high_availability report standard."""
        step_execution = []
        if execution:
            if isinstance(execution, list):
                step_execution = execution
            else:
                step_execution = [{"label": "实际执行", "text": str(execution)}]

        step_intermediate = []
        if intermediate:
            if isinstance(intermediate, list):
                step_intermediate = intermediate
            else:
                step_intermediate = [{"label": "中间状态", "text": str(intermediate)}]

        step_evidence = []
        if evidence:
            if isinstance(evidence, list):
                step_evidence = evidence
            else:
                step_evidence = [{"label": "证据", "text": str(evidence)}]

        step_checks = []
        if checks:
            for item in checks:
                if isinstance(item, ReportCheck):
                    step_checks.append(item)
                    self.all_checks.append(item)
                elif isinstance(item, (tuple, list)) and len(item) == 4:
                    chk = ReportCheck(*item)
                    step_checks.append(chk)
                    self.all_checks.append(chk)

        step = ReportStep(
            title=title,
            execution=step_execution,
            intermediate=step_intermediate,
            evidence=step_evidence,
            expected=expected,
            actual=actual,
            result=result,
            checks=step_checks,
            coverage=coverage,
            coverage_check=coverage_check,
        )
        self.guc_steps.append(step)
        return step

    def write_report(self, status, reason=None):
        """Generate high-standard report document matching high_availability suite."""
        db_cfg = self.env.config["database"]
        ports = db_cfg["ports"]

        rw_method = getattr(self.case, "rw_split_method", "sql_parse")
        rw_desc = "SQL_PARSE 模式 (SQL 语法解析模式)" if rw_method == "sql_parse" else "HINT 模式 (Hint 标签引导模式)"
        conf_snippets = self.extract_conf_evidence()

        config_lines = [
            "测试拓扑: %s" % self.case.topology,
            "【测试模式】: rw_split_method = %s (%s)" % (rw_method, rw_desc),
            "【GUC 同步开关】: enable_guc_sync = yes",
            "业务端口 (代理监听): %s, 控制台端口: %s" % (self.listen_port, self.listen_port),
            "连接池配置: pool=transaction, pool_size=20, pool_discard=no (支持事务级连接复用)",
            "后端数据库节点:",
            "  - mmr1 (主节点): %s:%s" % (db_cfg["mmr_host"], ports["mmr1"]),
            "  - mmr2 (主节点): %s:%s" % (db_cfg["mmr_host"], ports["mmr2"]),
            "【配置文件生效字段作证】:\n%s" % "\n".join("    | " + l for l in conf_snippets.splitlines()),
            "手动启动命令: %s %s --console --log_to_stdout" % (
                shlex.quote(str(self.process.binary)),
                shlex.quote(str(self.active_conf or (self.workdir / (self.case.name + ".conf")))),
            ),
        ]

        # Determine pass/failure summary reason
        if status == "PASS":
            pass_reason = reason or "search_path GUC 规范化与连接复用恢复测试通过；报告所列操作均执行成功，全部检测项符合预期。"
            failure_reason = None
        else:
            pass_reason = None
            failure_reason = reason or "GUC 测试步骤或检测项未达到预期。"

        doc = ReportDocument(
            target=self.case.target,
            status=status,
            started_at=self.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            finished_at=(self.finished_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
            purpose=self.case.summary,
            config_lines=config_lines,
            coverage_items=self.coverage_items,
            coverage_mapping=self.coverage_mapping,
            coverage_title="测试内容",
            overview_steps=self.overview_steps,
            steps=self.guc_steps,
            pass_reason=pass_reason,
            failure_reason=failure_reason,
        )

        rendered = render_report(doc)
        atomic_write_text(self.run_root / "report.txt", rendered)
        _json_write(self.run_root / "summary.json", {
            "target": self.case.target,
            "status": status,
            "reason": reason,
            "source_sections": self.case.source_sections,
            "checks_total": len(self.all_checks),
            "checks_passed": sum(1 for c in self.all_checks if c.result == "PASS"),
        })
