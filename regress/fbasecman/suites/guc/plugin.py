"""GUC suite integration contract."""

from framework.suites.contracts import SuitePlugin
from .manifest import case_items
from .suite import run, show

PLUGIN = SuitePlugin(
    suite_id="guc",
    title="GUC 规范化、多值解析与连接复用测试",
    description="search_path 多值 GUC 规范化、AST 表达式重放、Hint/SQL_PARSE 模式与连接复用防多重转义",
    case_loader=case_items,
    runner=run,
    shower=show,
)
