"""Declarative High Availability test case metadata (Chapter 4)."""

from framework.suites import CaseSpec


class HighAvailabilityCase(CaseSpec):
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
        super(HighAvailabilityCase, self).__init__(
            suite_id="high_availability", name=name, summary=summary,
            executor=executor or name, topology=topology, route_mode=route_mode,
            enabled=enabled,
        )
        self.core_id = core_id
        self.notes = tuple(notes)
        self.source_sections = tuple(source_sections) if source_sections else (
            "fbasecman转测前核心功能测试执行方案.md:%s" % core_id,
        )
        self.report_groups = tuple(report_groups)
