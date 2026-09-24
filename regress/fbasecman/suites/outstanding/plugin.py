"""Outstanding queue suite integration contract."""

from framework.suites.contracts import SuitePlugin
from .manifest import case_items
from .suite import run, show

PLUGIN = SuitePlugin(
    suite_id="outstanding",
    title="outstanding 队列与后端 PS 缓存一致性",
    description="outstanding 队列高并发执行、后端 PreparedStatement 缓存生命周期及一致性",
    case_loader=case_items,
    runner=run,
    shower=show,
)
