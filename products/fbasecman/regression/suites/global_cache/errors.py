"""Global-cache suite failures."""

from platform_regress.sdk import CaseFailure


class GlobalCacheFailure(CaseFailure):
    pass


class VerificationFailure(GlobalCacheFailure):
    def __init__(self, check):
        self.check = check
        super().__init__(
            "%s: expected=%s; actual=%s"
            % (check["title"], check["expected"], check["actual"])
        )
