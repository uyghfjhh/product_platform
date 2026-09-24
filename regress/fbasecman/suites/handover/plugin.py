"""Handover suite integration contract."""

from framework.suites.contracts import SuitePlugin
from .manifest import case_items
from .suite import run, show


def selectable_cases():
    return [case for case in case_items(include_long_time=True) if case.enabled]


PLUGIN = SuitePlugin(
    suite_id="handover",
    title="fbasecman 转测文档第 4 至 12 章",
    description="转测文档核心业务能力（MMR/REP 路由模式、客户端连接与管理接口验证）",
    case_loader=selectable_cases,
    runner=run,
    shower=show,
)
