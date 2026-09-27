import time

from framework.assertions import assertion_names, evaluate, format_psql_output
from framework.errors import ConfigError
from framework.models import ExecutionResult, StepResult


STEP_TYPES = {"sql", "command", "wait_sql", "background_sql", "wait_background_sql",
              "cluster_action", "node_action", "system_time_shift"}


def sql_step(title, user, sql, expected, assertion, node="primary",
             database="postgres", command_timeout=None, client_encoding=None,
             password=None, continue_on_failure=False):
    return {
        "type": "sql", "title": title, "node": node, "user": user,
        "database": database, "sql": sql, "expected": expected,
        "assertion": assertion, "command_timeout": command_timeout,
        "client_encoding": client_encoding, "password": password,
        "continue_on_failure": continue_on_failure,
    }


def command_step(title, argv, expected, assertion, node="primary",
                 cwd=None, env=None, input_text=None, timeout=None,
                 continue_on_failure=False, report=True, display_sql=None,
                 report_node=None):
    return {
        "type": "command", "title": title, "node": node,
        "argv": argv, "cwd": cwd, "env": env, "input": input_text,
        "expected": expected, "assertion": assertion, "timeout": timeout,
        "continue_on_failure": continue_on_failure, "report": report,
        "display_sql": display_sql, "report_node": report_node,
    }


def wait_sql_step(title, user, sql, expected, assertion, node="primary",
                  database="postgres", timeout=30, interval=1,
                  continue_on_failure=False):
    step = sql_step(title, user, sql, expected, assertion, node, database,
                    continue_on_failure=continue_on_failure)
    step.update({"type": "wait_sql", "timeout": timeout, "interval": interval,
                 "command_timeout": timeout})
    return step


def background_sql_step(title, user, sql, finish_sql, expected, assertion, key,
                        node="primary", database="postgres", hold_seconds=10,
                        settle_seconds=1):
    return {
        "type": "background_sql", "title": title, "node": node, "user": user,
        "database": database, "sql": sql, "finish_sql": finish_sql, "key": key,
        "hold_seconds": hold_seconds, "settle_seconds": settle_seconds,
        "expected": expected, "assertion": assertion,
    }


def wait_background_sql_step(title, key, expected, assertion, timeout=30):
    return {
        "type": "wait_background_sql", "title": title, "key": key,
        "timeout": timeout, "expected": expected, "assertion": assertion,
    }


def validate_step(step):
    kind = step.get("type", "sql")
    if kind not in STEP_TYPES:
        raise ConfigError("步骤 %s 使用未知类型: %s" %
                          (step.get("title", "<unknown>"), kind))
    common = {"title", "expected", "assertion"}
    required = set(common)
    if kind in ("sql", "wait_sql", "background_sql"):
        required.update({"user", "sql"})
    elif kind == "wait_background_sql":
        required.add("key")
    elif kind == "command":
        required.add("argv")
    elif kind == "cluster_action":
        required.add("action")
    elif kind == "node_action":
        required.update({"action", "node"})
    elif kind == "system_time_shift":
        required.add("seconds")
    missing = required - set(step)
    if missing:
        raise ConfigError("步骤 %s 缺少字段: %s" %
                          (step.get("title", "<unknown>"), ", ".join(sorted(missing))))
    assertion_spec = step.get("assertion") or {}
    if assertion_spec.get("type") not in assertion_names():
        raise ConfigError("步骤 %s 使用未知断言: %s" %
                          (step.get("title", "<unknown>"), assertion_spec.get("type")))
    if "continue_on_failure" in step and not isinstance(step["continue_on_failure"], bool):
        raise ConfigError("步骤 %s 的 continue_on_failure 必须为布尔值" %
                          step.get("title", "<unknown>"))
    if kind == "wait_sql" and float(step.get("timeout", 30)) <= 0:
        raise ConfigError("wait_sql timeout 必须大于 0")
    if kind == "background_sql":
        if not step.get("key") or not step.get("finish_sql"):
            raise ConfigError("background_sql 必须声明 key 和 finish_sql")
        if float(step.get("hold_seconds", 10)) <= 0:
            raise ConfigError("background_sql hold_seconds 必须大于 0")


