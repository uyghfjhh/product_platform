"""Declarative transfer-test case metadata."""

from platform_regress.suites import CaseSpec


class HandoverCase(CaseSpec):
    def __init__(self, name, summary, source_sections, executor, topology=None,
                 route_mode=None, long_time=False, enabled=True, notes=None, issue_id=None,
                 workload=None, step_mapping=None, step_rules=None):
        super(HandoverCase, self).__init__(
            suite_id="handover", name=name, summary=summary, executor=executor,
            topology=topology, route_mode=route_mode, enabled=enabled,
        )
        self.source_sections = tuple(source_sections)
        self.long_time = long_time
        self.notes = tuple(notes or ())
        self.step_mapping = tuple(step_mapping or ())
        # A rule is (regular_expression, document_content_id, business_check).
        # It is resolved against the report steps actually emitted by an
        # executor, so adding a retry or an intermediate observation cannot
        # silently shift a static step-number mapping.
        self.step_rules = tuple(step_rules or ())
        self.issue_id = issue_id
        self.workload = workload
