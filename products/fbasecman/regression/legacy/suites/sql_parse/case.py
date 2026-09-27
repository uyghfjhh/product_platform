"""Declarative SQL_PARSE test case metadata."""

from suites.ha_commands.case import HaCommandCase


class SqlParseCase(HaCommandCase):
    def __init__(self, name, summary, executor, notes):
        super(SqlParseCase, self).__init__(
            name=name,
            summary=summary,
            source_sections=("sources/parser/fb_frontend.c",),
            executor=executor,
            notes=notes,
            route_mode="sql_parse",
            report_groups=("mmr_group",),
            suite_id="sql_parse",
        )
