"""Business explanations for GUC checks; archived observations remain authoritative."""
GROUPS = {
    'extended_boundary': ('预处理 SET：解析不生效、执行才生效', '检查只解析或绑定 SET 时参数不提前改变，真正 Execute 后才生效；不同语句和 Portal 的候选值互不覆盖，关闭未执行语句后候选被清理。'),
    'transaction_sync': ('事务内 GUC：提交保留、回滚恢复与后端同步', '检查 SET、RESET、RESET ALL 在提交后保留、回滚后恢复；SET LOCAL 仅在当前事务内生效，失败事务和多语句请求不能提交错误的参数状态。'),
    'savepoint_report': ('保存点恢复与客户端时区通知', '检查回滚到保存点后参数恢复，嵌套、同名保存点及 RELEASE 行为正确；TimeZone 的实际查询值和客户端收到的 ParameterStatus 通知一致。'),
    'backend_redeploy': ('GUC 同步与数据库连接复用防污染', '检查代理将数据库连接交给不同客户端时，参数各自保持且互不污染；未提交断连、读写后端切换和 DISCARD 清理后参数正确。重部署指代理把当前客户端的参数重新应用到分配的数据库连接。'),
}

SCENARIO_PURPOSES = {
    'extended_parse_no_execute': '只发送 Parse，不发送 Execute；会话参数和正式缓存必须保持执行前的值。',
    'extended_bind_describe_no_execute': '发送 Bind 和 Describe 但不执行；绑定和描述成功不能使 SET 提前生效。',
    'extended_execute_apply': '实际 Execute 后参数才改变；随后换到另一物理后端，读取到同一客户端已设置的值。',
    'extended_statement_isolation': '不同命名语句保存各自的 SET 候选，执行指定语句时只应用该语句的值。',
    'extended_portal_isolation': '不同 Portal 绑定不同 SET 候选，执行某个 Portal 时不能采用另一个 Portal 的值。',
    'extended_candidate_cleanup': '关闭未执行的语句或 Portal 后，旧候选不能在后续请求中被错误应用；混合请求与已冻结基线一致。',
    'tx_set_commit': '事务内 SET 后 COMMIT；会话保持提交值，换物理后端后仍读到该提交值。',
    'tx_set_rollback': '事务内 SET 后 ROLLBACK；恢复事务前值，换物理后端后仍读到恢复值。',
    'tx_reset_commit_rollback': 'RESET work_mem 在事务内读回直连默认值；回滚恢复原会话值，提交保留默认值，分别在换后端后再次核对。',
    'tx_reset_all_commit_rollback': 'RESET ALL 对 work_mem、statement_timeout、TimeZone 的提交与回滚分别核对，默认值来自本次直连采集。',
    'tx_error_abort': '触发真实 SQL 错误并核对 SQLSTATE；失败事务结束后保留事务前值，再换后端检查错误候选未被同步。',
    'tx_batch_segments': '单个简单查询请求包含多个事务段；先核对每段提交或回滚产生的最终值，再换后端核对同一客户端值保持正确。',
    'set_local_scope': 'SET LOCAL 仅在当前事务生效；提交或回滚后恢复对应会话值，再换后端核对临时值未残留。',
    'savepoint_rollback': '保存点后修改或重置参数，回滚到保存点应恢复保存点前值；事务提交及换后端后继续核对。',
    'savepoint_nested_release': '嵌套、同名和带引号保存点按各自层次恢复；RELEASE 不误丢参数，外层回滚仍恢复正确值。',
    'report_parameter_status': '事务提交、回滚及保存点恢复后，TimeZone 查询值与客户端收到的 ParameterStatus 通知一致，换后端后仍一致。',
    'compatibility_scope': '关闭同步或预处理语句保留时，保持已有透传行为；仍区分只 Parse 与真正 Execute，不声称此分支验证了缓存同步。',
    'session_backend_redeploy': '先读取默认值，客户端 A/B 分别设置不同且非默认的 work_mem 测试值（本次值见实际记录）；事务池确认 A/B 轮流使用同一数据库连接且各自读回自己的值，随后 C 使用该连接读回直连默认值。会话池只检查 A/B/C 参数隔离，不要求交换物理连接。',
    'tx_disconnect_cleanup': '客户端在事务内设置 work_mem=32MB 后直接断连；新客户端必须取得原物理连接，且读到物理直连默认值，证明未提交参数没有留给下一客户端。',
    'routing_and_discard_boundaries': '同一客户端在写侧、读侧之间切换，先读取默认值并 SET 为不同的测试值，再读取该值并核对物理后端身份；事务外 DISCARD ALL 后恢复初始参数，事务内 DISCARD 返回 SQLSTATE 25001，回滚后保留本次重新建立的事务前基线值。',
    'local_backend_reclaim': '新连接的第一条业务请求执行 SET work_mem=32MB，随后查询确认生效；RESET 后恢复直连默认值；已分配后端的另一连接 SET 64MB 后仍可正确查询。物理回收与缓存状态由独立内部检查证明。',
    'mode_owner_isolation': '计划检查同一代理进程内两种模式交错时的状态归属；当前按单账号范围排除此专项，报告记 SKIPPED，不能视为已验证。',
    'product_cache_boundaries': '通过测试代理观察真实缓存：Parse、Bind、Describe 不提前写正式参数，Execute 后才写入；事务内工作缓存与提交后的会话缓存分别核对，并检查后端回收状态。',
    'local_commit_failure': '注入本地响应构造、参数应用及响应入队失败，要求故障点真实触发、不返回成功标签、不提升失败候选，并关闭失败连接。',
    'execute_registration_failure': '注入事务预记录、pending、outstanding 和转发失败，检查失败候选不提升、不返回成功响应，不能把超时或未触发当作故障处理成功。',
}


def plan_scope(row):
    return {
        '拓扑': {'mmr': '多主 MMR', 'replication': '主备复制'}.get(row.get('topology'), row.get('topology', '未保存')),
        '连接池': {'transaction': '事务池（事务结束后可交给其他客户端）', 'session': '会话池（连接期间固定后端）'}.get(row.get('pool'), row.get('pool', '未保存')),
        '预处理语句保留': {True: '开启', False: '关闭'}.get(row.get('reserve'), '未保存'),
        '请求方式': {'Q': '简单查询协议', 'E': '扩展协议（解析、绑定、执行）', 'product': '内部缓存与故障取证'}.get(row.get('protocol'), row.get('protocol', '未保存')),
    }
