import re
import shlex
from pathlib import Path


def _topology_lines(title, topology):
    if not topology:
        return [title, "  无"]
    lines = [title]
    if topology.get("summary"):
        lines.append("  %s" % topology["summary"])
    nodes = topology.get("nodes") or []
    if nodes:
        lines.append("  节点:")
        for node in nodes:
            line = "    - %s: role=%s host=%s port=%s" % (
                node.get("name", "-"), node.get("role", "-"),
                node.get("host", "-"), node.get("port", "-"))
            if node.get("data_dir"):
                line += " data_dir=%s" % node["data_dir"]
            lines.append(line)
    for relation in topology.get("relations") or []:
        lines.append("  关系: %s" % relation)
    if len(lines) == 1:
        lines.append("  无")
    return lines


def _report_node(step, result_node, topology):
    if step.get("report_node"):
        return step["report_node"]
    mapped = (topology or {}).get("report_nodes", {}).get(step.get("node"))
    if mapped:
        return mapped
    if step.get("type") != "command" or not topology:
        return result_node
    command = " ".join(str(item) for item in step.get("argv") or [])
    ports = set(re.findall(r"\s-p\s+(\d+)\b", command))
    if not ports:
        ports = set(re.findall(r"\bport=(\d+)\b", command))
    names = [node.get("name", "-") for node in topology.get("nodes") or []
             if str(node.get("port")) in ports]
    if not names:
        return result_node
    return ", ".join(names)


def _fixture_name(spec):
    return spec if isinstance(spec, str) else spec.get("type", "<unknown>")


def _conflict_configuration_lines(case):
    """Render the mode which makes a conflict-result assertion meaningful."""
    config = case.get("conflict_configuration")
    if config:
        lines = ["冲突处理关键配置:"]
        lines.extend("  - %s" % item for item in config)
        return lines + [""]
    if case.get("group") != "conflict":
        return []
    return [
        "冲突处理关键配置:",
        "  - 发布端/订阅端 debug_logical_replication_streaming=buffered。",
        "  - 发布端/订阅端 logical_decoding_work_mem=64MB。",
        "  - MMR 节点 streaming=off，two_phase=false；不是 streaming 冲突模式。",
        "  - 各冲突处理策略以验证步骤中读取或设置的 fdd.mmr_conflict_resolvers 为准。",
        "",
    ]


def _was_not_executed(step_result):
    """Runner placeholders after a failed prerequisite are not report evidence."""
    return (step_result.status == "BLOCKED" and
            step_result.actual == "未执行" and
            step_result.reason == "前序步骤失败，当前步骤不再具备有效前置条件")


def _command_sql(step):
    """Return the SQL sent by a simple psql command, when it is unambiguous."""
    argv = [str(item) for item in step.get("argv") or []]
    script = argv[-1] if len(argv) >= 3 and argv[0] in ("sh", "bash") else " ".join(argv)
    try:
        tokens = shlex.split(script)
    except ValueError:
        return ""
    statements = []
    for index, token in enumerate(tokens[:-1]):
        if token == "-c" and "psql" in " ".join(tokens[:index]):
            statements.append(tokens[index + 1])
    return statements[-1] if len(statements) == 1 else ""


def _step_command(step):
    kind = step.get("type", "sql")
    if kind in ("sql", "wait_sql", "background_sql"):
        sql = step["sql"].rstrip()
        if kind == "background_sql":
            sql = "%s; SELECT pg_sleep(%s); %s" % (
                sql.rstrip(";"), step["hold_seconds"], step["finish_sql"].rstrip(";"))
        if not sql.endswith(";"):
            sql += ";"
        prompt = "%s%s" % (step.get("database", "postgres"),
                            "=#" if step["user"] == "postgres" else "=>")
        return "%s %s" % (prompt, sql)
    if kind == "wait_background_sql":
        return "等待后台 psql 事务 key=%s 完成" % step["key"]
    if kind == "command":
        sql = step.get("display_sql") or _command_sql(step)
        if sql:
            sql = sql.rstrip()
            # display_sql may retain the absolute binary path.  These are shell
            # utilities, not SQL statements, so preserve a shell prompt.
            program = Path(sql.split(None, 1)[0]).name
            if program in ("fdd_mmr_join", "pg_ctl", "pg_basebackup"):
                return "$ %s" % sql
            return "%s=# %s%s" % (
                step.get("report_database", "postgres"), sql,
                "" if sql.endswith(";") else ";")
        return "$ " + " ".join(shlex.quote(str(value)) for value in step["argv"])
    if kind == "cluster_action":
        return "cluster action: %s" % step["action"]
    if kind == "node_action":
        return "node action: %s %s" % (step["action"], step["node"])
    if kind == "system_time_shift":
        return "system time shift: +%s seconds" % step["seconds"]
    return "<未知步骤类型>"


