"""Declarative outstanding queue regression case metadata."""

from framework.suites import CaseSpec


class OutstandingCase(CaseSpec):
    """Metadata for outstanding queue and backend prepared statements consistency cases."""

    def __init__(self, name, summary, executor=None, mode=None, notes=(), enabled=True,
                 topology="mmr", route_mode="none + pool_reserve_prepared_statement",
                 report_groups=("single_group",), report_all_datasources=False,
                 source_sections=("sources/parser/fb_frontend.c", "sources/relay.h")):
        super(OutstandingCase, self).__init__(
            suite_id="outstanding", name=name, summary=summary,
            executor=executor or name, topology=topology, route_mode=route_mode,
            enabled=enabled,
        )
        self.mode = mode or name
        self.notes = tuple(notes)
        self.report_groups = tuple(report_groups)
        self.report_all_datasources = bool(report_all_datasources)
        self.source_sections = tuple(source_sections)
