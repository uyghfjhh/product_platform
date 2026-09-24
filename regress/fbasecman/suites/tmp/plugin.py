"""Temporary reproduction suite integration contract."""

from framework.suites.contracts import SuitePlugin
from .manifest import case_items
from .suite import run, show

PLUGIN = SuitePlugin(
    suite_id="tmp",
    title="临时/特定缺陷复现测试",
    description="专项问题排查与临时 Bug 复现用例（如 reload 关闭监控路由丢失复现）",
    case_loader=case_items,
    runner=run,
    shower=show,
)
