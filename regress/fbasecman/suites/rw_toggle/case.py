"""Declarative read/write routing case metadata."""


class RwToggleCase(object):
    def __init__(self, name, summary, topology, route_mode, driver, scenario,
                 notes=()):
        self.name = name
        self.summary = summary
        self.topology = topology
        self.route_mode = route_mode
        self.driver = driver
        self.scenario = scenario
        self.notes = tuple(notes)
        self.source_sections = (
            "tests/rw_toggle_cases (legacy behavior reference)",
            "sources/router.c",
            "sources/frontend.c",
        )
        self.report_groups = ("mmr_group" if topology == "mmr" else "rep_group",)
        self.report_all_datasources = False
        self.suite_name = "rw_toggle"

    @property
    def target(self):
        return "rw_toggle.%s" % self.name
