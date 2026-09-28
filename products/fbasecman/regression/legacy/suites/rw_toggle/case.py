"""Declarative read/write routing case metadata."""

from platform_regress.suites import CaseSpec


class RwToggleCase(CaseSpec):
    def __init__(self, name, summary, topology, route_mode, driver, scenario,
                 notes=()):
        super(RwToggleCase, self).__init__(
            suite_id="rw_toggle", name=name, summary=summary, executor=driver,
            topology=topology, route_mode=route_mode,
        )
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
