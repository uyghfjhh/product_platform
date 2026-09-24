"""Product-neutral report document model."""


_TRANSPORT_ONLY_EXPECTATIONS = frozenset((
    "psql 命令完成",
    "psql 脚本执行完成",
    "JDBC 程序退出成功",
    "阶段 JDBC driver 退出成功",
    "JDBC 路由时序程序退出成功",
))


def is_transport_only_success(expected, result):
    """Whether a successful step only proves its transport command returned.

    The command and its real output are already shown under ``实际执行``.  Repeating
    a raw machine-readable copy as an expected/actual assertion makes reports
    harder to read without proving a product behavior.
    """
    return result == "PASS" and str(expected or "").strip() in _TRANSPORT_ONLY_EXPECTATIONS


class ReportCheck(object):
    def __init__(self, title, expected, actual, result):
        self.title = title
        self.expected = expected
        self.actual = actual
        self.result = result


class ReportStep(object):
    def __init__(self, title, details=None, expected=None, actual=None, result=None, checks=None,
                 execution=None, intermediate=None, evidence=None, key_expected=None,
                 coverage=None, coverage_check=None):
        self.title = title
        self.details = details or []
        self.expected = expected
        self.actual = actual
        self.result = result
        self.checks = checks or []
        self.execution = execution or []
        self.intermediate = intermediate or []
        self.evidence = evidence or []
        self.key_expected = key_expected
        self.coverage = coverage
        self.coverage_check = coverage_check


class ReportDocument(object):
    def __init__(self, target, status, started_at, finished_at, purpose,
                 config_lines=None, coverage_items=None, coverage_mapping=None,
                 coverage_title="文档测试内容",
                 overview_steps=None, steps=None,
                 pass_reason=None, failure_reason=None):
        self.target = target
        self.status = status
        self.started_at = started_at
        self.finished_at = finished_at
        self.purpose = purpose
        self.config_lines = config_lines or []
        self.coverage_items = coverage_items or []
        self.coverage_mapping = coverage_mapping or []
        self.coverage_title = coverage_title
        self.overview_steps = overview_steps or []
        self.steps = steps or []
        self.pass_reason = pass_reason
        self.failure_reason = failure_reason
