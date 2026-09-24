"""Global-cache case declaration."""


class GlobalCacheCase:
    def __init__(
        self,
        name,
        summary,
        batch,
        priority,
        driver,
        topology,
        rw_split_method,
        pool_mode,
        enabled=False,
        notes=None,
        fbasecman=None,
        jdbc=None,
        evidence=None,
        assertions=None,
        sql=None,
        reload=None,
        ha=None,
        allowed_fbasecman_log_patterns=None,
        report_level=None,
        issue_id=None,
        test_contents=None,
        step_rules=None,
    ):
        self.name = name
        self.summary = summary
        self.batch = batch
        self.priority = priority
        self.driver = driver
        self.topology = topology
        self.rw_split_method = rw_split_method
        self.pool_mode = pool_mode
        self.enabled = enabled
        self.notes = notes or []
        self.fbasecman = fbasecman or {}
        self.jdbc = jdbc or {}
        self.evidence = evidence or {}
        self.assertions = assertions or {}
        self.sql = sql or {}
        self.reload = reload
        self.ha = ha
        self.allowed_fbasecman_log_patterns = allowed_fbasecman_log_patterns or []
        self.report_level = report_level or "standard"
        self.issue_id = issue_id
        self.test_contents = tuple(test_contents or (summary,))
        self.step_rules = tuple(step_rules or ())

    @property
    def target(self):
        return "global_cache.%s" % self.name
