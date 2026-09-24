"""High-availability suite integration contract."""

from framework.suites.contracts import SuitePlugin
from .manifest import case_items
from .suite import run, show

PLUGIN = SuitePlugin(
    suite_id="high_availability",
    title="高可用故障切换与 monitor 探测（方案第四章）",
    description="主备切换、MMR写中心切换、防抖探测与配置重载等核心高可用能力验证",
    case_loader=case_items,
    runner=run,
    shower=show,
)