class StepExecutor(object):
    def __init__(self, context):
        self.context = context

    def execute(self, step):
        if hasattr(self.context, "expand"):
            step = self.context.expand(step)
        kind = step.get("type", "sql")
        if kind == "sql":
            return self._sql(step)
        if kind == "command":
            return self._command(step)
        if kind == "wait_sql":
            return self._wait_sql(step)
        if kind == "background_sql":
            return self._background_sql(step)
        if kind == "wait_background_sql":
            return self._wait_background_sql(step)
        if kind == "cluster_action":
            return self._cluster_action(step)
        if kind == "node_action":
            return self._node_action(step)
        if kind == "system_time_shift":
            return self._system_time_shift(step)
        raise ConfigError("未知步骤类型: %s" % kind)

    def _sql_execution(self, step):
        node_name = self.context.resolve_node(step.get("node", "primary"))
        structured = step["assertion"]["type"] in {
            "rows_equal", "rows_with_output_contains", "query_equals", "scalar_equals",
        }
        execution = self.context.postgres.execute(
            node_name, step["user"], step.get("database", "postgres"),
            step["sql"], structured=structured,
            timeout=step.get("command_timeout"),
            client_encoding=step.get("client_encoding"),
            password=step.get("password"),
            connection=(step.get("connection") or
                        self.context.case.get("connection")))
        return node_name, execution

    def _sql(self, step):
        node_name, execution = self._sql_execution(step)
        passed, actual, reason = evaluate(step["assertion"], execution)
        return StepResult(
            step, "SUCCESS" if passed else "FAILED", actual,
            format_psql_output(execution.display_output), reason, node_name)

    def _command(self, step):
        node_name = self.context.resolve_node(step.get("node", "primary"))
        # Isolated MMR fixtures reserve future server ports while earlier
        # nodes create libpq connections.  Release only the node this command
        # is about to start.
        from framework.fixtures import release_isolated_mmr_port_for_command
        release_isolated_mmr_port_for_command(self.context, step["argv"])
        process = self.context.transport.run(
            node_name, step["argv"], check=False,
            input_text=step.get("input"), cwd=step.get("cwd"), env=step.get("env"),
            timeout=step.get("timeout"))
        execution = ExecutionResult(process.returncode, output=process.stdout,
                                    command=step["argv"])
        passed, actual, reason = evaluate(step["assertion"], execution)
        return StepResult(
            step, "SUCCESS" if passed else "FAILED", actual,
            format_psql_output(execution.output), reason, node_name)

    def _wait_sql(self, step):
        deadline = time.time() + float(step.get("timeout", 30))
        last = None
        while time.time() < deadline:
            node_name, execution = self._sql_execution(step)
            passed, actual, reason = evaluate(step["assertion"], execution)
            last = (node_name, execution, actual, reason)
            if passed:
                return StepResult(step, "SUCCESS", actual,
                                  format_psql_output(execution.display_output), "", node_name)
            time.sleep(float(step.get("interval", 1)))
        if last is None:
            return StepResult(step, "FAILED", "未执行", "<无输出>", "等待超时")
        node_name, execution, actual, reason = last
        reason = "等待 %ss 超时；%s" % (step.get("timeout", 30), reason)
        return StepResult(step, "FAILED", actual,
                          format_psql_output(execution.display_output), reason, node_name)

    def _background_sql(self, step):
        node_name = self.context.resolve_node(step.get("node", "primary"))
        node = self.context.manager.node(node_name)
        key = step["key"]
        running = self.context.values.setdefault("background_sql", {})
        if key in running:
            raise ConfigError("后台 SQL key 已存在: %s" % key)
        script = "%s; SELECT pg_sleep(%s); %s" % (
            step["sql"].rstrip(";"), step["hold_seconds"],
            step["finish_sql"].rstrip(";"))
        command = [
            self.context.manager.binary("psql"), "-X", "-v", "ON_ERROR_STOP=1",
            "-v", "VERBOSITY=verbose", "-P", "pager=off",
            "-h", node["host"], "-p", str(node["port"]), "-U", step["user"],
            "-d", step.get("database", "postgres"), "-c", script,
        ]
        process = self.context.command_runner.start(command)
        running[key] = {"process": process, "command": command, "node": node_name}

        def cleanup():
            entry = running.pop(key, None)
            if entry and entry["process"].poll() is None:
                entry["process"].terminate()
                self.context.command_runner.finish(entry["command"], entry["process"], 5)

        self.context.add_cleanup("终止后台 SQL %s" % key, cleanup, priority=300)
        time.sleep(float(step.get("settle_seconds", 1)))
        if process.poll() is not None:
            completed = self.context.command_runner.finish(command, process, 1)
            execution = ExecutionResult(completed.returncode, output=completed.stdout,
                                        command=command)
            passed, actual, reason = evaluate(step["assertion"], execution)
            return StepResult(step, "SUCCESS" if passed else "FAILED", actual,
                              format_psql_output(execution.output), reason, node_name)
        execution = ExecutionResult(0, output="后台 psql 已启动，pid=%s；事务保持中" % process.pid,
                                    command=command)
        passed, actual, reason = evaluate(step["assertion"], execution)
        return StepResult(step, "SUCCESS" if passed else "FAILED", actual,
                          format_psql_output(execution.output), reason, node_name)

    def _wait_background_sql(self, step):
        running = self.context.values.setdefault("background_sql", {})
        entry = running.pop(step["key"], None)
        if not entry:
            raise ConfigError("未找到后台 SQL key: %s" % step["key"])
        completed = self.context.command_runner.finish(
            entry["command"], entry["process"], step.get("timeout", 30))
        execution = ExecutionResult(completed.returncode, output=completed.stdout,
                                    command=entry["command"])
        passed, actual, reason = evaluate(step["assertion"], execution)
        return StepResult(step, "SUCCESS" if passed else "FAILED", actual,
                          format_psql_output(execution.output), reason, entry["node"])

    def _cluster_action(self, step):
        action = step["action"]
        callback = getattr(self.context.manager, action, None)
        if action not in ("reload", "restart", "start", "stop") or not callback:
            raise ConfigError("不支持的 cluster action: %s" % action)
        callback(quiet=True)
        execution = ExecutionResult(0, output="%s complete" % action)
        passed, actual, reason = evaluate(step["assertion"], execution)
        return StepResult(step, "SUCCESS" if passed else "FAILED", actual,
                          execution.output, reason)

    def _node_action(self, step):
        action = step["action"]
        if action not in ("start", "stop", "stop_immediate", "restart"):
            raise ConfigError("不支持的 node action: %s" % action)
        node_name = self.context.resolve_node(step["node"])
        self.context.manager._pg_ctl(node_name, action)
        execution = ExecutionResult(0, output="%s %s complete" % (action, node_name))
        passed, actual, reason = evaluate(step["assertion"], execution)
        return StepResult(step, "SUCCESS" if passed else "FAILED", actual,
                          execution.output, reason, node_name)

    def _system_time_shift(self, step):
        state = self.context.values.get("system_clock")
        if not state:
            raise ConfigError("system_time_shift 需要 system_clock fixture")
        seconds = int(step["seconds"])
        if seconds <= 0:
            raise ConfigError("system_time_shift seconds 必须大于 0")
        target = int(state["epoch"]) + seconds
        self.context.command_runner.run(
            ["sudo", "-n", "timedatectl", "set-ntp", "false"])
        process = self.context.command_runner.run(
            ["sudo", "-n", "date", "-s", "@%s" % target], check=False)
        if process.returncode == 0:
            state["changed"] = True
        execution = ExecutionResult(
            process.returncode,
            output="原始 epoch=%s，目标 epoch=%s\n%s" % (
                state["epoch"], target, process.stdout or ""),
            command=["sudo", "-n", "date", "-s", "@%s" % target])
        passed, actual, reason = evaluate(step["assertion"], execution)
        return StepResult(step, "SUCCESS" if passed else "FAILED", actual,
                          format_psql_output(execution.output), reason)
