"""Report writing and decision policy for active global-cache cases."""

import re

from datetime import datetime

from framework.reporting import is_transport_only_success, render_report
from framework.reporting import ReportStep
from framework.persistence.atomic import atomic_write_text
from lib.report_utils import render_psql_expanded_from_pipe_text
from suites.global_cache.reporting import build_structured_report_document


REPORT_KEEP_ZERO_STATS_CASES = {"global_capacity_reload_shrink"}
REPORT_SKIP_SERVER_STEPS_CASES = {
    "capacity_eviction_zero_ref",
    "heartbeat_reload_reclassifies_existing_normal_entry",
    "parse_invalid_error_recovery_same_connection",
    "prepare_before_bind_deploy",
}
CAPACITY_STRUCTURED_REPORT_CASES = {
    "capacity_eviction_zero_ref",
    "capacity_mixed_bypass_response_and_zero_ref_shortage",
    "ref_count_protects_active_entries",
    "global_capacity_reload_shrink",
}


def _render_psql_table_from_pipe_text(output):
    rows = []
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if "|" not in line:
            return output.rstrip("\n")
        rows.append([cell.strip() for cell in line.split("|")])
    if not rows:
        return "<empty>"
    widths = [max(len(row[index]) if index < len(row) else 0 for row in rows)
              for index in range(max(len(row) for row in rows))]

    def render_row(row):
        return " | ".join(
            (row[index] if index < len(row) else "").ljust(widths[index])
            for index in range(len(widths))
        )

    rendered = [render_row(rows[0]), "-+-".join("-" * width for width in widths)]
    rendered.extend(render_row(row) for row in rows[1:])
    count = len(rows) - 1
    rendered.append("(%d %s)" % (count, "row" if count == 1 else "rows"))
    return "\n".join(rendered)


def _condense_trace_output(title, output):
    """Only remove duplicated runtime-config noise; do not infer case semantics."""
    if not output or not (title.startswith("初始运行配置:") or title.startswith("修改运行中配置:")):
        return output
    kept = []
    seen = set()
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith(("conf :", "copy  :")) or line in seen:
            continue
        seen.add(line)
        kept.append(line)
    return "\n".join(kept) if kept else output


