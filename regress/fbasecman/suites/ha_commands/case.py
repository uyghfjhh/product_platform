"""Declarative high-availability command test case metadata."""


class HaCommandCase(object):
    def __init__(self, name, summary, source_sections, executor, notes,
                 topology="mmr", route_mode=None, enabled=True,
                 report_groups=None, report_all_datasources=False):
        self.name = name
        self.summary = summary
        self.source_sections = tuple(source_sections)
        self.executor = executor
        self.notes = tuple(notes)
        self.topology = topology
        self.route_mode = route_mode
        self.enabled = enabled
        self.report_groups = tuple(report_groups or ("mmr_group",))
        self.report_all_datasources = bool(report_all_datasources)

    @property
    def target(self):
        return "ha_commands.%s" % self.name
