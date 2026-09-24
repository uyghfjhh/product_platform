"""Global prepared-statement cache suite integration contract."""

from framework.suites.contracts import SuitePlugin
from .manifest import formal_case_items
from .suite import run, show

PLUGIN = SuitePlugin(
    suite_id="global_cache",
    title="全局 PreparedStatement 缓存回归测试",
    description="全局 PS 缓存生命周期、LRU 淘汰、跨客户端复用与 JDBC 扩展协议支持",
    case_loader=formal_case_items,
    runner=run,
    shower=show,
)