class GlobalCacheReportMixin(object):
    def _visible_report_steps(self):
        visible = []
        previous_console_query = None
        for source in self.step_records:
            title = source.get("title", "")
            output = source.get("output", "")
            if title == "用例断言阶段失败":
                continue
            status = self.summary.get("status")
            if status != "FAIL" and (
                title == "启动 fbasecman"
                or title.endswith(": 启动 fbasecman")
                or title.startswith("启动运行配置:")
                or ": 启动运行配置:" in title
                or title.startswith("运行配置:")
                or ": 运行配置:" in title
            ):
                continue
            if title.startswith("验证:") or ": 验证:" in title or title.startswith("解析 console 查询结果:"):
                continue
            if title.startswith("编译") or ": 编译" in title:
                continue
            if title.startswith("console 查询:"):
                query = title.split("console 查询:", 1)[1].strip()
                rendered = render_psql_expanded_from_pipe_text(output)
                if title.startswith("console 查询: SHOW GLOBAL_PREPARED_STATEMENTS;") and rendered.strip() == "(0 rows)":
                    continue
                if title.startswith("console 查询: SHOW GLOBAL_PREPARED_STATEMENTS_STATS;"):
                    if self.case.name not in REPORT_KEEP_ZERO_STATS_CASES and all(
                        token in rendered for token in (
                            "total_entries        | 0", "referenced_entries   | 0",
                            "unreferenced_entries | 0", "bypass_entries       | 0",
                            "hits                 | 0", "misses               | 0", "evictions            | 0",
                        )
                    ):
                        continue
                if title.startswith("console 查询: SHOW SERVER_PREP_STMTS;"):
                    if rendered.strip() == "(0 rows)" or self.case.name in REPORT_SKIP_SERVER_STEPS_CASES:
                        continue
                if previous_console_query == query:
                    continue
                previous_console_query = query
            else:
                previous_console_query = None
            visible.append(source)
        return visible

    def write_report(self):
        status = self.summary.get("status", "UNKNOWN")
        started_at = getattr(self, "started_at", None) or datetime.now()
        finished_at = getattr(self, "finished_at", None) or datetime.now()
        failure_reason = None
        if status == "FAIL":
            failed = self.summary.get("failed_step") or {}
            failure_reason = self.summary.get("reason") or failed.get("actual") or "存在未通过的验证项。"
        report_summary = dict(self.summary)
        report_summary["report_steps"] = self._structured_report_steps()
        document = build_structured_report_document(
            self.case,
            report_summary,
            status,
            started_at.strftime("%Y-%m-%d %H:%M:%S"),
            finished_at.strftime("%Y-%m-%d %H:%M:%S"),
            pass_reason=self._build_pass_reason() if status == "PASS" else None,
            failure_reason=failure_reason,
        )
        if document is None:
            raise RuntimeError("report builder returned no ReportDocument for %s" % self.case.target)
        evidence_steps = self._evidence_document_steps()
        if self.case.name == "heartbeat_reload_reclassifies_existing_normal_entry":
            self._merge_heartbeat_evidence(document, evidence_steps)
            evidence_steps = []
        elif self.case.name in CAPACITY_STRUCTURED_REPORT_CASES:
            self._merge_capacity_reload_execution(document, evidence_steps)
            # Capacity builders already aggregate every business SQL, cache
            # row and statistic into their three concise business steps.
            # Per-seed journal snapshots remain in steps.json for diagnosis.
            evidence_steps = []
        # Generic cases use a "验证产品行为" placeholder solely to carry checks.
        # The journal is the authoritative operation sequence, so keep real
        # JDBC/psql actions there and attach the checks to their business phase.
        placeholder = next(
            (step for step in document.steps if step.title == "验证产品行为"), None,
        )
        if placeholder is not None:
            self._attach_checks_to_evidence(
                evidence_steps, placeholder.checks,
            )
            journal_titles = {step.title for step in evidence_steps}
            suppress_standalone_console = self._has_staged_console_evidence()
            document.steps = [
                step for step in document.steps
                if step is not placeholder
                and step.title not in journal_titles
                and not (
                    suppress_standalone_console
                    and step.title.startswith(("console 查询:", "console=>"))
                )
            ]
            document.overview_steps = [
                step.title for step in document.steps + evidence_steps
            ]
        document.steps.extend(evidence_steps)
        if status == "FAIL":
            self._append_failure_step(document, self.summary.get("failed_step") or {})
        self._apply_case_coverage(document)
        atomic_write_text(self.report_file, render_report(document))

    @staticmethod
    def _merge_heartbeat_evidence(document, evidence_steps):
        """Merge phased evidence into the three business steps of this case."""
        if len(document.steps) < 3:
            return
        for evidence in evidence_steps:
            title = evidence.title
            if "初始 prepared SQL" in title:
                target = document.steps[0]
            elif "console 执行 reload" in title:
                target = document.steps[1]
            elif "reload 后 prepared SQL" in title:
                target = document.steps[2]
            else:
                # Completion output is already represented by the structured
                # JDBC result assertion and would duplicate ResultSet values.
                continue
            if "console 执行 reload" in title:
                target.execution.extend(evidence.execution)
            target.intermediate.extend(evidence.intermediate)
            target.evidence.extend(evidence.evidence)

    @staticmethod
    def _merge_capacity_reload_execution(document, evidence_steps):
        reload_step = next(
            (step for step in document.steps if "reload" in step.title.lower()), None,
        )
        reload_evidence = next(
            (step for step in evidence_steps if "console 执行 reload" in step.title), None,
        )
        if reload_step is not None and reload_evidence is not None:
            reload_step.execution.extend(reload_evidence.execution)

    def _apply_case_coverage(self, document):
        """Attach the declared test objective to every rendered report step."""
        document.coverage_title = "测试内容"
        document.coverage_items = list(self.case.test_contents)
        setup_pattern = re.compile(
            r"^(启动|编译|准备|创建测试|初始运行配置|运行配置|修改运行中配置)"
        )
        mappings = []
        all_contents = "1" if len(self.case.test_contents) == 1 else (
            "1 至 %d" % len(self.case.test_contents)
        )
        for index, step in enumerate(document.steps, 1):
            if setup_pattern.search(step.title):
                coverage = "前置条件"
                check = "准备本用例所需的代理、driver、配置或业务数据"
            else:
                coverage = None
                check = None
                for expression, candidate, description in self.case.step_rules:
                    if re.search(expression, step.title, re.IGNORECASE):
                        coverage, check = candidate, description
                        break
                if coverage is None:
                    coverage = all_contents
                    check = step.title
            step.coverage = coverage
            step.coverage_check = check
            mappings.append((str(index), coverage, check))
        document.coverage_mapping = mappings

    def _evidence_document_steps(self):
        journal = getattr(self, "step_journal", None)
        if journal is None:
            return []
        steps = []
        suppress_standalone_console = self._has_staged_console_evidence()
        for item in sorted(journal.steps, key=lambda source: source.get("order", 0)):
            # Standalone snapshots are framework baselines/final collection.
            # Their useful state is shown at the JDBC pause that caused it;
            # showing an empty pre-run console as a report step is misleading.
            if suppress_standalone_console and item["title"].startswith("console 查询:"):
                continue
            transport_only = is_transport_only_success(
                item.get("expected"), item.get("result"),
            )
            observation_only = bool(item.get("observation_only"))
            steps.append(ReportStep(
                item["title"], execution=item.get("execution", []),
                intermediate=item.get("intermediate", []), evidence=item.get("evidence", []),
                key_expected=None if transport_only else item.get("expected") or None,
                actual=None if transport_only or observation_only else item.get("actual") or None,
                result=None if transport_only or observation_only else item.get("result") or None,
            ))
        return steps

    def _has_staged_console_evidence(self):
        """Whether JDBC phases already contain complete console observations."""
        journal = getattr(self, "step_journal", None)
        if journal is None:
            return False
        return any(
            "console 查询 global/server cache" in item.get("text", "")
            for step in journal.steps
            for item in step.get("intermediate", [])
        )

    @staticmethod
    def _attach_checks_to_evidence(steps, checks):
        """Place generic product checks below the JDBC action they describe."""
        phase_titles = {
            "单连接复用": "首次 PreparedStatement 后观察 global cache",
            "跨客户端复用": "第一个 JDBC 客户端释放后观察共享 entry",
        }
        fallback = next(
            (step for step in reversed(steps) if step.execution or step.intermediate),
            steps[-1] if steps else None,
        )
        if fallback is None:
            return
        for check in checks:
            phase, separator, _ = check.title.partition(":")
            target = None
            expected_title = phase_titles.get(phase) if separator else None
            if expected_title:
                target = next(
                    (step for step in steps if step.title == expected_title), None,
                )
            (target or fallback).checks.append(check)

    @staticmethod
    def _append_failure_step(document, failed):
        """Never let a specialised PASS-looking summary hide the stop point."""
        if not failed:
            return
        failed_title = failed.get("title", "未记录步骤")
        if failed_title == "用例断言阶段失败" and any(
                step.result == "FAIL" for step in document.steps):
            return
        if any(step.title == failed_title and step.result == "FAIL" for step in document.steps):
            return
        details = []
        command = failed.get("command", "").strip()
        if command:
            details.append(("失败命令", command))
        output = failed.get("output", "").strip()
        if output:
            details.append(("原始输出", output))
        document.steps.append(ReportStep(
            "执行中断: %s" % failed_title,
            details=details,
            expected=failed.get("expected") or "该步骤完成并继续执行后续业务验证。",
            actual=failed.get("actual") or "步骤异常退出。",
            result="FAIL",
        ))

    def _structured_report_steps(self):
        steps = []
        for source in self._visible_report_steps():
            step = dict(source)
            title = step.get("title", "")
            output = step.get("output", "")
            if title.startswith("console 查询:"):
                step["title"] = "console=>" + title.split("console 查询:", 1)[1].strip()
                output = _render_psql_table_from_pipe_text(output)
            step["output"] = _condense_trace_output(title, output)
            # A command completing is not, by itself, a product assertion.  Keep
            # setup and driver activity visible, but render expected/actual/PASS
            # only when the scenario explicitly recorded a business contract.
            steps.append(step)
        return steps

    def _build_pass_reason(self):
        checks = [item for item in self.summary.get("verification_checks", []) if item.get("result") == "PASS"]
        if not checks:
            return "用例定义的业务步骤已完成，未发现违反产品契约的行为。"
        titles = [item.get("title", "未命名检测项") for item in checks]
        reason = "；".join(titles[:3])
        if len(titles) > 3:
            reason += "；另有 %d 项检测通过" % (len(titles) - 3)
        return "共 %d 个产品行为检测项通过：%s。" % (len(titles), reason)