def render_case_report(case, result, cluster_name, env_id, plugins, nodes,
                       started, finished, postgres_binary="postgres",
                       environment_topology=None):
    duration = (result.duration_seconds if result.duration_seconds is not None
                else (finished - started).total_seconds())
    group_path = " / ".join(case["id"].split(".")[:-1])
    lines = [
        "用例: %s" % case["id"],
        "名称: %s" % case["name"],
        "来源: %s / %s" % (case["document"], case["section"]),
        "分组: %s" % group_path,
        "结论: %s" % result.status,
        "开始时间: %s" % started.strftime("%Y-%m-%d %H:%M:%S"),
        "结束时间: %s" % finished.strftime("%Y-%m-%d %H:%M:%S"),
        "耗时: %.3fs" % duration,
        "集群: %s env_id=%s plugins=%s" %
        (cluster_name, env_id, ",".join(sorted(plugins))),
        "Fixture: %s" % ",".join(_fixture_name(item) for item in case["fixtures"]),
    ]
    if case.get("known_issue"):
        lines.insert(5, "已知问题: %s（详见 问题记录.md）" % case["known_issue"])
    if case.get("report_setting_details", True):
        lines.extend(["", "PostgreSQL 生效配置:"])
    if case.get("report_setting_details", True) and result.setting_details:
        for index, item in enumerate(result.setting_details, 1):
            lines.extend([
                "%s. %s" % (index, item["purpose"]),
                "   执行节点: %s" % item["node"],
                "   执行用户: postgres",
                "   postgres=# %s;" % item["sql"],
            ])
            lines.extend("   %s" % line for line in item["output"].splitlines())
            if "previous" in item:
                lines.append("   修改前值: %s" % item["previous"])
            lines.extend([
                "   预期结果: 参数值%s" % item["requirement"],
                "   判定: %s" % ("SUCCESS" if item["matched"] else "BLOCKED"),
            ])
            if not item["matched"]:
                reason = item.get("error") or "实际值=%s，不满足%s" % (
                    item["actual"], item["requirement"])
                lines.append("   阻塞原因: %s" % reason)
            lines.extend(["   证据: execution.log", ""])
    elif case.get("report_setting_details", True):
        lines.append("  无")
    lines.extend([
        "",
    ])
    lines.extend(_conflict_configuration_lines(case))
    topology = case.get("test_topology") or environment_topology
    lines.extend(_topology_lines("测试运行拓扑:", topology))
    lines.extend([
        "",
        "前置条件:",
    ])
    lines.extend("  - %s" % item for item in case["prerequisites"])
    lines.extend(["", "验证步骤:"])
    # Setup details may be hidden in successful reports, but a hidden setup
    # failure is the primary evidence and must never be omitted.
    visible_steps = [item for item in result.steps
                     if (item.step.get("report", True) or item.status != "SUCCESS")
                     and not _was_not_executed(item)]
    visible_step_numbers = {id(item): index
                            for index, item in enumerate(visible_steps, 1)}
    for index, step_result in enumerate(visible_steps, 1):
        step = step_result.step
        lines.append("%s. %s" % (index, step["title"]))
        report_node = _report_node(step, step_result.node, topology)
        if report_node:
            lines.append("   执行节点: %s" % report_node)
        if step.get("type", "sql") in ("sql", "wait_sql", "background_sql"):
            lines.append("   执行用户: %s" % step["user"])
        lines.append("   %s" % _step_command(step))
        lines.extend("   %s" % line for line in step_result.output.splitlines())
        lines.extend([
            "   预期结果: %s" % step["expected"],
            "   判定: %s" % step_result.status,
        ])
        if step_result.reason:
            label = "阻塞原因" if step_result.status == "BLOCKED" else "失败原因"
            lines.append("   %s: %s" % (label, step_result.reason))
        lines.append("   证据: %s" % step_result.evidence)
        lines.append("")
    lines.append("清理动作: %s" % case["teardown"])
    lines.append("自动清理: %s" % ("FAILED" if result.cleanup_errors else "SUCCESS"))
    lines.extend("  - %s" % error for error in result.cleanup_errors)
    lines.extend(["", "服务端日志:"])
    if result.server_evidence:
        lines.extend("  - %s" % path for path in result.server_evidence)
    else:
        lines.append("  无")
    for error in result.server_log_errors:
        lines.append("  - 收集失败: %s" % error)
    lines.extend(["", "Core 文件:"])
    if result.core_files:
        lines.extend("  - %s" % path for path in result.core_files)
        lines.append("GDB 调试命令:")
        lines.extend("  - gdb %s %s" % (
            shlex.quote(str(postgres_binary)), shlex.quote(str(path)))
                     for path in result.core_files)
    else:
        lines.append("  无")
    lines.extend(["", "失败/阻塞说明:"])
    issues = [item for item in result.steps
              if item.status != "SUCCESS" and not _was_not_executed(item)]
    if result.blocker:
        lines.append("  - %s" % result.blocker)
    if result.error:
        lines.append("  - %s" % result.error)
    for item in result.steps:
        if item.status != "SUCCESS" and not _was_not_executed(item):
            lines.append("  - 第 %s 步: %s" %
                         (visible_step_numbers.get(id(item), "隐藏"),
                          item.reason or item.actual))
    lines.extend("  - 清理失败: %s" % error for error in result.cleanup_errors)
    if not result.blocker and not result.error and not issues and not result.cleanup_errors:
        lines.append("  无")
    return "\n".join(lines) + "\n"
