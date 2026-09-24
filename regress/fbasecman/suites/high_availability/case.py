"""Declarative High Availability test case metadata (Chapter 4)."""


class HighAvailabilityCase(object):
    """Metadata for high availability, failover, and monitor regression cases."""

    def __init__(
        self,
        name,
        core_id,
        summary,
        executor=None,
        notes=(),
        source_sections=(),
        report_groups=("qa_rep", "qa_mmr"),
        topology="mmr",
        route_mode="none",
        enabled=True,
    ):
        self.name = name
        self.core_id = core_id
        self.summary = summary
        self.executor = executor or name
        self.notes = tuple(notes)
        self.source_sections = tuple(source_sections) if source_sections else (
            "fbasecman转测前核心功能测试执行方案.md:%s" % core_id,
        )
        self.report_groups = tuple(report_groups)
        self.topology = topology
        self.route_mode = route_mode
        self.enabled = bool(enabled)
        self.suite_name = "high_availability"

    @property
    def target(self):
        return "high_availability.%s" % self.name
