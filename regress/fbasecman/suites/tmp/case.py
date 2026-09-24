"""Declarative tmp test case metadata."""

from suites.ha_commands.case import HaCommandCase


class TmpCase(HaCommandCase):
    def __init__(self, name, summary, executor, notes, topology="mmr"):
        super(TmpCase, self).__init__(
            name=name,
            summary=summary,
            source_sections=(
                "sources/monitor/fb_monitor_reload.c:fb_monitor_reload_cluster_policy_compatible",
                "sources/router.c:fb_router_route_by_rule",
                "sources/frontend.c:od_router_route",
            ),
            executor=executor,
            notes=notes,
            topology=topology,
            report_groups=("mmr_group",),
        )
        self.suite_name = "tmp"

    @property
    def target(self):
        return "tmp.%s" % self.name
