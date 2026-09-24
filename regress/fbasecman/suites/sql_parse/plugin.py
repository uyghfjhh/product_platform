"""SQL parse suite integration contract."""

from framework.suites.contracts import SuitePlugin
from .manifest import case_items
from .suite import run, show

PLUGIN = SuitePlugin(
    suite_id="sql_parse",
    title="SQL_PARSE 扩展协议测试",
    description="基于 SQL 语法解析的读写路由决策及扩展查询协议兼容性测试",
    case_loader=case_items,
    runner=run,
    shower=show,
)
