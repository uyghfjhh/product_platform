class RegressError(Exception):
    exit_code = 1


class ConfigError(RegressError):
    exit_code = 2


class SafetyError(RegressError):
    exit_code = 2


class OperationError(RegressError):
    exit_code = 1
