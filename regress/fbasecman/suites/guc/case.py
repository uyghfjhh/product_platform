"""Declarative GUC test case metadata."""

from suites.ha_commands.case import HaCommandCase


class GucCase(HaCommandCase):
    def __init__(self, name, summary, executor, notes, rw_split_method="sql_parse"):
        super(GucCase, self).__init__(
            name=name,
            summary=summary,
            source_sections=(
                "sources/fb_guc_cache.c",
                "sources/backend.c",
                "sources/frontend.c",
                "sources/parser/fb_sql_parse_adapter.c",
            ),
            executor=executor,
            notes=notes,
            route_mode=rw_split_method,
            report_groups=("mmr_group",),
        )
        self.suite_name = "guc"
        self.rw_split_method = rw_split_method

    @property
    def target(self):
        return "guc.%s" % self.name
