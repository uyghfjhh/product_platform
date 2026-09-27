"""Manifest of outstanding queue and backend PS consistency cases."""

from .case import OutstandingCase

OUTSTANDING_CASES = (
    OutstandingCase(
        name="lru_close_multiple_restore_consistency",
        summary="LRU 淘汰并发多连接 Close 与一致性恢复验证",
        mode="lru_close_multiple_restore",
        notes=(
            "验证并发事务池下，多个客户端释放/Close 后端 PS 时与 outstanding 请求队列的一致性。",
            "证明缓存淘汰和并发连接不会导致后端 prepared statement 孤立残留或 42P05 冲突。",
        ),
    ),
    OutstandingCase(
        name="lru_close_skipped_restore_consistency",
        summary="LRU 替换目标 Parse 失败时跳过 Close 并恢复旧条目一致性",
        mode="lru_close_skipped_restore",
        notes=(
            "验证当新语句 Parse 失败时，跳过对已被淘汰候选的 Close 清理或正确恢复原条目所有权。",
            "证明旧条目仍可被后续查询正常引用并执行，不产生 42P05 或 26000 异常。",
        ),
    ),
    OutstandingCase(
        name="lru_close_success_consistency",
        summary="LRU 淘汰成功触发 Close(S) 的缓存与 outstanding 一致性",
        mode="lru_close_success",
        notes=(
            "验证后端连接达到 prepared statement 上限时，触发 LRU 淘汰并生成 Close 报文正常关闭旧语句。",
            "验证新语句成功准备并执行，后端缓存容量与条目状态符合 LRU 限制。",
        ),
    ),
    OutstandingCase(
        name="lru_confirmed_multiple_restore_order",
        summary="复杂流水线下多条 LRU Close 与 Restore 的顺序保序验证",
        mode="lru_confirmed_multiple_restore_order",
        notes=(
            "验证在同一个 Sync 内交替出现 Execute 与多次 Parse 引起的多次 LRU 淘汰与 Close。",
            "验证 outstanding 队列在发生执行错误时能够按照严格的反向/保序顺序恢复后端状态。",
        ),
    ),
    OutstandingCase(
        name="long_statement_name_cleanup",
        summary="超长 prepared statement 名称失败后的 outstanding 清理",
        mode="long_statement_name_cleanup",
        notes=(
            "验证客户端使用达到或接近边界长度的 prepared statement 名称且 Parse 失败时，内部名称缓存能够正确回收。",
            "验证超长名称不会导致 outstanding 状态截断、越界或内存泄漏。",
        ),
    ),
    OutstandingCase(
        name="fragmented_close_packet",
        summary="分片传输 Close 报文场景下的协议解析与缓存清理",
        mode="fragmented_close_packet",
        notes=(
            "验证客户端将 Close(S) 报文逐字节分片发送时，fbasecman 能完整重组报文并不产生 outstanding 阻塞。",
            "验证 Close 执行完毕后后端与全局缓存状态同步完成。",
        ),
    ),
    OutstandingCase(
        name="fragmented_execute_packet",
        summary="分片传输 Execute 报文场景下的协议解析与状态一致性",
        mode="fragmented_execute_packet",
        notes=(
            "验证客户端将 Execute 报文逐字节分片发送时，中间状态机正确维持 outstanding 引用。",
            "验证分片执行完成后返回正确结果，且后续执行保持正常。",
        ),
    ),
    OutstandingCase(
        name="execute_payload_validation",
        summary="畸形 Execute 报文格式校验与 08P01 协议拒绝",
        mode="execute_payload_validation",
        notes=(
            "验证缺失 max_rows 或携带多余尾部数据的畸形 Execute 报文被安全拦截并返回 08P01 协议错误。",
            "验证协议错误后连接处于合法状态，正常 Execute 与事务回滚恢复均能保持缓存一致。",
        ),
    ),
    OutstandingCase(
        name="parse_failure_single_consistency",
        summary="单连接 Parse 失败后的 outstanding 一致性",
        mode="parse_failure_single",
        notes=(
            "验证单连接发送错误 SQL 导致 Parse 失败时，outstanding 队列能够正常清理未完成状态。",
            "验证恢复正常 Parse 与 Execute 后，后端 prepared statement 与缓存状态恢复一致。",
        ),
    ),
    OutstandingCase(
        name="parse_failure_shared_sync_consistency",
        summary="多 Parse 批量同步中单条 Parse 失败的 outstanding 一致性",
        mode="parse_failure_shared_sync",
        notes=(
            "验证同一 pipeline 批次中包含多个 Parse 请求且中间发生 Parse 失败时，前置成功的语句保留而后置语句安全取消。",
            "验证后续恢复流程能够正确补全 Parse 并正常执行，缓存引用计数和后端 prepared statement 保持一致。",
        ),
    ),
    OutstandingCase(
        name="execute_failure_shared_sync_consistency",
        summary="批量执行中 Execute 阶段失败后的 outstanding 一致性",
        mode="execute_failure_shared_sync",
        notes=(
            "验证 Extended 协议管道批次中某一语句执行失败时，outstanding 请求状态正确终止。",
            "验证事务失败回滚及恢复执行后，后端 prepared statement 不会发生状态错乱或残留冲突。",
        ),
    ),
)


def case_items():
    return list(OUTSTANDING_CASES)


def find_case(name):
    for case in OUTSTANDING_CASES:
        if case.name == name:
            return case
    raise KeyError("unknown outstanding case: %s" % name)
