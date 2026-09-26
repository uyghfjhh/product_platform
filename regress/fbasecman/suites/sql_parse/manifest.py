"""SQL_PARSE heartbeat Bind regression cases."""

from .case import SqlParseCase


SQL_PARSE_CASES = (
    SqlParseCase(
        name="savepoint_recovery_after_local_25p02",
        summary="Extended 事务在本地 25P02 后仍可 ROLLBACK TO SAVEPOINT",
        executor="savepoint_recovery_after_local_25p02",
        notes=(
            "每条 SQL 明确发送 Extended P/B/D/E/S 并记录回包、SQLSTATE 与 ReadyForQuery。",
            "先验证除零后直接 ROLLBACK TO 的对照路径。",
            "再验证除零、额外 25P02、ROLLBACK TO、SELECT 9 的完整恢复路径。",
            "保存点可恢复时代理不应向后端发送内部简单查询 Q(ROLLBACK)。",
        ),
    ),
    SqlParseCase(
        name="heartbeat_bind_normal",
        summary="SQL_PARSE heartbeat 正常 Extended Bind 本地响应",
        executor="heartbeat_bind_normal",
        notes=(
            "测试配置明确启用 rw_split_method sql_parse。",
            "同时启用 pool_reserve_prepared_statement，heartbeat_request 为 select 1。",
            "验证无参数、文本结果格式的 Bind/Execute 返回本地缓存响应。",
        ),
    ),
    SqlParseCase(
        name="heartbeat_bind_invalid",
        summary="SQL_PARSE heartbeat 截断 Bind 被安全拒绝",
        executor="heartbeat_bind_invalid",
        notes=(
            "测试配置明确启用 rw_split_method sql_parse。",
            "发送缺少结果格式字段的截断 Bind，验证不会创建本地 heartbeat portal。",
            "报告记录 Parse/Bind 响应类型及异常 Bind 的拒绝结果。",
        ),
    ),
    SqlParseCase(
        name="heartbeat_bind_unsupported",
        summary="SQL_PARSE heartbeat 不支持格式回退 PostgreSQL",
        executor="heartbeat_bind_unsupported",
        notes=(
            "发送完整但要求二进制结果的 Bind，覆盖校验函数返回 0。",
            "验证本地 heartbeat bypass 放弃接管并回到后端 Bind 流程。",
        ),
    ),
)


def case_items():
    return list(SQL_PARSE_CASES)


def find_case(name):
    for case in SQL_PARSE_CASES:
        if case.name == name:
            return case
    raise KeyError("unknown sql_parse case: %s" % name)
