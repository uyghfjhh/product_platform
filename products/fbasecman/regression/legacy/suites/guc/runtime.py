"""Runtime and high-quality report support for GUC regression test cases."""

import re
import shlex
from datetime import datetime
from pathlib import Path

from platform_regress.persistence.atomic import atomic_write_text
from platform_regress.clients.psql import build_psql_command
from platform_regress.execution.command import run_logged_command
from platform_regress.reporting import ReportCheck, ReportDocument, ReportStep, render_report
from products.fbasecman.case_runtime import FbasecmanCaseRuntime
# HaCommandFailure 是全框架通用的"用例失败"异常名，GUC 套件继续沿用。
from suites.ha_commands.runtime import HaCommandFailure, _json_write


class GucRuntime(FbasecmanCaseRuntime):
    """Runtime tailored for GUC synchronization, parsing, and session reuse tests."""

    def __init__(self, root, case):
        super(GucRuntime, self).__init__(root, case)
        self.coverage_items = list(case.notes)
        self.coverage_mapping = []
        self.overview_steps = []
        self.guc_steps = []
        self.all_checks = []
        self._search_path_created_schemas = {}

    def prepare_search_path_tables(self):
        """Create distinct same-name rows on both MMR primaries for name resolution checks."""
        ports = self.env.config["database"]["ports"]
        host = self.env.config["database"]["mmr_host"]
        statements = []
        for schema, marker in (("public", "public"), ("postgres", "postgres"),
                               ("schema1", "schema1"), ("schema2", "schema2")):
            if schema != "public":
                statements.append("CREATE SCHEMA IF NOT EXISTS %s" % schema)
            statements.extend((
                "CREATE TABLE IF NOT EXISTS %s.guc_search_path_probe (marker text PRIMARY KEY)" % schema,
                "INSERT INTO %s.guc_search_path_probe VALUES ('%s') "
                "ON CONFLICT (marker) DO NOTHING" % (schema, marker),
            ))
        sql = "; ".join(statements) + ";"
        evidence = []
        for port in (ports["mmr1"], ports["mmr2"]):
            before_command = build_psql_command(
                self.env.config["local"]["postgres_dir"], host, port,
                "postgres", "postgres",
                "SELECT nspname FROM pg_namespace WHERE nspname IN ('postgres','schema1','schema2');",
                tuples_only=True,
            )
            before = run_logged_command(
                before_command, self.logs_dir / ("search_path_schemas_%s.log" % port),
                cwd=self.workdir,
            )
            if before.returncode != 0:
                raise HaCommandFailure("cannot inspect search_path schemas on port %s: %s" %
                                       (port, before.output))
            existing = {line.strip() for line in before.output.splitlines()}
            self._search_path_created_schemas[port] = tuple(
                schema for schema in ("postgres", "schema1", "schema2")
                if schema not in existing
            )
            command = build_psql_command(
                self.env.config["local"]["postgres_dir"], host, port,
                "postgres", "postgres", sql,
            )
            result = run_logged_command(
                command, self.logs_dir / ("search_path_fixture_%s.log" % port),
                cwd=self.workdir,
            )
            evidence.append("backend %s rc=%s\n%s" % (port, result.returncode, result.output))
            if result.returncode != 0:
                raise HaCommandFailure("search_path fixture setup failed on port %s: %s" %
                                       (port, result.output))
        return "准备 SQL:\n%s\n\n%s" % (sql, "\n\n".join(evidence))

    def cleanup_search_path_tables(self):
        ports = self.env.config["database"]["ports"]
        host = self.env.config["database"]["mmr_host"]
        sql = "; ".join(
            "DROP TABLE IF EXISTS %s.guc_search_path_probe" % schema
            for schema in ("public", "postgres", "schema1", "schema2")
        ) + ";"
        evidence = []
        for port in (ports["mmr1"], ports["mmr2"]):
            cleanup_sql = sql + " " + "; ".join(
                "DROP SCHEMA IF EXISTS %s" % schema
                for schema in self._search_path_created_schemas.get(port, ())
            )
            command = build_psql_command(
                self.env.config["local"]["postgres_dir"], host, port,
                "postgres", "postgres", cleanup_sql,
            )
            result = run_logged_command(
                command, self.logs_dir / ("search_path_cleanup_%s.log" % port),
                cwd=self.workdir,
            )
            evidence.append("backend %s rc=%s\n%s" % (port, result.returncode, result.output))
            if result.returncode != 0:
                raise HaCommandFailure("search_path fixture cleanup failed on port %s: %s" %
                                       (port, result.output))
        return "清理 SQL:\n%s\n\n%s" % (sql, "\n\n".join(evidence))

    def verify_search_path_table(self, path_sql, expected_schema=None):
        """Resolve an unqualified table through the proxy with the requested search_path."""
        query = "SELECT marker || '|' || current_schemas(true)::text FROM guc_search_path_probe;"
        sql = "SET search_path = %s; %s" % (path_sql, query)
        if expected_schema is None:
            schemas_sql = "SET search_path = %s; SELECT current_schemas(true)::text;" % path_sql
            schemas_output = self.psql_business(
                schemas_sql, "检查空 search_path 的有效 schema 列表",
                "current_schemas(true) 不包含 public/postgres/schema1/schema2",
                lambda out: "pg_catalog" in out and all(
                    schema not in out for schema in ("public", "postgres", "schema1", "schema2")),
            )
            output = self.psql_business_error(
                sql, "空 search_path 下未限定表名不可解析",
                "未限定 guc_search_path_probe 应报 relation does not exist",
                lambda out: "guc_search_path_probe" in out and "does not exist" in out,
            )
            expected = "relation guc_search_path_probe does not exist"
            output = "current_schemas(true):\n%s\n\n未限定表查询:\n%s" % (schemas_output, output)
        else:
            output = self.psql_business(
                sql, "验证 search_path 实际解析同名表",
                "未限定表名应命中 %s.guc_search_path_probe" % expected_schema,
                lambda out: any(line.strip().startswith(expected_schema + "|{") and
                                expected_schema in line for line in out.splitlines())
                and "(1 row)" in out,
            )
            expected = "%s 且 current_schemas(true) 包含 %s" % (expected_schema, expected_schema)
        self.add_guc_step(
            title="search_path 实际表名解析验证",
            execution="psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c %r" %
                      (self.listen_port, sql),
            intermediate=output,
            evidence=self.extract_guc_log_evidence([r"search_path", r"guc_search_path_probe"]),
            expected="未限定表名解析结果: %s" % expected,
            actual=output.strip(),
            result="PASS",
            checks=[ReportCheck(
                title="search_path 决定真实表访问结果",
                expected=expected, actual=output.strip(), result="PASS",
            )],
        )

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

        steps = list(self.guc_steps)
        if status == "FAIL" and not any(s.result == "FAIL" for s in steps):
            journal_steps = getattr(self.step_journal, "steps", [])
            for item in journal_steps:
                if item.get("result") == "FAIL" or item.get("status") == "FAIL":
                    if any(s.title == item.get("title") for s in steps):
                        continue
                    exec_cmd = ""
                    out_text = ""
                    for ex in item.get("execution", []):
                        if isinstance(ex, dict) and "text" in ex:
                            t = ex["text"]
                            if "\n\n" in t:
                                parts = t.split("\n\n", 1)
                                exec_cmd = parts[0]
                                out_text = parts[1]
                            elif t.startswith("$"):
                                exec_cmd = t
                            else:
                                out_text = t
                            break
                    intermediate_list = []
                    if out_text:
                        intermediate_list.append({"label": "中间状态", "text": "执行输出:\n" + out_text})
                    for im in item.get("intermediate", []):
                        if isinstance(im, dict):
                            intermediate_list.append(im)

                    chk_actual = out_text.strip() if out_text else item.get("actual", reason or "未达到预期")
                    chk = ReportCheck(
                        title=item.get("title", "断言校验"),
                        expected=item.get("expected", ""),
                        actual=chk_actual,
                        result="FAIL",
                    )
                    self.all_checks.append(chk)

                    cov_num = len(steps) + 1
                    step_cov_check = None
                    if cov_num <= len(self.overview_steps):
                        step_cov_check = self.overview_steps[cov_num - 1]

                    step = ReportStep(
                        title=item.get("title", "执行步骤"),
                        execution=[{"label": "实际执行", "text": exec_cmd}] if exec_cmd else [],
                        intermediate=intermediate_list,
                        evidence=item.get("evidence", []),
                        expected=item.get("expected", ""),
                        actual=item.get("actual", reason or "未达到预期"),
                        result="FAIL",
                        checks=[chk],
                        coverage=cov_num if cov_num <= len(self.coverage_items) else None,
                        coverage_check=step_cov_check,
                    )
                    steps.append(step)

            if not any(s.result == "FAIL" for s in steps):
                fail_check = ReportCheck(
                    title="用例执行断言",
                    expected="用例全部步骤成功完成",
                    actual=reason or "用例异常中止",
                    result="FAIL",
                )
                self.all_checks.append(fail_check)
                steps.append(ReportStep(
                    title="用例执行异常中止",
                    expected="所有操作成功完成",
                    actual=reason or "执行未达预期中止",
                    result="FAIL",
                    checks=[fail_check],
                ))

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
            steps=steps,
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
