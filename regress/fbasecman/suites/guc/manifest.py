"""GUC regression test cases manifest."""

from .case import GucCase


GUC_CASES = (
    # 1. 核心缺陷场景：连接复用时的 search_path 重放与防嵌套引号
    GucCase(
        name="search_path_reuse_sql_parse",
        summary="SQL_PARSE 模式下连接复用时 search_path 部署与恢复测试",
        executor="search_path_reuse_sql_parse",
        rw_split_method="sql_parse",
        notes=(
            "测试配置启用 enable_guc_sync 与 rw_split_method sql_parse。",
            "客户端 1 执行 SET search_path = 'public'，使前后端 GUC 缓存更新为 public。",
            "客户端 1 断开，客户端 2 建立连接并复用先前后端连接。",
            "对比前后端 GUC 缓存触发 deploy，验证重放表达式不生成带 E 引号的错误语法。",
            "执行 SHOW search_path 验证恢复为正常 \"$user\", public，绝不出现 \"\"\"$user\"\", public\" 嵌套引号错误。",
        ),
    ),
    GucCase(
        name="search_path_reuse_hint",
        summary="HINT 模式下连接复用时 search_path 部署与恢复测试",
        executor="search_path_reuse_hint",
        rw_split_method="hint",
        notes=(
            "测试配置启用 enable_guc_sync 与 rw_split_method hint。",
            "客户端 1 执行 SET search_path = 'public'，使前后端 GUC 缓存更新为 public。",
            "客户端 1 断开，客户端 2 建立连接并复用先前后端连接。",
            "对比前后端 GUC 缓存触发 deploy，验证 HINT 模式下 AST 规范化生效，重放正常表达式。",
            "执行 SHOW search_path 验证恢复为正常 \"$user\", public，绝不出现 \"\"\"$user\"\", public\" 嵌套引号错误。",
        ),
    ),

    # 2. 多值 GUC 显式设置与 SHOW 一致性
    GucCase(
        name="search_path_multivalue_sql_parse",
        summary="SQL_PARSE 模式下显式 SET search_path 多值与 SHOW 一致性验证",
        executor="search_path_multivalue_sql_parse",
        rw_split_method="sql_parse",
        notes=(
            "测试配置启用 enable_guc_sync 与 rw_split_method sql_parse。",
            "显式执行 SET search_path = \"$user\", public;。",
            "执行 SHOW search_path 验证返回 \"$user\", public。",
            "断开并由新连接复用后端，验证 GUC 缓存状态与 SHOW 结果一致。",
        ),
    ),
    GucCase(
        name="search_path_multivalue_hint",
        summary="HINT 模式下显式 SET search_path 多值与 SHOW 一致性验证",
        executor="search_path_multivalue_hint",
        rw_split_method="hint",
        notes=(
            "测试配置启用 enable_guc_sync 与 rw_split_method hint。",
            "验证 HINT 模式接入 fb_hint_parse_guc_batch 后正确识别多值 GUC。",
            "显式执行 SET search_path = \"$user\", public;。",
            "执行 SHOW search_path 验证返回 \"$user\", public，无语法截断或错误。",
        ),
    ),

    # 3. 边界与特殊值：空 search_path 规范化与混合单双引号
    GucCase(
        name="search_path_empty_normalize",
        summary="空 search_path 规范化与连接复用恢复测试",
        executor="search_path_empty_normalize",
        rw_split_method="sql_parse",
        notes=(
            "测试配置启用 enable_guc_sync。",
            "客户端执行 SET search_path = '';，验证后端报告空文本 \"\" 被规范化为 ''。",
            "断开并由新客户端复用连接，验证重新部署默认 \"$user\", public 正常无错。",
        ),
    ),
    GucCase(
        name="search_path_mixed_quotes_cleanup",
        summary="多 schema 混合引号设置与会话复用清理测试",
        executor="search_path_mixed_quotes_cleanup",
        rw_split_method="hint",
        notes=(
            "测试配置启用 enable_guc_sync 与 rw_split_method hint。",
            "客户端执行 SET search_path = 'schema1', \"schema2\", public; 混合单双引号与普通标识符。",
            "执行 SHOW search_path 确认生效。",
            "断开后新会话复用连接，验证会话状态正确重置为默认 \"$user\", public，无残留污染。",
        ),
    ),

    # 4. 单参数 RESET 机制测试 (SQL_PARSE / HINT)
    GucCase(
        name="reset_param_sql_parse",
        summary="SQL_PARSE 模式下单参数 RESET 与后端状态重置测试",
        executor="reset_param_sql_parse",
        rw_split_method="sql_parse",
        notes=(
            "启用 enable_guc_sync 与 rw_split_method sql_parse。",
            "客户端执行 SET work_mem = '64MB';，确认 GUC 缓存记录生效。",
            "客户端执行 RESET work_mem;，验证前端缓存删除并向后端发送 RESET 命令。",
            "SHOW work_mem 验证恢复数据库默认值 (4MB)。",
            "新会话复用连接，验证后端无残留污染。",
        ),
    ),
    GucCase(
        name="reset_param_hint",
        summary="HINT 模式下单参数 RESET 与后端状态重置测试",
        executor="reset_param_hint",
        rw_split_method="hint",
        notes=(
            "启用 enable_guc_sync 与 rw_split_method hint。",
            "客户端执行 SET work_mem = '64MB';，确认 GUC 缓存记录生效。",
            "客户端执行 RESET work_mem;，验证前端缓存删除并向后端发送 RESET 命令。",
            "SHOW work_mem 验证恢复数据库默认值 (4MB)。",
            "新会话复用连接，验证后端无残留污染。",
        ),
    ),

    # 5. 全局 RESET ALL 机制测试 (SQL_PARSE / HINT)
    GucCase(
        name="reset_all_sql_parse",
        summary="SQL_PARSE 模式下 RESET ALL 批量重置与缓存清空测试",
        executor="reset_all_sql_parse",
        rw_split_method="sql_parse",
        notes=(
            "启用 enable_guc_sync 与 rw_split_method sql_parse。",
            "客户端批量修改多个不同类型的 GUC (work_mem, statement_timeout, DateStyle)。",
            "客户端执行 RESET ALL;，触发 pending_reset_all 并清空前端 GUC 缓存。",
            "验证向后端生成 RESET ALL; 语句并清空后端缓存。",
            "SHOW 各参数确认全部恢复数据库初始值。",
        ),
    ),
    GucCase(
        name="reset_all_hint",
        summary="HINT 模式下 RESET ALL 批量重置与缓存清空测试",
        executor="reset_all_hint",
        rw_split_method="hint",
        notes=(
            "启用 enable_guc_sync 与 rw_split_method hint。",
            "客户端批量修改多个不同类型的 GUC (work_mem, statement_timeout, DateStyle)。",
            "客户端执行 RESET ALL;，触发 pending_reset_all 并清空前端 GUC 缓存。",
            "验证向后端生成 RESET ALL; 语句并清空后端缓存。",
            "SHOW 各参数确认全部恢复数据库初始值。",
        ),
    ),

    # 6. 全局 DISCARD ALL 机制测试 (SQL_PARSE / HINT)
    GucCase(
        name="discard_all_sql_parse",
        summary="SQL_PARSE 模式下 DISCARD ALL 彻底清理会话与 GUC 缓存测试",
        executor="discard_all_sql_parse",
        rw_split_method="sql_parse",
        notes=(
            "启用 enable_guc_sync 与 rw_split_method sql_parse。",
            "客户端修改多个 GUC 参数。",
            "客户端执行 DISCARD ALL;，验证触发 pending_discard_all 并发送 DISCARD ALL;。",
            "SHOW 校验所有参数恢复默认，新连接复用后端状态干净。",
        ),
    ),
    GucCase(
        name="discard_all_hint",
        summary="HINT 模式下 DISCARD ALL 彻底清理会话与 GUC 缓存测试",
        executor="discard_all_hint",
        rw_split_method="hint",
        notes=(
            "启用 enable_guc_sync 与 rw_split_method hint。",
            "客户端修改多个 GUC 参数。",
            "客户端执行 DISCARD ALL;，验证触发 pending_discard_all 并发送 DISCARD ALL;。",
            "SHOW 校验所有参数恢复默认，新连接复用后端状态干净。",
        ),
    ),

    # 7. 事务内局部设置 SET LOCAL 与隔离性测试 (SQL_PARSE / HINT)
    GucCase(
        name="set_local_transaction_sql_parse",
        summary="SQL_PARSE 模式下事务内 SET LOCAL 作用域与提交/回滚隔离测试",
        executor="set_local_transaction_sql_parse",
        rw_split_method="sql_parse",
        notes=(
            "启用 enable_guc_sync 与 rw_split_method sql_parse。",
            "客户端在 BEGIN 事务内执行 SET LOCAL work_mem = '128MB';。",
            "事务内 SHOW work_mem 验证当前事务为 128MB。",
            "事务 COMMIT 后，验证 work_mem 自动恢复为事务前的值 (4MB)，不污染会话缓存。",
            "再次在事务内 SET LOCAL 后 ROLLBACK，验证同样不残留。",
        ),
    ),
    GucCase(
        name="set_local_transaction_hint",
        summary="HINT 模式下事务内 SET LOCAL 作用域与提交/回滚隔离测试",
        executor="set_local_transaction_hint",
        rw_split_method="hint",
        notes=(
            "启用 enable_guc_sync 与 rw_split_method hint。",
            "客户端在 BEGIN 事务内执行 SET LOCAL work_mem = '128MB';。",
            "事务内 SHOW work_mem 验证当前事务为 128MB。",
            "事务 COMMIT 后，验证 work_mem 自动恢复为事务前的值 (4MB)，不污染会话缓存。",
            "再次在事务内 SET LOCAL 后 ROLLBACK，验证同样不残留。",
        ),
    ),

    # 8. 参数名大小写与双引号标识符规范化测试 (SQL_PARSE / HINT)
    GucCase(
        name="case_insensitive_quotes_sql_parse",
        summary="SQL_PARSE 模式下 GUC 名称大小写不敏感与双引号标识符规范化测试",
        executor="case_insensitive_quotes_sql_parse",
        rw_split_method="sql_parse",
        notes=(
            "启用 enable_guc_sync 与 rw_split_method sql_parse。",
            "分别使用 SET \"TimeZone\" = 'UTC'、SET timezone = 'Asia/Shanghai'、SET TIMEZONE = 'PRC' 进行设置。",
            "验证 fb_guc_cache_normalize_key 规范化为统一小写 key，不分裂为重复条目。",
            "SHOW TimeZone 校验最终值为 PRC，前后端缓存保持精确一致。",
        ),
    ),
    GucCase(
        name="case_insensitive_quotes_hint",
        summary="HINT 模式下 GUC 名称大小写不敏感与双引号标识符规范化测试",
        executor="case_insensitive_quotes_hint",
        rw_split_method="hint",
        notes=(
            "启用 enable_guc_sync 与 rw_split_method hint。",
            "分别使用 SET \"TimeZone\" = 'UTC'、SET timezone = 'Asia/Shanghai'、SET TIMEZONE = 'PRC' 进行设置。",
            "验证 HINT 模式下统一规范化，不分裂为重复条目。",
            "SHOW TimeZone 校验最终值为 PRC，前后端缓存保持精确一致。",
        ),
    ),

    # 9. GUC Report 参数与 ParameterStatus 报文同步测试 (SQL_PARSE / HINT)
    GucCase(
        name="report_param_timezone_sql_parse",
        summary="SQL_PARSE 模式下 Report 参数 TimeZone 与 ParameterStatus 报文同步测试",
        executor="report_param_timezone_sql_parse",
        rw_split_method="sql_parse",
        notes=(
            "启用 enable_guc_sync 与 rw_split_method sql_parse。",
            "客户端执行 SET TimeZone = 'Asia/Shanghai';，PostgreSQL 反馈 ParameterStatus 报文。",
            "验证 fbasecman 捕获 ParameterStatus 并更新 report 参数位图与缓存。",
            "新会话复用后端连接，验证 TimeZone 保持一致或正确同步重放。",
        ),
    ),
    GucCase(
        name="report_param_timezone_hint",
        summary="HINT 模式下 Report 参数 TimeZone 与 ParameterStatus 报文同步测试",
        executor="report_param_timezone_hint",
        rw_split_method="hint",
        notes=(
            "启用 enable_guc_sync 与 rw_split_method hint。",
            "客户端执行 SET TimeZone = 'Asia/Shanghai';，PostgreSQL 反馈 ParameterStatus 报文。",
            "验证 HINT 模式下正确处理 ParameterStatus 并同步更新前后端 GUC 缓存。",
            "新会话复用后端连接，验证 TimeZone 保持一致或正确同步重放。",
        ),
    ),
)


def case_items():
    return list(GUC_CASES)


def find_case(name):
    for case in GUC_CASES:
        if case.name == name:
            return case
    raise KeyError("unknown guc case: %s" % name)
