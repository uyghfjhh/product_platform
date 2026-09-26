class ExecutionResult(object):
    def __init__(self, returncode, output="", display_output=None, command=None,
                 columns=None, rows=None, sqlstate=None, error_message=None):
        self.returncode = returncode
        self.output = output or ""
        self.display_output = display_output if display_output is not None else self.output
        self.command = command or []
        self.columns = columns or []
        self.rows = rows or []
        self.sqlstate = sqlstate
        self.error_message = error_message


class StepResult(object):
    def __init__(self, step, status, actual, output, reason="", node=None,
                 evidence="execution.log"):
        self.step = step
        self.status = status
        self.actual = actual
        self.output = output
        self.reason = reason
        self.node = node
        self.evidence = evidence


class CaseResult(object):
    def __init__(self, status, steps, output_dir, blocker="", evidence="execution.log",
                 server_evidence=None, server_log_errors=None, core_files=None,
                 cleanup_errors=None, error="", setting_details=None,
                 duration_seconds=None):
        self.status = status
        self.steps = steps
        self.output_dir = output_dir
        self.blocker = blocker
        self.evidence = evidence
        self.server_evidence = server_evidence or []
        self.server_log_errors = server_log_errors or []
        self.core_files = core_files or []
        self.cleanup_errors = cleanup_errors or []
        self.error = error
        self.setting_details = setting_details or []
        self.duration_seconds = duration_seconds
