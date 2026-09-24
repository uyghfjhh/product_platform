"""Common regression suite integration contract."""

from framework.suites.contracts import SuitePlugin
from .manifest import case_items
from .suite import run, show

PLUGIN = SuitePlugin(
    suite_id="common",
    title="通用能力、Locale热切换与错误轮换测试",
    description="控制台命令中英文国际化动态热切换、错误统计轮换与并发写入同步安全性",
    case_loader=case_items,
    runner=run,
    shower=show,
)
