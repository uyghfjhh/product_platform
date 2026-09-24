"""Global-cache suite failures."""


class GlobalCacheFailure(RuntimeError):
    pass


class VerificationFailure(GlobalCacheFailure):
    def __init__(self, check):
        self.check = check
        super().__init__(
            "%s: expected=%s; actual=%s"
            % (check["title"], check["expected"], check["actual"])
        )
