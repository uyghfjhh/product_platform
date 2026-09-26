import datetime
import shutil
import shlex
import time
from pathlib import Path

from framework.artifacts import (ClusterRunLock, create_run_id, run_directory,
                                 write_run_summary)
from framework.catalog import (long_time_cases_for_plugins, resolve_target,
                               select_cases, select_cases_for_plugins)
from suites import SUITES
from framework.context import TestContext
from framework.environment import EnvironmentManager
from framework.errors import ConfigError, SafetyError
from framework.evidence import CoreCollector
from framework.fixtures import FIXTURES
from framework.models import CaseResult, StepResult
from framework.reporting import render_case_report
from framework.server_logs import ServerLogCollector
from framework.steps import StepExecutor


class SuiteRunner(object):
    def __init__(self, config, cluster_name):
        self.config = config
        self.cluster_name = cluster_name
        self.manager = EnvironmentManager(config, cluster_name)

    def run(self, target, enable_run_id=False, include_all=False,
            longtime_only=False):
        if target:
            if include_all or longtime_only:
                raise ConfigError("指定 target 时不能同时使用 --all 或 --longtime")
            targets = [resolve_target(item.strip())
                       for item in target.split(",") if item.strip()]
            if not targets:
                raise ConfigError("target 不能为空")
            cases = []
            seen = set()
            for resolved in targets:
                for case in select_cases(resolved):
                    if case["id"] not in seen:
                        cases.append(case)
                        seen.add(case["id"])
            target = ",".join(targets)
        else:
            cases = (long_time_cases_for_plugins(self.manager.plugins)
                     if longtime_only else
                     select_cases_for_plugins(self.manager.plugins,
                                              include_all=include_all))
            cases = self._group_session_cases(cases)
        if not cases:
            if target:
                raise ConfigError("target 尚无可执行用例: %s" % target)
            plugins = ",".join(sorted(self.manager.plugins)) or "-"
            raise ConfigError(
                "cluster %s 的已启用插件未匹配任何已注册用例: %s" %
                (self.cluster_name, plugins))
        run_id = create_run_id() if enable_run_id else "default"
        run_dir = (run_directory(self.config.root, self.cluster_name, run_id)
                   if enable_run_id else self.config.root / "output" / self.cluster_name)
        started = datetime.datetime.now()
        started_monotonic = time.monotonic()
        with ClusterRunLock(self.config.root, self.cluster_name,
                            self.manager.lock_identity()):
            if not enable_run_id and run_dir.exists():
                shutil.rmtree(str(run_dir))
            run_dir.mkdir(parents=True, exist_ok=not enable_run_id)
            state = self._ensure_environment_started()
            print("----------------------%s---------------------------------" % self.cluster_name)
            if enable_run_id:
                print("RUN ID: %s" % run_id)
            failed = False
            records = []
            non_success_results = []
            sessions = {}
            try:
                for case in cases:
                    session_errors = self._cleanup_inactive_sessions(
                        sessions, self._session_key(case))
                    if session_errors:
                        failed = True
                        for error in session_errors:
                            print("共享会话清理失败: %s" % error)
                    isolated_mmr = self._uses_isolated_mmr_topology(case)
                    blocker = "" if isolated_mmr else self._environment_blocker(state)
                    requirement_blocker = ""
                    if not blocker:
                        node_blocker = "" if isolated_mmr else self._case_node_blocker(case)
                        if node_blocker:
                            requirement_blocker = node_blocker
                        else:
                            basic_requirements = dict(case.get("requirements") or {})
                            basic_requirements.pop("settings", None)
                            # Disposable MMR topologies create their own writable
                            # nodes.  A shared MMR health failure is unrelated.
                            if isolated_mmr:
                                basic_requirements.pop("writable_node", None)
                            requirement_blocker = self.manager.check_requirements(basic_requirements)
                    case_blocker = blocker or requirement_blocker
                    if not case_blocker:
                        case_blocker = self._session_blocker(
                            case, sessions, run_dir, run_id)
                    session = sessions.get(self._session_key(case)) or {}
                    case_started = datetime.datetime.now()
                    result = self._run_case(
                        case, state, case_blocker, run_dir,
                        session_values=session.get("shared_values"))
                    case_finished = datetime.datetime.now()
                    records.append(self._summary_record(
                        case, result, case_started, case_finished))
                    print("%-60s %-16s %8.3fs" % (
                        case["id"], self._display_case_status(case, result.status),
                        result.duration_seconds))
                    if result.status in ("FAILED", "BLOCKED"):
                        non_success_results.append((case, result))
                        failed = True
            finally:
                session_errors = self._cleanup_sessions(sessions)
                if session_errors:
                    failed = True
                    for error in session_errors:
                        print("共享会话清理失败: %s" % error)
            write_run_summary(
                run_dir, run_id, self.cluster_name, target, state,
                self.manager.plugins, records, started, datetime.datetime.now(),
                duration_seconds=time.monotonic() - started_monotonic)
            self._print_run_summary(
                records, total_duration=time.monotonic() - started_monotonic,
                known_issues=self._known_issues_for_run(cases) if target is None else [])
            self._print_non_success_details(non_success_results)
        return 1 if failed else 0

    @staticmethod
    def _uses_isolated_mmr_topology(case):
        return any(isinstance(fixture, dict) and
                   fixture.get("type") == "isolated_mmr_node_creation"
                   for fixture in case.get("fixtures") or [])

    def _session_blocker(self, case, sessions, run_dir, run_id):
        """Start a reusable fixture session once and retain it for sibling cases."""
        spec = case.get("session")
        if not spec:
            return ""
        key = spec["key"]
        session = sessions.get(key)
        if session is not None:
            return session["error"]
        session_dir = run_dir / "_sessions" / key
        session_dir.mkdir(parents=True, exist_ok=True)
        session_case = {"id": "_session.%s" % key,
                        "steps": spec.get("steps") or []}
        context = TestContext(self.config, self.manager, session_case, session_dir,
                              run_id=run_id)
        error = ""
        try:
            FIXTURES.setup_all(context, spec.get("fixtures") or [])
            for step in spec.get("steps") or []:
                result = StepExecutor(context).execute(step)
                if result.status != "SUCCESS":
                    raise SafetyError("共享会话初始化步骤 %s 失败: %s" % (
                        step.get("title", "<unknown>"),
                        result.reason or result.actual))
        except Exception as exc:
            error = "共享会话 %s 初始化失败: %s" % (key, exc)
        sessions[key] = {
            "context": context,
            "error": error,
            "shared_values": {
                "isolated_mmr_port_mapping": dict(
                    context.values.get("isolated_mmr_port_mapping") or {}),
            },
        }
        return error

    @staticmethod
    def _session_key(case):
        spec = case.get("session") or {}
        return spec.get("key")

    @classmethod
    def _group_session_cases(cls, cases):
        """Keep each reusable session contiguous in an implicit full-suite run."""
        by_key = {}
        for index, case in enumerate(cases):
            key = cls._session_key(case)
            if key:
                by_key.setdefault(key, []).append((
                    (case.get("session") or {}).get("order", index), index, case))
        for key in by_key:
            by_key[key].sort(key=lambda item: (item[0], item[1]))
        emitted = set()
        ordered = []
        for case in cases:
            key = cls._session_key(case)
            if not key:
                ordered.append(case)
            elif key not in emitted:
                ordered.extend(item[2] for item in by_key[key])
                emitted.add(key)
        return ordered

    def _cleanup_inactive_sessions(self, sessions, active_key):
        """Close sessions before an unrelated case can reuse their ports."""
        inactive = {key: sessions.pop(key) for key in list(sessions)
                    if key != active_key}
        return self._cleanup_sessions(inactive)

    @staticmethod
    def _cleanup_sessions(sessions):
        errors = []
        for key, session in reversed(list(sessions.items())):
            for error in session["context"].cleanup():
                errors.append("%s: %s" % (key, error))
        return errors

    @staticmethod
    def _display_status(status):
        return "FAIL" if status == "FAILED" else status

    @classmethod
    def _display_case_status(cls, case, status):
        display = cls._display_status(status)
        if status == "FAILED" and case.get("known_issue"):
            return "%s [%s]" % (display, case["known_issue"])
        return display

    @classmethod
    def _print_run_summary(cls, records, total_duration=None, known_issues=None):
        counts = {"SUCCESS": 0, "FAIL": 0, "BLOCKED": 0}
        for record in records:
            status = cls._display_status(record["status"])
            if status in counts:
                counts[status] += 1
        print("-" * 85)
        print("Total:")
        for status in ("SUCCESS", "FAIL", "BLOCKED"):
            print("        %s:%s" % (status, counts[status]))
        if total_duration is not None:
            print("总耗时:%.3fs" % total_duration)
        if known_issues:
            print("已登记产品问题:%s" % len(known_issues))
            for issue, case_ids, executed in known_issues:
                state = "本次已执行" if executed else "[LONG-TIME] 默认未执行"
                print("        %s: %s (%s)" % (
                    issue, state, ", ".join(case_ids)))

    def _known_issues_for_run(self, executed_cases):
        """Show compatible issue cases and whether this invocation selected them."""
        executed_ids = {case["id"] for case in executed_cases}
        by_issue = {}
        for case in select_cases_for_plugins(self.manager.plugins, include_all=True):
            issue = case.get("known_issue")
            if issue:
                entry = by_issue.setdefault(issue, [[], False])
                entry[0].append(case["id"])
                entry[1] = entry[1] or case["id"] in executed_ids
        return [(issue, values[0], values[1])
                for issue, values in sorted(by_issue.items())]

    def _print_non_success_details(self, results):
        if not results:
            return
        print("Details:")
        for case, result in results:
            print("%-60s %s" % (
                case["id"], self._display_case_status(case, result.status)))
            self._print_failure(result)

    def _summary_record(self, case, result, started, finished):
        evidence = [result.output_dir / "execution.log"]
        for path in result.server_evidence:
            path = Path(path)
            evidence.append(path if path.is_absolute() else result.output_dir / path)
        for path in result.core_files:
            evidence.append(path if ":/" in str(path) else Path(path))
        return {
            "id": case["id"],
            "status": result.status,
            "known_issue": case.get("known_issue", ""),
            "duration_seconds": round(
                result.duration_seconds if result.duration_seconds is not None
                else (finished - started).total_seconds(), 3),
            "report": str((result.output_dir / "report.txt").relative_to(self.config.root)),
            "evidence": [
                str(path.relative_to(self.config.root))
                if isinstance(path, Path) and not path.is_absolute()
                else str(path)
                for path in evidence
            ],
            "reason": self._result_reason(result),
        }

    @staticmethod
    def _result_reason(result):
        if result.blocker:
            return result.blocker
        if result.error:
            return result.error
        for item in result.steps:
            if item.status != "SUCCESS":
                return item.reason or item.actual
        if result.cleanup_errors:
            return result.cleanup_errors[0]
        return ""

    def _complete_setting_details(self, case, actual_details):
        details = list(actual_details)
        existing = {(item["node"], item["name"]) for item in details}
        requirements = case.get("requirements") or {}
        for spec in requirements.get("settings", []):
            selector = spec.get("node", requirements.get("node", "primary"))
            try:
                node = self.manager.resolve_node(selector)
            except ConfigError:
                node = selector
            key = (node, spec["name"])
            if key in existing:
                continue
            requirement = ("等于 %s" % spec["equals"] if "equals" in spec
                           else "包含 %s" % spec["contains"])
            details.append({
                "source": "requirement", "node": node, "name": spec["name"],
                "config_sql": "SELECT ... FROM pg_file_settings WHERE name = '%s'" % spec["name"],
                "config_output": "<未检查>",
                "config_error": "环境或前置条件不可用，未查询配置文件",
                "sql": "SHOW %s" % spec["name"], "output": "<未检查>",
                "actual": "<未检查>", "requirement": requirement,
                "purpose": spec["purpose"], "matched": False,
                "error": "环境或前置条件不可用，未执行 SHOW",
            })
        return details

    def _print_failure(self, result):
        label = "阻塞原因" if result.status == "BLOCKED" else "失败原因"
        reasons = []
        if result.blocker:
            reasons.append(result.blocker)
        if result.error:
            reasons.append(result.error)
        for index, item in enumerate(result.steps, 1):
            if item.status == "FAILED":
                reasons.append("第 %s 步 %s: %s" %
                               (index, item.step["title"],
                                item.reason or item.actual or "失败"))
        reasons.extend("清理失败: %s" % error for error in result.cleanup_errors)
        for reason in reasons or ["用例未成功，详情请查看 report.txt"]:
            print("  %s: %s" % (label, self._compact_console_text(reason)))
        report_path = result.output_dir / "report.txt"
        print("  REPORT: %s" % report_path.relative_to(self.config.root))
        for core_file in result.core_files:
            print("  CORE: %s" % core_file)
            print("  GDB: gdb %s %s" % (
                shlex.quote(str(self._postgres_binary())),
                shlex.quote(str(core_file))))

    def _postgres_binary(self):
        manager = getattr(self, "manager", None)
        return manager.binary("postgres") if manager else "postgres"

    @staticmethod
    def _compact_console_text(value, limit=240):
        text = " ".join(str(value).split())
        return text if len(text) <= limit else text[:limit - 3] + "..."

    def _environment_blocker(self, state):
        if state.get("state") != "running":
            return "cluster %s 未运行（state=%s）" % (
                self.cluster_name, state.get("state", "-"))
        self.manager.managed_state()
        try:
            _, rows = self.manager.status_rows()
        except Exception as exc:
            return "无法检查 cluster 状态: %s" % exc
        if not any(row[4] == "running" and row[6] == "healthy" for row in rows):
            return "cluster %s 没有健康节点" % self.cluster_name
        return ""

    def _case_node_blocker(self, case):
        try:
            node_names = self._declared_nodes(case)
            _, rows = self.manager.status_rows()
        except Exception as exc:
            return "无法检查用例涉及节点: %s" % exc
        status_by_node = {row[0]: row for row in rows}
        unhealthy = []
        for name in node_names:
            row = status_by_node.get(name)
            if not row:
                unhealthy.append("%s=未返回状态" % name)
            elif row[4] != "running" or row[6] != "healthy":
                unhealthy.append("%s=process:%s,health:%s" %
                                 (name, row[4], row[6]))
        if unhealthy:
            return "用例涉及节点不可用: %s" % "; ".join(unhealthy)
        return ""

    def _declared_nodes(self, case, context=None):
        selectors = list(case.get("evidence_nodes") or [])
        for step in case["steps"]:
            if step.get("type", "sql") == "cluster_action":
                return list(self.manager.nodes)
            selectors.append(step.get("node", "primary"))
        for fixture in case.get("fixtures") or []:
            if not isinstance(fixture, dict):
                continue
            selectors.extend(fixture.get("nodes") or [])
            if fixture.get("node"):
                selectors.append(fixture["node"])
            elif fixture.get("type") in ("database", "roles", "settings"):
                selectors.append("primary")
        if not selectors:
            selectors.append("primary")
        result = []
        for selector in selectors:
            name = (context.resolve_node(selector) if context else
                    self.manager.resolve_node(selector))
            if name not in result:
                result.append(name)
        return result

    def _log_collectors(self, node_names, case_dir, transport):
        multiple = len(node_names) > 1
        return [ServerLogCollector(
            self.manager, name, case_dir, label=name if multiple else None,
            transport=transport)
            for name in node_names]

    @staticmethod
    def _blocked_steps(case, reason):
        return [StepResult(step, "BLOCKED", "未执行", "未执行", reason)
                for step in case["steps"]]

    def _run_case(self, case, state, blocker, run_dir, session_values=None):
        case_dir = run_dir / Path(*case["id"].split("."))
        case_dir.mkdir(parents=True, exist_ok=True)
        for pattern in ("execution.log", "postgresql*.log", "postgresql*.csv"):
            for artifact in case_dir.glob(pattern):
                artifact.unlink()

        started = datetime.datetime.now()
        started_monotonic = time.monotonic()
        context = TestContext(self.config, self.manager, case, case_dir,
                              run_id=run_dir.name)
        context.values.update(session_values or {})
        core_collector = CoreCollector(
            self.manager, case_dir, transport=context.transport)
        core_collector.start()
        step_results = []
        server_evidence = []
        server_log_errors = []
        cleanup_errors = []
        error = ""
        collectors = []
        setting_details = []

        if blocker:
            context.log_path.write_text("BLOCKED: %s\n" % blocker, encoding="utf-8")
            step_results = self._blocked_steps(case, blocker)
            setting_details = self._complete_setting_details(case, [])
            status = "BLOCKED"
        else:
            status = "SUCCESS"
            try:
                nodes = self._declared_nodes(case, context)
                collectors = self._log_collectors(nodes, case_dir, context.transport)
                for collector in collectors:
                    collector.start()
                requirements = case.get("requirements") or {}
                setting_requirements = {
                    "node": requirements.get("node", "primary"),
                    "settings": requirements.get("settings", []),
                }
                setting_blocker, setting_details = self.manager.evaluate_requirements(
                    setting_requirements, postgres_client=context.postgres)
                if setting_blocker:
                    blocker = setting_blocker
                    status = "BLOCKED"
                    step_results = self._blocked_steps(case, blocker)
                else:
                    FIXTURES.setup_all(context, case.get("fixtures") or [])
                    executor = StepExecutor(context)
                    halted = False
                    case_failed = False
                    for step in case["steps"]:
                        if halted:
                            step_results.append(StepResult(
                                step, "BLOCKED", "未执行", "未执行",
                                "前序步骤失败，当前步骤不再具备有效前置条件"))
                            continue
                        try:
                            result = executor.execute(step)
                        except Exception as exc:
                            result = StepResult(
                                step, "FAILED", "执行器异常", "<无输出>", str(exc))
                        step_results.append(result)
                        if result.status == "FAILED":
                            case_failed = True
                            halted = not step.get("continue_on_failure", False)
                    if case_failed:
                        status = "FAILED"
            except SafetyError as exc:
                blocker = str(exc)
                status = "BLOCKED"
                step_results = self._blocked_steps(case, blocker)
            except Exception as exc:
                status = "FAILED"
                error = "fixture/setup 异常: %s" % exc
                step_results = self._blocked_steps(case, error)
            finally:
                cleanup_errors = context.cleanup()
                if cleanup_errors:
                    status = "FAILED"
                server_evidence.extend(context.values.get("case_evidence", []))
                for collector in collectors:
                    server_evidence.extend(collector.finish(
                        allow_missing_before=context.values.get("server_log_reset", False)))
                    server_log_errors.extend(collector.errors)

        status, error = self._with_server_log_errors(
            status, error, server_log_errors)

        finished = datetime.datetime.now()
        result = CaseResult(
            status, step_results, case_dir, blocker=blocker,
            server_evidence=server_evidence,
            server_log_errors=server_log_errors,
            core_files=core_collector.finish(),
            cleanup_errors=cleanup_errors,
            error=error,
            setting_details=setting_details +
                            context.values.get("postgresql_settings", []),
            duration_seconds=time.monotonic() - started_monotonic,
        )
        node_names = context.touched_nodes or [self.manager.resolve_node("primary")]
        nodes = []
        for name in node_names:
            node = dict(self.manager.node(name))
            node["name"] = name
            nodes.append(node)
        report_case = dict(case)
        test_topology = case.get("test_topology") or context.values.get("test_topology")
        if test_topology:
            report_case["test_topology"] = context.expand(test_topology)
        report = render_case_report(
            report_case, result, self.cluster_name, state.get("env_id", "-"),
            self.manager.plugins, nodes, started, finished,
            postgres_binary=self.manager.binary("postgres"),
            environment_topology=self._report_topology())
        (case_dir / "report.txt").write_text(report, encoding="utf-8")
        return result

    def _report_topology(self):
        roles = self.manager.roles()
        report_names = self.manager.cluster.get("report_nodes") or {}

        def report_name(name):
            return report_names.get(name, name)

        nodes = []
        for name, node in self.manager.nodes.items():
            nodes.append({"name": report_name(name), "role": roles.get(name, "standalone"),
                          "host": node["host"], "port": node["port"],
                          "data_dir": node["data_dir"]})
        physical = self.manager.physical_relations()
        logical = self.manager.groups.get("logical") or {}
        mmr = self.manager.groups.get("mmr") or {}
        members = mmr.get("members") or {}
        relations = ["物理流复制: %s -> %s" % (report_name(primary), report_name(standby))
                     for primary, standby in physical]
        for subscriber, options in (logical.get("subscribers") or {}).items():
            relations.append("普通逻辑复制: %s -> %s (publication=%s, subscription=%s)" % (
                report_name(logical.get("publisher", "-")), report_name(subscriber),
                logical.get("publication_name", "-"),
                options.get("subscription_name", "-")))
        if members:
            member_names = []
            for member, options in members.items():
                member_names.append(report_name(options.get("primary", member)))
            relations.append("MMR 多活: group=%s members=%s" % (
                mmr.get("group_name", "-"), ",".join(member_names)))
        return {"summary": "节点数=%s；物理主备=%s；普通逻辑复制=%s；MMR 成员=%s" % (
                    len(nodes), len(physical),
                    len(logical.get("subscribers") or {}), len(members)),
                "nodes": nodes, "relations": relations,
                "report_nodes": report_names}

    def _ensure_environment_started(self):
        """Start stopped managed nodes before evaluating normal case requirements."""
        state = self.manager.store.load()
        should_start = state.get("state") != "running"
        if not should_start:
            # Do not call fdd.show_node_info here.  A product UDF health check
            # can itself wait on application DDL; pg_ctl status is sufficient
            # to decide whether a managed postmaster needs starting.
            should_start = any(
                self.manager._pg_ctl(name, "status", check=False).returncode != 0
                for name in self.manager.nodes)
        if should_start:
            self.manager.start()
            state = self.manager.store.load()
        return state

    @staticmethod
    def _with_server_log_errors(status, error, server_log_errors):
        if not server_log_errors:
            return status, error
        log_error = "服务端日志收集失败: %s" % "; ".join(server_log_errors)
        return "FAILED", "%s；%s" % (error, log_error) if error else log_error
