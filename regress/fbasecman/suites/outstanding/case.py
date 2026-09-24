"""Declarative outstanding queue regression case metadata."""


class OutstandingCase(object):
    """Metadata for outstanding queue and backend prepared statements consistency cases."""

    def __init__(self, name, summary, executor=None, mode=None, notes=(), enabled=True,
                 topology="mmr", route_mode="none + pool_reserve_prepared_statement",
                 report_groups=("single_group",), report_all_datasources=False,
                 source_sections=("sources/parser/fb_frontend.c", "sources/relay.h")):
        self.name = name
        self.summary = summary
        self.executor = executor or name
        self.mode = mode or name
        self.notes = tuple(notes)
        self.enabled = bool(enabled)
        self.topology = topology
        self.route_mode = route_mode
        self.report_groups = tuple(report_groups)
        self.report_all_datasources = bool(report_all_datasources)
        self.source_sections = tuple(source_sections)
        self.suite_name = "outstanding"

    @property
    def target(self):
        return "outstanding.%s" % self.name
