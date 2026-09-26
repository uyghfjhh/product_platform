"""Declarative common regression test case metadata."""

from framework.suites import CaseSpec


class CommonCase(CaseSpec):
    def __init__(self, name, summary, source_sections, executor, notes,
                 topology="mmr", route_mode=None, enabled=True,
                 report_groups=None, report_all_datasources=False):
        super(CommonCase, self).__init__(
            suite_id="common", name=name, summary=summary, executor=executor,
            topology=topology, route_mode=route_mode, enabled=enabled,
        )
        self.source_sections = tuple(source_sections)
        self.notes = tuple(notes)
        self.report_groups = tuple(report_groups or ("mmr_group",))
        self.report_all_datasources = bool(report_all_datasources)
