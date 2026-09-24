"""Read/write toggle suite integration contract."""

from framework.suites.contracts import SuitePlugin
from .manifest import case_items
from .suite import run, show

PLUGIN = SuitePlugin(
    suite_id="rw_toggle",
    title="读写切换/读写分离回归测试",
    description="单双主拓扑、读写分离策略、Hint 标签模式与端口路由机制切换",
    case_loader=case_items,
    runner=run,
    shower=show,
)
