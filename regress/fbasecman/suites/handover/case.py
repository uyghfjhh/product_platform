"""Declarative transfer-test case metadata."""


class HandoverCase(object):
    def __init__(self, name, summary, source_sections, executor, topology=None,
                 route_mode=None, long_time=False, enabled=True, notes=None, issue_id=None,
                 workload=None, step_mapping=None, step_rules=None):
        self.name = name
        self.summary = summary
        self.source_sections = tuple(source_sections)
        self.executor = executor
        self.topology = topology
        self.route_mode = route_mode
        self.long_time = long_time
        self.enabled = enabled
        self.notes = tuple(notes or ())
        self.step_mapping = tuple(step_mapping or ())
        # A rule is (regular_expression, document_content_id, business_check).
        # It is resolved against the report steps actually emitted by an
        # executor, so adding a retry or an intermediate observation cannot
        # silently shift a static step-number mapping.
        self.step_rules = tuple(step_rules or ())
        self.issue_id = issue_id
        self.workload = workload

    @property
    def target(self):
        return "handover.%s" % self.name
