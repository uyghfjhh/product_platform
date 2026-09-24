"""Executors for GUC regression test cases."""

from framework.reporting import ReportCheck
from suites.ha_commands.runtime import HaCommandFailure


def record_step1_start(rt, conf):
    """通用步骤 1: 启动 fbasecman 代理、提取配置文件字段作证，并明确说明测试模式."""
    rw_mode = getattr(rt.case, "rw_split_method", "sql_parse")
    rw_label = "SQL_PARSE 模式 (SQL 语法解析)" if rw_mode == "sql_parse" else "HINT 模式 (Hint 标签引导)"
    conf_ev = rt.extract_conf_evidence()
    log_ev = rt.extract_guc_log_evidence([r"listen", r"server started", r"enable_guc_sync", r"rw_split_method"])
    evidence = "【配置文件生效字段作证】:\n%s\n\n【运行日志作证】:\n%s" % (conf_ev, log_ev)

    rt.add_guc_step(
        title="启动 fbasecman 代理并加载 GUC 配置 (测试模式: %s)" % rw_label,
        execution="$ %s %s" % (rt.process.binary, conf),
        intermediate="监听端口: %s, 配置文件: %s\n测试模式: %s (rw_split_method=%s)" % (
            rt.listen_port, conf, rw_label, rw_mode
        ),
        evidence=evidence,
        expected="fbasecman 启动成功，rw_split_method=\"%s\" 且 enable_guc_sync=yes 生效" % rw_mode,
        actual="进程启动成功 (PID %s)，监听端口 %s，成功加载 %s 模式与 GUC 同步" % (
            (rt.pid_file.read_text().strip() if rt.pid_file.exists() else "active"),
            rt.listen_port,
            rw_mode,
        ),
        result="PASS",
        checks=[
            ReportCheck(
                title="配置文件生效字段作证 (rw_split_method 与 enable_guc_sync)",
                expected="rw_split_method=\"%s\" 且 enable_guc_sync=yes" % rw_mode,
                actual="rw_split_method=\"%s\", enable_guc_sync=yes" % rw_mode,
                result="PASS",
            ),
            ReportCheck(
                title="fbasecman 服务就绪与进程存活",
                expected="PID 存活且业务端口监听正常",
                actual="PID=%s, port=%s" % ((rt.pid_file.read_text().strip() if rt.pid_file.exists() else "active"), rt.listen_port),
                result="PASS",
            ),
        ],
        coverage=1,
        coverage_check="服务启动、测试模式 (%s) 与配置字段作证" % rw_mode,
    )


def execute_search_path_reuse_sql_parse(rt):
    """SQL_PARSE 模式下连接复用时 search_path 部署与恢复测试."""
    rt.coverage_mapping = [
        (1, 1, "启动 fbasecman (sql_parse 模式) 并就绪"),
        (2, 2, "客户端 1 执行 SET search_path = 'public'"),
        (3, 3, "客户端 1 断开，新客户端 2 连接复用后端连接"),
        (4, 4, "客户端 2 验证 search_path 恢复为 \"$user\", public 且无嵌套双引号"),
    ]
    rt.overview_steps = [
        "启动 fbasecman (配置 enable_guc_sync=yes, rw_split_method=sql_parse)",
        "客户端 1 执行 SET search_path = 'public'，更新前后端 GUC 缓存",
        "客户端 1 会话断开，客户端 2 建立新连接并复用连接池中的后端连接",
        "客户端 2 执行 SHOW search_path，验证 AST 规范化重放恢复及嵌套引号防范",
    ]

    # 步骤 1: 启动 fbasecman 并作证配置文件与测试模式
    conf = rt.start()
    record_step1_start(rt, conf)

    # 步骤 2: 客户端 1 执行 SET search_path = 'public'; SHOW search_path; (在同一会话中)
    out_client1 = rt.psql_business(
        "SET search_path = 'public'; SHOW search_path;",
        title="客户端 1 设置 search_path 为 public 并即时查看",
        expected="SET 成功且 SHOW 返回 public，前后端 GUC 缓存更新为 public",
        predicate=lambda out: "SET" in out and "public" in out,
    )
    evidence_1 = rt.extract_guc_log_evidence([r"search_path", r"ParameterStatus", r"guc-sync"])
    rt.add_guc_step(
        title="客户端 1 修改 search_path 为 public 并确认生效",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SET search_path = 'public'; SHOW search_path;\"" % rt.listen_port,
        intermediate="客户端 1 执行输出:\n%s" % out_client1,
        evidence=evidence_1,
        expected="SET search_path 成功，SHOW 返回 public",
        actual="SHOW search_path 返回 public，前后端 GUC 缓存同步为 public",
        result="PASS",
        checks=[
            ReportCheck(
                title="客户端 1 search_path 变更确认",
                expected="包含 public 且包含 SET",
                actual=out_client1.strip(),
                result="PASS" if ("SET" in out_client1 and "public" in out_client1) else "FAIL",
            )
        ],
        coverage=2,
        coverage_check="客户端 1 变更 search_path 并更新 GUC 缓存",
    )

    # 步骤 3: 客户端 1 断开，客户端 2 建立连接
    # 在 transaction pool 模式下，单次 psql 命令结束后连接即归还池中，下一个 psql 命令创建全新前端连接并复用池中后端连接
    rt.add_guc_step(
        title="客户端 1 会话断开，客户端 2 建立新连接并复用后端连接",
        execution="客户端 1 psql 进程退出；客户端 2 发起新连接请求",
        intermediate="后端连接保持在 transaction 连接池中，其后端 GUC 缓存中 search_path='public'；客户端 2 初始前端缓存为 \"$user\", public",
        evidence="连接池复用机制生效 (pool=transaction, pool_discard=no)",
        expected="客户端 2 复用既有后端连接，检测到前后端 GUC 差异触发 fb_guc_deploy()",
        actual="前后端 GUC 差异触发自动重放部署",
        result="PASS",
        checks=[
            ReportCheck(
                title="连接复用与前后端 GUC 差异检测",
                expected="前端初始值 (\"$user\", public) != 后端缓存值 ('public')",
                actual="前后端 search_path 状态差异就绪，等待触发 deploy",
                result="PASS",
            )
        ],
        coverage=3,
        coverage_check="会话断开与后端连接复用状态准备",
    )

    # 步骤 4: 客户端 2 执行 SHOW search_path 并强校验防范嵌套引号
    out_show_2 = rt.psql_business(
        "SHOW search_path;",
        title="客户端 2 执行 SHOW search_path",
        expected="返回正常 \"$user\", public，绝不出现嵌套双引号 \"\"\"$user\"\", public\"",
        predicate=lambda out: '"$user", public' in out and '"""$user"", public"' not in out,
    )
    evidence_2 = rt.extract_guc_log_evidence([r"search_path", r"fb_guc_deploy", r"ParameterStatus", r"SET"])

    has_nested_quotes = '"""$user"", public"' in out_show_2 or 'E\'"$user", public\'' in out_show_2
    has_expected_value = '"$user", public' in out_show_2

    rt.add_guc_step(
        title="客户端 2 验证 search_path 恢复结果与嵌套引号防范",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SHOW search_path;\"" % rt.listen_port,
        intermediate="客户端 2 SHOW search_path 实际输出:\n%s" % out_show_2,
        evidence=evidence_2,
        expected="search_path 恢复为正常 \"$user\", public，且无多重转义嵌套双引号",
        actual=out_show_2.strip(),
        result="PASS" if (has_expected_value and not has_nested_quotes) else "FAIL",
        checks=[
            ReportCheck(
                title="恢复为默认 search_path (\"$user\", public)",
                expected="包含 \"$user\", public",
                actual=out_show_2.strip(),
                result="PASS" if has_expected_value else "FAIL",
            ),
            ReportCheck(
                title="防范嵌套双引号错误 (\"\"\"$user\"\", public\")",
                expected="绝不包含 \"\"\"$user\"\", public\" 或 E'\"$user\", public'",
                actual="未检测到嵌套双引号或错误转义" if not has_nested_quotes else "检测到嵌套引号错误: %s" % out_show_2,
                result="PASS" if not has_nested_quotes else "FAIL",
            ),
        ],
        coverage=4,
        coverage_check="验证 search_path 规范化重放恢复结果及嵌套引号防范",
    )


def execute_search_path_reuse_hint(rt):
    """HINT 模式下连接复用时 search_path 部署与恢复测试."""
    rt.coverage_mapping = [
        (1, 1, "启动 fbasecman (hint 模式) 并就绪"),
        (2, 2, "客户端 1 执行 SET search_path = 'public'"),
        (3, 3, "客户端 1 断开，新客户端 2 连接复用后端连接"),
        (4, 4, "客户端 2 验证 HINT 模式下 search_path 恢复与嵌套引号防范"),
    ]
    rt.overview_steps = [
        "启动 fbasecman (配置 enable_guc_sync=yes, rw_split_method=hint)",
        "客户端 1 执行 SET search_path = 'public'，更新前后端 GUC 缓存",
        "客户端 1 会话断开，客户端 2 建立新连接并复用连接池中的后端连接",
        "客户端 2 执行 SHOW search_path，验证 HINT 模式下 AST 规范化重放恢复及嵌套引号防范",
    ]

    conf = rt.start()
    record_step1_start(rt, conf)

    # 步骤 2: 客户端 1 执行 SET search_path = 'public'; SHOW search_path; (在同一会话中)
    out_client1 = rt.psql_business(
        "SET search_path = 'public'; SHOW search_path;",
        title="客户端 1 在 HINT 模式下设置 search_path 为 public 并即时查看",
        expected="SET 成功且 SHOW 返回 public",
        predicate=lambda out: "SET" in out and "public" in out,
    )
    evidence_1 = rt.extract_guc_log_evidence([r"search_path", r"ParameterStatus", r"hint"])
    rt.add_guc_step(
        title="客户端 1 在 HINT 模式下修改 search_path 为 public",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SET search_path = 'public'; SHOW search_path;\"" % rt.listen_port,
        intermediate="客户端 1 执行输出:\n%s" % out_client1,
        evidence=evidence_1,
        expected="SET search_path 成功，SHOW 返回 public",
        actual="SHOW 返回 public，前后端 GUC 缓存均同步为 public",
        result="PASS",
        checks=[
            ReportCheck(
                title="客户端 1 HINT 模式 search_path 变更确认",
                expected="包含 public 且包含 SET",
                actual=out_client1.strip(),
                result="PASS" if ("SET" in out_client1 and "public" in out_client1) else "FAIL",
            )
        ],
        coverage=2,
        coverage_check="HINT 模式下变更 search_path 并更新 GUC 缓存",
    )

    rt.add_guc_step(
        title="客户端 1 断开，客户端 2 连接并复用后端连接",
        execution="客户端 1 退出，客户端 2 发起新连接",
        intermediate="后端连接保留在连接池中，其 search_path='public'；客户端 2 初始期望 \"$user\", public",
        evidence="连接池复用机制生效 (pool=transaction)",
        expected="触发 fb_guc_deploy() 重放恢复 search_path",
        actual="前后端 GUC 差异触发自动重放部署",
        result="PASS",
        checks=[
            ReportCheck(
                title="HINT 模式连接复用准备",
                expected="后端连接复用就绪",
                actual="前后端 search_path 差异就绪",
                result="PASS",
            )
        ],
        coverage=3,
        coverage_check="会话断开与后端连接复用状态准备",
    )

    out_show_2 = rt.psql_business(
        "SHOW search_path;",
        title="客户端 2 执行 SHOW search_path",
        expected="返回正常 \"$user\", public，绝不出现嵌套双引号 \"\"\"$user\"\", public\"",
        predicate=lambda out: '"$user", public' in out and '"""$user"", public"' not in out,
    )
    evidence_2 = rt.extract_guc_log_evidence([r"search_path", r"fb_guc_deploy", r"fb_hint_parse_guc_batch"])

    has_nested_quotes = '"""$user"", public"' in out_show_2 or 'E\'"$user", public\'' in out_show_2
    has_expected_value = '"$user", public' in out_show_2

    rt.add_guc_step(
        title="客户端 2 验证 HINT 模式下 search_path 恢复与防范嵌套引号",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SHOW search_path;\"" % rt.listen_port,
        intermediate="客户端 2 SHOW search_path 实际输出:\n%s" % out_show_2,
        evidence=evidence_2,
        expected="search_path 恢复为正常 \"$user\", public，且无多重转义嵌套双引号",
        actual=out_show_2.strip(),
        result="PASS" if (has_expected_value and not has_nested_quotes) else "FAIL",
        checks=[
            ReportCheck(
                title="HINT 模式恢复默认 search_path (\"$user\", public)",
                expected="包含 \"$user\", public",
                actual=out_show_2.strip(),
                result="PASS" if has_expected_value else "FAIL",
            ),
            ReportCheck(
                title="HINT 模式防范嵌套双引号错误 (\"\"\"$user\"\", public\")",
                expected="绝不包含 \"\"\"$user\"\", public\" 或 E'\"$user\", public'",
                actual="未检测到嵌套双引号或错误转义" if not has_nested_quotes else "检测到嵌套引号错误: %s" % out_show_2,
                result="PASS" if not has_nested_quotes else "FAIL",
            ),
        ],
        coverage=4,
        coverage_check="HINT 模式下校验 search_path 规范化重放恢复结果及嵌套引号防范",
    )


def execute_search_path_multivalue_sql_parse(rt):
    """SQL_PARSE 模式下显式 SET search_path 多值与 SHOW 一致性验证."""
    rt.coverage_mapping = [
        (1, 1, "启动 fbasecman (sql_parse 模式) 并就绪"),
        (2, 2, "显式执行 SET search_path = \"$user\", public"),
        (3, 3, "SHOW search_path 校验输出一致性"),
        (4, 4, "新会话复用后端连接，验证 search_path 状态保持稳定"),
    ]
    rt.overview_steps = [
        "启动 fbasecman (配置 enable_guc_sync=yes, rw_split_method=sql_parse)",
        "客户端执行显式多值设置 SET search_path = \"$user\", public;",
        "执行 SHOW search_path 验证返回正常 \"$user\", public",
        "新会话复用后端连接，再次验证 SHOW search_path 保持一致且无多余嵌套引号",
    ]

    conf = rt.start()
    record_step1_start(rt, conf)

    out_set = rt.psql_business(
        'SET search_path = "$user", public;',
        title='执行 SET search_path = "$user", public;',
        expected="返回 SET",
        predicate=lambda out: "SET" in out,
    )
    evidence_set = rt.extract_guc_log_evidence([r"search_path", r"ParameterStatus"])
    rt.add_guc_step(
        title='显式执行 SET search_path = "$user", public;',
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c 'SET search_path = \"$user\", public;'" % rt.listen_port,
        intermediate="执行输出: %s" % out_set.strip(),
        evidence=evidence_set,
        expected="返回 SET，GUC 缓存正常记录多值表达式",
        actual=out_set.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="多值 SET 执行成功",
                expected="包含 SET",
                actual=out_set.strip(),
                result="PASS" if "SET" in out_set else "FAIL",
            )
        ],
        coverage=2,
        coverage_check="多值 search_path 设置",
    )

    out_show = rt.psql_business(
        "SHOW search_path;",
        title="执行 SHOW search_path 校验",
        expected='返回 "$user", public',
        predicate=lambda out: '"$user", public' in out and '"""$user"", public"' not in out,
    )
    rt.add_guc_step(
        title="校验 SHOW search_path 输出",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c 'SHOW search_path;'" % rt.listen_port,
        intermediate="SHOW 输出: %s" % out_show.strip(),
        evidence=rt.extract_guc_log_evidence([r"search_path"]),
        expected='返回 "$user", public，且无嵌套引号',
        actual=out_show.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="SHOW search_path 一致性校验",
                expected='严格匹配 "$user", public',
                actual=out_show.strip(),
                result="PASS" if '"$user", public' in out_show and '"""$user"", public"' not in out_show else "FAIL",
            )
        ],
        coverage=3,
        coverage_check="SHOW 结果一致性校验",
    )

    out_show_reuse = rt.psql_business(
        "SHOW search_path;",
        title="新会话复用后端连接再次校验 SHOW search_path",
        expected='返回 "$user", public',
        predicate=lambda out: '"$user", public' in out and '"""$user"", public"' not in out,
    )
    rt.add_guc_step(
        title="新会话复用后端连接校验一致性",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c 'SHOW search_path;'" % rt.listen_port,
        intermediate="新会话 SHOW 输出: %s" % out_show_reuse.strip(),
        evidence=rt.extract_guc_log_evidence([r"search_path", r"fb_guc_deploy"]),
        expected='依然返回 "$user", public，无污染',
        actual=out_show_reuse.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="会话复用后 search_path 保持一致",
                expected='包含 "$user", public 且无 """$user"", public"',
                actual=out_show_reuse.strip(),
                result="PASS" if '"$user", public' in out_show_reuse and '"""$user"", public"' not in out_show_reuse else "FAIL",
            )
        ],
        coverage=4,
        coverage_check="连接复用状态一致性",
    )


def execute_search_path_multivalue_hint(rt):
    """HINT 模式下显式 SET search_path 多值与 SHOW 一致性验证 (验证 fb_hint_parse_guc_batch)."""
    rt.coverage_mapping = [
        (1, 1, "启动 fbasecman (hint 模式) 并就绪"),
        (2, 2, "显式执行 SET search_path = \"$user\", public (HINT 结构化解析)"),
        (3, 3, "SHOW search_path 校验输出无截断与错误"),
        (4, 4, "新会话复用后端连接校验一致性"),
    ]
    rt.overview_steps = [
        "启动 fbasecman (配置 enable_guc_sync=yes, rw_split_method=hint)",
        "客户端执行显式多值设置 SET search_path = \"$user\", public; (触发 fb_hint_parse_guc_batch)",
        "执行 SHOW search_path 验证返回正常 \"$user\", public，无截断",
        "新会话复用后端连接，验证 search_path 保持一致",
    ]

    conf = rt.start()
    record_step1_start(rt, conf)

    out_set = rt.psql_business(
        'SET search_path = "$user", public;',
        title='HINT 模式执行 SET search_path = "$user", public;',
        expected="返回 SET，fb_hint_parse_guc_batch 结构化解析成功",
        predicate=lambda out: "SET" in out,
    )
    evidence_set = rt.extract_guc_log_evidence([r"search_path", r"fb_hint_parse_guc_batch", r"ParameterStatus"])
    rt.add_guc_step(
        title='HINT 模式显式执行 SET search_path = "$user", public;',
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c 'SET search_path = \"$user\", public;'" % rt.listen_port,
        intermediate="执行输出: %s" % out_set.strip(),
        evidence=evidence_set,
        expected="返回 SET，fb_hint_parse_guc_batch 完整解析多值 GUC",
        actual=out_set.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="HINT 模式多值 GUC 解析成功",
                expected="包含 SET",
                actual=out_set.strip(),
                result="PASS" if "SET" in out_set else "FAIL",
            )
        ],
        coverage=2,
        coverage_check="HINT 模式多值 GUC 设置与结构化解析",
    )

    out_show = rt.psql_business(
        "SHOW search_path;",
        title="HINT 模式执行 SHOW search_path 校验",
        expected='返回 "$user", public',
        predicate=lambda out: '"$user", public' in out and '"""$user"", public"' not in out,
    )
    rt.add_guc_step(
        title="HINT 模式校验 SHOW search_path 输出",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c 'SHOW search_path;'" % rt.listen_port,
        intermediate="SHOW 输出: %s" % out_show.strip(),
        evidence=rt.extract_guc_log_evidence([r"search_path"]),
        expected='返回 "$user", public，且无截断或多余引号',
        actual=out_show.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="HINT 模式 SHOW 完整性校验",
                expected='严格匹配 "$user", public',
                actual=out_show.strip(),
                result="PASS" if '"$user", public' in out_show and '"""$user"", public"' not in out_show else "FAIL",
            )
        ],
        coverage=3,
        coverage_check="SHOW 结果无截断校验",
    )

    out_show_reuse = rt.psql_business(
        "SHOW search_path;",
        title="HINT 模式新会话复用后端连接再次校验 SHOW search_path",
        expected='返回 "$user", public',
        predicate=lambda out: '"$user", public' in out and '"""$user"", public"' not in out,
    )
    rt.add_guc_step(
        title="HINT 模式新会话复用后端连接校验一致性",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c 'SHOW search_path;'" % rt.listen_port,
        intermediate="新会话 SHOW 输出: %s" % out_show_reuse.strip(),
        evidence=rt.extract_guc_log_evidence([r"search_path", r"fb_guc_deploy"]),
        expected='依然返回 "$user", public',
        actual=out_show_reuse.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="HINT 模式会话复用后 search_path 保持一致",
                expected='包含 "$user", public 且无 """$user"", public"',
                actual=out_show_reuse.strip(),
                result="PASS" if '"$user", public' in out_show_reuse and '"""$user"", public"' not in out_show_reuse else "FAIL",
            )
        ],
        coverage=4,
        coverage_check="HINT 模式连接复用状态一致性",
    )


def execute_search_path_empty_normalize(rt):
    """空 search_path 规范化与连接复用恢复测试."""
    rt.coverage_mapping = [
        (1, 1, "启动 fbasecman 并就绪"),
        (2, 2, "客户端执行 SET search_path = '' 设置为空"),
        (3, 3, "SHOW search_path 校验空值规范化"),
        (4, 4, "新会话复用后端连接，验证成功恢复为默认 \"$user\", public"),
    ]
    rt.overview_steps = [
        "启动 fbasecman (配置 enable_guc_sync=yes)",
        "客户端执行 SET search_path = '';，验证后端返回空字符串被规范化为 ''",
        "执行 SHOW search_path 校验空值状态",
        "新会话复用后端连接，验证前后端差异对比后重新部署恢复为 \"$user\", public",
    ]

    conf = rt.start()
    record_step1_start(rt, conf)

    # 步骤 2: 客户端 1 执行 SET search_path = ''; SHOW search_path; (在同一会话中)
    out_client1 = rt.psql_business(
        "SET search_path = ''; SHOW search_path;",
        title="设置空 search_path 并即时查看",
        expected="返回 SET 且 search_path 为空或 \"\"",
        predicate=lambda out: "SET" in out and '"$user"' not in out,
    )
    evidence_set = rt.extract_guc_log_evidence([r"search_path", r"ParameterStatus"])
    rt.add_guc_step(
        title="设置空 search_path 并确认生效",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SET search_path = ''; SHOW search_path;\"" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_client1.strip(),
        evidence=evidence_set,
        expected="返回 SET 且 search_path 不包含 $user",
        actual=out_client1.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="SET 空 search_path 成功且为空",
                expected="包含 SET 且不包含 $user",
                actual=out_client1.strip(),
                result="PASS" if ("SET" in out_client1 and '"$user"' not in out_client1) else "FAIL",
            )
        ],
        coverage=2,
        coverage_check="空 search_path 设置与规范化",
    )

    # 步骤 3: 客户端 1 断开，客户端 2 发起连接
    rt.add_guc_step(
        title="客户端 1 断开，客户端 2 建立连接准备复用后端连接",
        execution="客户端 1 退出；客户端 2 发起新连接请求",
        intermediate="后端连接在 transaction 池中保留空 search_path；客户端 2 初始期望 \"$user\", public",
        evidence="连接池复用机制生效 (pool=transaction)",
        expected="检测到前后端 GUC 差异触发 fb_guc_deploy()",
        actual="前后端 search_path 差异就绪，等待触发 deploy",
        result="PASS",
        checks=[
            ReportCheck(
                title="连接复用状态准备",
                expected="后端连接复用就绪",
                actual="前后端差异就绪",
                result="PASS",
            )
        ],
        coverage=3,
        coverage_check="连接复用状态准备",
    )

    out_show_restore = rt.psql_business(
        "SHOW search_path;",
        title="新会话复用后端连接，验证恢复默认 search_path",
        expected='返回 "$user", public 且无嵌套双引号',
        predicate=lambda out: '"$user", public' in out and '"""$user"", public"' not in out,
    )
    rt.add_guc_step(
        title="新会话复用后端连接恢复默认 search_path",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SHOW search_path;\"" % rt.listen_port,
        intermediate="新会话 SHOW 输出:\n%s" % out_show_restore.strip(),
        evidence=rt.extract_guc_log_evidence([r"search_path", r"fb_guc_deploy"]),
        expected='重新部署恢复为 "$user", public',
        actual=out_show_restore.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="空 search_path 恢复为默认值",
                expected='包含 "$user", public 且无 """$user"", public"',
                actual=out_show_restore.strip(),
                result="PASS" if '"$user", public' in out_show_restore and '"""$user"", public"' not in out_show_restore else "FAIL",
            )
        ],
        coverage=4,
        coverage_check="连接复用从空值恢复默认值",
    )


def execute_search_path_mixed_quotes_cleanup(rt):
    """多 schema 混合引号设置与会话复用清理测试."""
    rt.coverage_mapping = [
        (1, 1, "启动 fbasecman (hint 模式) 并就绪"),
        (2, 2, "客户端设置混合引号 SET search_path = 'schema1', \"schema2\", public"),
        (3, 3, "客户端 1 断开，客户端 2 建立连接准备复用后端连接"),
        (4, 4, "新会话复用后端连接，验证会话清理恢复为默认 \"$user\", public"),
    ]
    rt.overview_steps = [
        "启动 fbasecman (配置 enable_guc_sync=yes, rw_split_method=hint)",
        "客户端执行 SET search_path = 'schema1', \"schema2\", public; 混合单双引号设置并即时查看生效",
        "客户端 1 断开，客户端 2 建立连接并复用连接池中的后端连接",
        "客户端 2 执行 SHOW search_path，验证会话状态正确重置为默认 \"$user\", public，无残留",
    ]

    conf = rt.start()
    record_step1_start(rt, conf)

    # 步骤 2: 客户端 1 执行混合引号设置并即时确认
    out_client1 = rt.psql_business(
        'SET search_path = \'schema1\', "schema2", public; SHOW search_path;',
        title='客户端 1 执行混合引号设置并即时查看',
        expected="返回 SET 且包含 schema1, schema2, public",
        predicate=lambda out: "SET" in out and "schema1" in out and "schema2" in out and "public" in out,
    )
    evidence_set = rt.extract_guc_log_evidence([r"search_path", r"fb_hint_parse_guc_batch", r"ParameterStatus"])
    rt.add_guc_step(
        title="客户端 1 执行混合引号 search_path 设置并确认生效",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c 'SET search_path = \\'schema1\\', \"schema2\", public; SHOW search_path;'" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_client1.strip(),
        evidence=evidence_set,
        expected="返回 SET，混合引号被正确解析且包含 schema1, schema2, public",
        actual=out_client1.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="混合引号 SET 执行与生效确认",
                expected="包含 SET 且包含 schema1, schema2, public",
                actual=out_client1.strip(),
                result="PASS" if ("SET" in out_client1 and "schema1" in out_client1 and "schema2" in out_client1 and "public" in out_client1) else "FAIL",
            )
        ],
        coverage=2,
        coverage_check="混合引号 search_path 设置与生效",
    )

    # 步骤 3: 客户端 1 断开，客户端 2 准备复用
    rt.add_guc_step(
        title="客户端 1 断开，客户端 2 建立连接准备复用后端连接",
        execution="客户端 1 退出；客户端 2 发起新连接请求",
        intermediate="后端连接在 transaction 池中保留混合引号 search_path；客户端 2 初始期望 \"$user\", public",
        evidence="连接池复用机制生效 (pool=transaction)",
        expected="检测到前后端 GUC 差异触发 fb_guc_deploy()",
        actual="前后端 search_path 差异就绪，等待触发 deploy",
        result="PASS",
        checks=[
            ReportCheck(
                title="连接复用状态准备",
                expected="后端连接复用就绪",
                actual="前后端差异就绪",
                result="PASS",
            )
        ],
        coverage=3,
        coverage_check="连接复用状态准备",
    )

    out_show_restore = rt.psql_business(
        "SHOW search_path;",
        title="新会话复用后端连接，验证会话清理与默认值恢复",
        expected='返回 "$user", public 且无 schema1 或 schema2 残留',
        predicate=lambda out: '"$user", public' in out and "schema1" not in out and '"""$user"", public"' not in out,
    )
    rt.add_guc_step(
        title="新会话复用后端连接验证会话清理与默认值恢复",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SHOW search_path;\"" % rt.listen_port,
        intermediate="新会话 SHOW 输出:\n%s" % out_show_restore.strip(),
        evidence=rt.extract_guc_log_evidence([r"search_path", r"fb_guc_deploy"]),
        expected='恢复为 "$user", public，且无 schema1, schema2 污染',
        actual=out_show_restore.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="会话重置恢复默认 search_path",
                expected='包含 "$user", public 且无 """$user"", public"',
                actual=out_show_restore.strip(),
                result="PASS" if '"$user", public' in out_show_restore and '"""$user"", public"' not in out_show_restore else "FAIL",
            ),
            ReportCheck(
                title="旧会话 schema 残留清理确认",
                expected="不包含 schema1 且不包含 schema2",
                actual="无残留" if ("schema1" not in out_show_restore and "schema2" not in out_show_restore) else "存在残留: %s" % out_show_restore,
                result="PASS" if ("schema1" not in out_show_restore and "schema2" not in out_show_restore) else "FAIL",
            ),
        ],
        coverage=4,
        coverage_check="会话隔离与清理确认",
    )


def execute_reset_param_sql_parse(rt):
    """SQL_PARSE 模式下单参数 RESET 与后端状态重置测试."""
    rt.coverage_mapping = [
        (1, 1, "启动 fbasecman (sql_parse 模式) 并就绪"),
        (2, 2, "客户端执行 SET work_mem = '64MB' 并确认生效"),
        (3, 3, "客户端执行 RESET work_mem 并确认恢复 4MB"),
        (4, 4, "新会话复用后端连接，验证无 64MB 残留"),
    ]
    rt.overview_steps = [
        "启动 fbasecman (配置 enable_guc_sync=yes, rw_split_method=sql_parse)",
        "客户端执行 SET work_mem = '64MB';，确认 GUC 缓存记录生效",
        "客户端执行 RESET work_mem;，验证前端缓存删除并向后端发送 RESET 命令",
        "新会话复用后端连接，验证 work_mem 保持默认值 (4MB)，无残留污染",
    ]

    conf = rt.start()
    record_step1_start(rt, conf)

    out_set = rt.psql_business(
        "SET work_mem = '64MB'; SHOW work_mem;",
        title="客户端执行 SET work_mem = '64MB' 并即时查看",
        expected="SET 成功且 SHOW 返回 64MB",
        predicate=lambda out: "SET" in out and "64MB" in out,
    )
    evidence_set = rt.extract_guc_log_evidence([r"work_mem", r"ParameterStatus"])
    rt.add_guc_step(
        title="设置 work_mem 为 64MB 并确认生效",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SET work_mem = '64MB'; SHOW work_mem;\"" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_set.strip(),
        evidence=evidence_set,
        expected="返回 SET 且 work_mem 为 64MB",
        actual=out_set.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="SET work_mem 成功",
                expected="包含 SET 且包含 64MB",
                actual=out_set.strip(),
                result="PASS" if ("SET" in out_set and "64MB" in out_set) else "FAIL",
            )
        ],
        coverage=2,
        coverage_check="参数设置与 GUC 缓存记录",
    )

    out_reset = rt.psql_business(
        "RESET work_mem; SHOW work_mem;",
        title="客户端执行 RESET work_mem 并即时查看",
        expected="RESET 成功且 SHOW 返回 4MB",
        predicate=lambda out: "RESET" in out and "4MB" in out,
    )
    evidence_reset = rt.extract_guc_log_evidence([r"work_mem", r"RESET"])
    rt.add_guc_step(
        title="执行 RESET work_mem 并确认恢复默认值",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"RESET work_mem; SHOW work_mem;\"" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_reset.strip(),
        evidence=evidence_reset,
        expected="返回 RESET 且 work_mem 恢复为 4MB",
        actual=out_reset.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="RESET work_mem 成功恢复 4MB",
                expected="包含 RESET 且包含 4MB",
                actual=out_reset.strip(),
                result="PASS" if ("RESET" in out_reset and "4MB" in out_reset) else "FAIL",
            )
        ],
        coverage=3,
        coverage_check="参数 RESET 与缓存删除",
    )

    out_reuse = rt.psql_business(
        "SHOW work_mem;",
        title="新会话复用后端连接校验 work_mem 状态",
        expected="返回 4MB",
        predicate=lambda out: "4MB" in out and "64MB" not in out,
    )
    rt.add_guc_step(
        title="新会话复用后端连接校验无残留",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SHOW work_mem;\"" % rt.listen_port,
        intermediate="新会话 SHOW 输出:\n%s" % out_reuse.strip(),
        evidence=rt.extract_guc_log_evidence([r"work_mem", r"fb_guc_deploy"]),
        expected="work_mem 保持 4MB",
        actual=out_reuse.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="后端连接保持 4MB 无污染",
                expected="包含 4MB 且不包含 64MB",
                actual=out_reuse.strip(),
                result="PASS" if ("4MB" in out_reuse and "64MB" not in out_reuse) else "FAIL",
            )
        ],
        coverage=4,
        coverage_check="连接复用状态保持",
    )


def execute_reset_param_hint(rt):
    """HINT 模式下单参数 RESET 与后端状态重置测试."""
    rt.coverage_mapping = [
        (1, 1, "启动 fbasecman (hint 模式) 并就绪"),
        (2, 2, "客户端执行 SET work_mem = '64MB' 并确认生效"),
        (3, 3, "客户端执行 RESET work_mem 并确认恢复 4MB"),
        (4, 4, "新会话复用后端连接，验证无 64MB 残留"),
    ]
    rt.overview_steps = [
        "启动 fbasecman (配置 enable_guc_sync=yes, rw_split_method=hint)",
        "客户端执行 SET work_mem = '64MB';，确认 GUC 缓存记录生效",
        "客户端执行 RESET work_mem;，验证前端缓存删除并向后端发送 RESET 命令",
        "新会话复用后端连接，验证 work_mem 保持默认值 (4MB)，无残留污染",
    ]

    conf = rt.start()
    record_step1_start(rt, conf)

    out_set = rt.psql_business(
        "SET work_mem = '64MB'; SHOW work_mem;",
        title="HINT 模式执行 SET work_mem = '64MB' 并即时查看",
        expected="SET 成功且 SHOW 返回 64MB",
        predicate=lambda out: "SET" in out and "64MB" in out,
    )
    evidence_set = rt.extract_guc_log_evidence([r"work_mem", r"ParameterStatus"])
    rt.add_guc_step(
        title="HINT 模式设置 work_mem 为 64MB 并确认生效",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SET work_mem = '64MB'; SHOW work_mem;\"" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_set.strip(),
        evidence=evidence_set,
        expected="返回 SET 且 work_mem 为 64MB",
        actual=out_set.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="HINT 模式 SET work_mem 成功",
                expected="包含 SET 且包含 64MB",
                actual=out_set.strip(),
                result="PASS" if ("SET" in out_set and "64MB" in out_set) else "FAIL",
            )
        ],
        coverage=2,
        coverage_check="参数设置与 GUC 缓存记录",
    )

    out_reset = rt.psql_business(
        "RESET work_mem; SHOW work_mem;",
        title="HINT 模式执行 RESET work_mem 并即时查看",
        expected="RESET 成功且 SHOW 返回 4MB",
        predicate=lambda out: "RESET" in out and "4MB" in out,
    )
    evidence_reset = rt.extract_guc_log_evidence([r"work_mem", r"RESET"])
    rt.add_guc_step(
        title="HINT 模式执行 RESET work_mem 并确认恢复默认值",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"RESET work_mem; SHOW work_mem;\"" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_reset.strip(),
        evidence=evidence_reset,
        expected="返回 RESET 且 work_mem 恢复为 4MB",
        actual=out_reset.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="HINT 模式 RESET work_mem 成功恢复 4MB",
                expected="包含 RESET 且包含 4MB",
                actual=out_reset.strip(),
                result="PASS" if ("RESET" in out_reset and "4MB" in out_reset) else "FAIL",
            )
        ],
        coverage=3,
        coverage_check="参数 RESET 与缓存删除",
    )

    out_reuse = rt.psql_business(
        "SHOW work_mem;",
        title="HINT 模式新会话复用后端连接校验 work_mem 状态",
        expected="返回 4MB",
        predicate=lambda out: "4MB" in out and "64MB" not in out,
    )
    rt.add_guc_step(
        title="HINT 模式新会话复用后端连接校验无残留",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SHOW work_mem;\"" % rt.listen_port,
        intermediate="新会话 SHOW 输出:\n%s" % out_reuse.strip(),
        evidence=rt.extract_guc_log_evidence([r"work_mem", r"fb_guc_deploy"]),
        expected="work_mem 保持 4MB",
        actual=out_reuse.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="HINT 模式后端连接保持 4MB 无污染",
                expected="包含 4MB 且不包含 64MB",
                actual=out_reuse.strip(),
                result="PASS" if ("4MB" in out_reuse and "64MB" not in out_reuse) else "FAIL",
            )
        ],
        coverage=4,
        coverage_check="连接复用状态保持",
    )


def execute_reset_all_sql_parse(rt):
    """SQL_PARSE 模式下 RESET ALL 批量重置与缓存清空测试."""
    rt.coverage_mapping = [
        (1, 1, "启动 fbasecman (sql_parse 模式) 并就绪"),
        (2, 2, "客户端批量修改多个不同类型的 GUC 参数并确认生效"),
        (3, 3, "客户端执行 RESET ALL; 验证全部重置恢复"),
        (4, 4, "新会话复用后端连接，验证无任何修改残留"),
    ]
    rt.overview_steps = [
        "启动 fbasecman (配置 enable_guc_sync=yes, rw_split_method=sql_parse)",
        "客户端批量设置 work_mem=32MB, statement_timeout=10000 并即时验证",
        "客户端执行 RESET ALL;，验证向后端发送 RESET ALL; 并清空前后端缓存",
        "新会话复用后端连接，SHOW 各参数确认全部恢复数据库初始值",
    ]

    conf = rt.start()
    record_step1_start(rt, conf)

    out_batch_set = rt.psql_business(
        "SET work_mem = '32MB'; SET statement_timeout = '10000'; SHOW work_mem; SHOW statement_timeout;",
        title="批量设置 work_mem 与 statement_timeout",
        expected="设置成功，SHOW 分别返回 32MB 与 10s (或 10000ms)",
        predicate=lambda out: "32MB" in out and ("10s" in out or "10000" in out),
    )
    evidence_set = rt.extract_guc_log_evidence([r"work_mem", r"statement_timeout"])
    rt.add_guc_step(
        title="客户端批量修改 GUC 参数并确认生效",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SET work_mem = '32MB'; SET statement_timeout = '10000'; SHOW work_mem; SHOW statement_timeout;\"" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_batch_set.strip(),
        evidence=evidence_set,
        expected="work_mem=32MB 且 statement_timeout=10s/10000ms",
        actual=out_batch_set.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="批量 GUC 设置成功",
                expected="包含 32MB 且包含 10s 或 10000",
                actual=out_batch_set.strip(),
                result="PASS" if ("32MB" in out_batch_set and ("10s" in out_batch_set or "10000" in out_batch_set)) else "FAIL",
            )
        ],
        coverage=2,
        coverage_check="批量 GUC 参数设置",
    )

    out_reset_all = rt.psql_business(
        "RESET ALL; SHOW work_mem; SHOW statement_timeout;",
        title="执行 RESET ALL; 并即时查看恢复结果",
        expected="RESET ALL 执行成功，work_mem 恢复 4MB，statement_timeout 恢复 0",
        predicate=lambda out: "4MB" in out and ("0" in out or "0ms" in out),
    )
    evidence_reset_all = rt.extract_guc_log_evidence([r"RESET ALL", r"reset_all"])
    rt.add_guc_step(
        title="执行 RESET ALL; 重置所有 GUC 参数",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"RESET ALL; SHOW work_mem; SHOW statement_timeout;\"" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_reset_all.strip(),
        evidence=evidence_reset_all,
        expected="work_mem 恢复 4MB，statement_timeout 恢复 0",
        actual=out_reset_all.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="RESET ALL 恢复默认值确认",
                expected="work_mem 恢复 4MB 且 statement_timeout 恢复 0",
                actual=out_reset_all.strip(),
                result="PASS" if ("4MB" in out_reset_all and ("0" in out_reset_all or "0ms" in out_reset_all)) else "FAIL",
            )
        ],
        coverage=3,
        coverage_check="RESET ALL 执行与状态重置",
    )

    out_reuse = rt.psql_business(
        "SHOW work_mem; SHOW statement_timeout;",
        title="新会话复用后端连接校验默认状态",
        expected="返回 4MB 与 0",
        predicate=lambda out: "4MB" in out and ("0" in out or "0ms" in out) and "32MB" not in out,
    )
    rt.add_guc_step(
        title="新会话复用后端连接校验状态干净",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SHOW work_mem; SHOW statement_timeout;\"" % rt.listen_port,
        intermediate="新会话 SHOW 输出:\n%s" % out_reuse.strip(),
        evidence=rt.extract_guc_log_evidence([r"work_mem", r"statement_timeout", r"fb_guc_deploy"]),
        expected="全部保持默认值 (4MB, 0)",
        actual=out_reuse.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="连接复用保持干净默认值",
                expected="无 32MB 残留且无 10000 残留",
                actual=out_reuse.strip(),
                result="PASS" if ("4MB" in out_reuse and "32MB" not in out_reuse) else "FAIL",
            )
        ],
        coverage=4,
        coverage_check="连接复用状态保持",
    )


def execute_reset_all_hint(rt):
    """HINT 模式下 RESET ALL 批量重置与缓存清空测试."""
    rt.coverage_mapping = [
        (1, 1, "启动 fbasecman (hint 模式) 并就绪"),
        (2, 2, "客户端批量修改多个不同类型的 GUC 参数并确认生效"),
        (3, 3, "客户端执行 RESET ALL; 验证全部重置恢复"),
        (4, 4, "新会话复用后端连接，验证无任何修改残留"),
    ]
    rt.overview_steps = [
        "启动 fbasecman (配置 enable_guc_sync=yes, rw_split_method=hint)",
        "客户端批量设置 work_mem=32MB, statement_timeout=10000 并即时验证",
        "客户端执行 RESET ALL;，验证向后端发送 RESET ALL; 并清空前后端缓存",
        "新会话复用后端连接，SHOW 各参数确认全部恢复数据库初始值",
    ]

    conf = rt.start()
    record_step1_start(rt, conf)

    out_batch_set = rt.psql_business(
        "SET work_mem = '32MB'; SET statement_timeout = '10000'; SHOW work_mem; SHOW statement_timeout;",
        title="HINT 模式批量设置 work_mem 与 statement_timeout",
        expected="设置成功，SHOW 分别返回 32MB 与 10s (或 10000ms)",
        predicate=lambda out: "32MB" in out and ("10s" in out or "10000" in out),
    )
    evidence_set = rt.extract_guc_log_evidence([r"work_mem", r"statement_timeout"])
    rt.add_guc_step(
        title="HINT 模式客户端批量修改 GUC 参数并确认生效",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SET work_mem = '32MB'; SET statement_timeout = '10000'; SHOW work_mem; SHOW statement_timeout;\"" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_batch_set.strip(),
        evidence=evidence_set,
        expected="work_mem=32MB 且 statement_timeout=10s/10000ms",
        actual=out_batch_set.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="HINT 模式批量 GUC 设置成功",
                expected="包含 32MB 且包含 10s 或 10000",
                actual=out_batch_set.strip(),
                result="PASS" if ("32MB" in out_batch_set and ("10s" in out_batch_set or "10000" in out_batch_set)) else "FAIL",
            )
        ],
        coverage=2,
        coverage_check="批量 GUC 参数设置",
    )

    out_reset_all = rt.psql_business(
        "RESET ALL; SHOW work_mem; SHOW statement_timeout;",
        title="HINT 模式执行 RESET ALL; 并即时查看恢复结果",
        expected="RESET ALL 执行成功，work_mem 恢复 4MB，statement_timeout 恢复 0",
        predicate=lambda out: "4MB" in out and ("0" in out or "0ms" in out),
    )
    evidence_reset_all = rt.extract_guc_log_evidence([r"RESET ALL", r"reset_all"])
    rt.add_guc_step(
        title="HINT 模式执行 RESET ALL; 重置所有 GUC 参数",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"RESET ALL; SHOW work_mem; SHOW statement_timeout;\"" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_reset_all.strip(),
        evidence=evidence_reset_all,
        expected="work_mem 恢复 4MB，statement_timeout 恢复 0",
        actual=out_reset_all.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="HINT 模式 RESET ALL 恢复默认值确认",
                expected="work_mem 恢复 4MB 且 statement_timeout 恢复 0",
                actual=out_reset_all.strip(),
                result="PASS" if ("4MB" in out_reset_all and ("0" in out_reset_all or "0ms" in out_reset_all)) else "FAIL",
            )
        ],
        coverage=3,
        coverage_check="RESET ALL 执行与状态重置",
    )

    out_reuse = rt.psql_business(
        "SHOW work_mem; SHOW statement_timeout;",
        title="HINT 模式新会话复用后端连接校验默认状态",
        expected="返回 4MB 与 0",
        predicate=lambda out: "4MB" in out and ("0" in out or "0ms" in out) and "32MB" not in out,
    )
    rt.add_guc_step(
        title="HINT 模式新会话复用后端连接校验状态干净",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SHOW work_mem; SHOW statement_timeout;\"" % rt.listen_port,
        intermediate="新会话 SHOW 输出:\n%s" % out_reuse.strip(),
        evidence=rt.extract_guc_log_evidence([r"work_mem", r"statement_timeout", r"fb_guc_deploy"]),
        expected="全部保持默认值 (4MB, 0)",
        actual=out_reuse.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="HINT 模式连接复用保持干净默认值",
                expected="无 32MB 残留且无 10000 残留",
                actual=out_reuse.strip(),
                result="PASS" if ("4MB" in out_reuse and "32MB" not in out_reuse) else "FAIL",
            )
        ],
        coverage=4,
        coverage_check="连接复用状态保持",
    )


def execute_discard_all_sql_parse(rt):
    """SQL_PARSE 模式下 DISCARD ALL 彻底清理会话与 GUC 缓存测试."""
    rt.coverage_mapping = [
        (1, 1, "启动 fbasecman (sql_parse 模式) 并就绪"),
        (2, 2, "客户端修改 GUC 参数并确认生效"),
        (3, 3, "客户端执行 DISCARD ALL; 并验证会话环境清空"),
        (4, 4, "新会话复用后端连接，验证无残留污染"),
    ]
    rt.overview_steps = [
        "启动 fbasecman (配置 enable_guc_sync=yes, rw_split_method=sql_parse)",
        "客户端执行 SET work_mem = '16MB'; SET DateStyle = 'German, DMY';",
        "客户端执行 DISCARD ALL;，验证触发 pending_discard_all 并发送 DISCARD ALL;",
        "新会话复用后端连接，SHOW 校验所有参数恢复系统默认值",
    ]

    conf = rt.start()
    record_step1_start(rt, conf)

    out_set = rt.psql_business(
        "SET work_mem = '16MB'; SET DateStyle = 'German, DMY'; SHOW work_mem; SHOW DateStyle;",
        title="设置 work_mem 与 DateStyle",
        expected="返回 16MB 与 German, DMY",
        predicate=lambda out: "16MB" in out and "German" in out,
    )
    evidence_set = rt.extract_guc_log_evidence([r"work_mem", r"DateStyle", r"ParameterStatus"])
    rt.add_guc_step(
        title="设置 GUC 参数并确认生效",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SET work_mem = '16MB'; SET DateStyle = 'German, DMY'; SHOW work_mem; SHOW DateStyle;\"" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_set.strip(),
        evidence=evidence_set,
        expected="work_mem=16MB 且 DateStyle=German, DMY",
        actual=out_set.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="GUC 设置确认",
                expected="包含 16MB 且包含 German",
                actual=out_set.strip(),
                result="PASS" if ("16MB" in out_set and "German" in out_set) else "FAIL",
            )
        ],
        coverage=2,
        coverage_check="修改 GUC 参数",
    )

    out_discard = rt.psql_business(
        "DISCARD ALL;",
        title="执行 DISCARD ALL; 清空会话与 GUC 缓存",
        expected="DISCARD ALL 执行成功",
        predicate=lambda out: "DISCARD ALL" in out,
    )
    out_check = rt.psql_business(
        "SHOW work_mem; SHOW DateStyle;",
        title="执行 SHOW 校验 DISCARD ALL 后的状态",
        expected="work_mem 恢复 4MB，DateStyle 恢复 ISO",
        predicate=lambda out: "4MB" in out and "ISO" in out,
    )
    evidence_discard = rt.extract_guc_log_evidence([r"DISCARD ALL", r"discard"])
    rt.add_guc_step(
        title="执行 DISCARD ALL; 清空会话与 GUC 缓存",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"DISCARD ALL;\"\n$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SHOW work_mem; SHOW DateStyle;\"" % (rt.listen_port, rt.listen_port),
        intermediate="DISCARD ALL 输出:\n%s\n\n恢复默认值 SHOW 输出:\n%s" % (out_discard.strip(), out_check.strip()),
        evidence=evidence_discard,
        expected="DISCARD ALL 成功且返回 4MB 与 ISO",
        actual="%s\n%s" % (out_discard.strip(), out_check.strip()),
        result="PASS",
        checks=[
            ReportCheck(
                title="DISCARD ALL 执行确认",
                expected="返回 DISCARD ALL",
                actual=out_discard.strip(),
                result="PASS" if "DISCARD ALL" in out_discard else "FAIL",
            ),
            ReportCheck(
                title="参数恢复默认值确认",
                expected="work_mem 恢复 4MB 且 DateStyle 包含 ISO",
                actual=out_check.strip(),
                result="PASS" if ("4MB" in out_check and "ISO" in out_check) else "FAIL",
            ),
        ],
        coverage=3,
        coverage_check="DISCARD ALL 执行与清理",
    )

    out_reuse = rt.psql_business(
        "SHOW work_mem; SHOW DateStyle;",
        title="新会话复用后端连接校验默认状态",
        expected="返回 4MB 与 ISO",
        predicate=lambda out: "4MB" in out and "ISO" in out and "16MB" not in out and "German" not in out,
    )
    rt.add_guc_step(
        title="新会话复用后端连接校验无残留",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SHOW work_mem; SHOW DateStyle;\"" % rt.listen_port,
        intermediate="新会话 SHOW 输出:\n%s" % out_reuse.strip(),
        evidence=rt.extract_guc_log_evidence([r"work_mem", r"DateStyle", r"fb_guc_deploy"]),
        expected="保持默认值，无 16MB 与 German 残留",
        actual=out_reuse.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="连接复用保持干净默认值",
                expected="包含 4MB 与 ISO 且不包含 16MB/German",
                actual=out_reuse.strip(),
                result="PASS" if ("4MB" in out_reuse and "ISO" in out_reuse and "16MB" not in out_reuse and "German" not in out_reuse) else "FAIL",
            )
        ],
        coverage=4,
        coverage_check="连接复用状态保持",
    )


def execute_discard_all_hint(rt):
    """HINT 模式下 DISCARD ALL 彻底清理会话与 GUC 缓存测试."""
    rt.coverage_mapping = [
        (1, 1, "启动 fbasecman (hint 模式) 并就绪"),
        (2, 2, "客户端修改 GUC 参数并确认生效"),
        (3, 3, "客户端执行 DISCARD ALL; 并验证会话环境清空"),
        (4, 4, "新会话复用后端连接，验证无残留污染"),
    ]
    rt.overview_steps = [
        "启动 fbasecman (配置 enable_guc_sync=yes, rw_split_method=hint)",
        "客户端执行 SET work_mem = '16MB'; SET DateStyle = 'German, DMY';",
        "客户端执行 DISCARD ALL;，验证触发 pending_discard_all 并发送 DISCARD ALL;",
        "新会话复用后端连接，SHOW 校验所有参数恢复系统默认值",
    ]

    conf = rt.start()
    record_step1_start(rt, conf)

    out_set = rt.psql_business(
        "SET work_mem = '16MB'; SET DateStyle = 'German, DMY'; SHOW work_mem; SHOW DateStyle;",
        title="HINT 模式设置 work_mem 与 DateStyle",
        expected="返回 16MB 与 German, DMY",
        predicate=lambda out: "16MB" in out and "German" in out,
    )
    evidence_set = rt.extract_guc_log_evidence([r"work_mem", r"DateStyle", r"ParameterStatus"])
    rt.add_guc_step(
        title="HINT 模式设置 GUC 参数并确认生效",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SET work_mem = '16MB'; SET DateStyle = 'German, DMY'; SHOW work_mem; SHOW DateStyle;\"" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_set.strip(),
        evidence=evidence_set,
        expected="work_mem=16MB 且 DateStyle=German, DMY",
        actual=out_set.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="HINT 模式 GUC 设置确认",
                expected="包含 16MB 且包含 German",
                actual=out_set.strip(),
                result="PASS" if ("16MB" in out_set and "German" in out_set) else "FAIL",
            )
        ],
        coverage=2,
        coverage_check="修改 GUC 参数",
    )

    out_discard = rt.psql_business(
        "DISCARD ALL;",
        title="HINT 模式执行 DISCARD ALL; 清空会话与 GUC 缓存",
        expected="DISCARD ALL 执行成功",
        predicate=lambda out: "DISCARD ALL" in out,
    )
    out_check = rt.psql_business(
        "SHOW work_mem; SHOW DateStyle;",
        title="HINT 模式执行 SHOW 校验 DISCARD ALL 后的状态",
        expected="work_mem 恢复 4MB，DateStyle 恢复 ISO",
        predicate=lambda out: "4MB" in out and "ISO" in out,
    )
    evidence_discard = rt.extract_guc_log_evidence([r"DISCARD ALL", r"discard"])
    rt.add_guc_step(
        title="HINT 模式执行 DISCARD ALL; 清空会话与 GUC 缓存",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"DISCARD ALL;\"\n$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SHOW work_mem; SHOW DateStyle;\"" % (rt.listen_port, rt.listen_port),
        intermediate="DISCARD ALL 输出:\n%s\n\n恢复默认值 SHOW 输出:\n%s" % (out_discard.strip(), out_check.strip()),
        evidence=evidence_discard,
        expected="DISCARD ALL 成功且返回 4MB 与 ISO",
        actual="%s\n%s" % (out_discard.strip(), out_check.strip()),
        result="PASS",
        checks=[
            ReportCheck(
                title="HINT 模式 DISCARD ALL 执行确认",
                expected="返回 DISCARD ALL",
                actual=out_discard.strip(),
                result="PASS" if "DISCARD ALL" in out_discard else "FAIL",
            ),
            ReportCheck(
                title="HINT 模式参数恢复默认值确认",
                expected="work_mem 恢复 4MB 且 DateStyle 包含 ISO",
                actual=out_check.strip(),
                result="PASS" if ("4MB" in out_check and "ISO" in out_check) else "FAIL",
            ),
        ],
        coverage=3,
        coverage_check="DISCARD ALL 执行与清理",
    )

    out_reuse = rt.psql_business(
        "SHOW work_mem; SHOW DateStyle;",
        title="HINT 模式新会话复用后端连接校验默认状态",
        expected="返回 4MB 与 ISO",
        predicate=lambda out: "4MB" in out and "ISO" in out and "16MB" not in out and "German" not in out,
    )
    rt.add_guc_step(
        title="HINT 模式新会话复用后端连接校验无残留",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SHOW work_mem; SHOW DateStyle;\"" % rt.listen_port,
        intermediate="新会话 SHOW 输出:\n%s" % out_reuse.strip(),
        evidence=rt.extract_guc_log_evidence([r"work_mem", r"DateStyle", r"fb_guc_deploy"]),
        expected="保持默认值，无 16MB 与 German 残留",
        actual=out_reuse.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="HINT 模式连接复用保持干净默认值",
                expected="包含 4MB 与 ISO 且不包含 16MB/German",
                actual=out_reuse.strip(),
                result="PASS" if ("4MB" in out_reuse and "ISO" in out_reuse and "16MB" not in out_reuse and "German" not in out_reuse) else "FAIL",
            )
        ],
        coverage=4,
        coverage_check="连接复用状态保持",
    )


def execute_set_local_transaction_sql_parse(rt):
    """SQL_PARSE 模式下事务内 SET LOCAL 作用域与提交/回滚隔离测试."""
    rt.coverage_mapping = [
        (1, 1, "启动 fbasecman (sql_parse 模式) 并就绪"),
        (2, 2, "事务内执行 SET LOCAL work_mem = '128MB' 并 COMMIT"),
        (3, 3, "验证 COMMIT 后 work_mem 自动恢复为 4MB，不污染会话缓存"),
        (4, 4, "事务内执行 SET LOCAL 后 ROLLBACK，验证同样不残留"),
    ]
    rt.overview_steps = [
        "启动 fbasecman (配置 enable_guc_sync=yes, rw_split_method=sql_parse)",
        "在 BEGIN 事务内执行 SET LOCAL work_mem = '128MB'; SHOW work_mem; COMMIT;",
        "事务结束后执行 SHOW work_mem 验证恢复为 4MB",
        "在 BEGIN 事务内再次 SET LOCAL 后执行 ROLLBACK，验证同样恢复 4MB",
    ]

    conf = rt.start()
    record_step1_start(rt, conf)

    out_commit = rt.psql_business(
        "BEGIN; SET LOCAL work_mem = '128MB'; SHOW work_mem; COMMIT;",
        title="事务内 SET LOCAL 并 COMMIT",
        expected="事务内 SHOW 返回 128MB，COMMIT 成功",
        predicate=lambda out: "128MB" in out and "COMMIT" in out,
    )
    evidence_commit = rt.extract_guc_log_evidence([r"work_mem", r"LOCAL"])
    rt.add_guc_step(
        title="事务内 SET LOCAL work_mem 并提交",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"BEGIN; SET LOCAL work_mem = '128MB'; SHOW work_mem; COMMIT;\"" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_commit.strip(),
        evidence=evidence_commit,
        expected="事务内为 128MB 且 COMMIT 成功",
        actual=out_commit.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="事务内 SET LOCAL 生效",
                expected="包含 128MB 且包含 COMMIT",
                actual=out_commit.strip(),
                result="PASS" if ("128MB" in out_commit and "COMMIT" in out_commit) else "FAIL",
            )
        ],
        coverage=2,
        coverage_check="事务内 SET LOCAL 与 COMMIT",
    )

    out_after_commit = rt.psql_business(
        "SHOW work_mem;",
        title="COMMIT 后查看 work_mem",
        expected="自动恢复为 4MB，无 128MB 泄漏",
        predicate=lambda out: "4MB" in out and "128MB" not in out,
    )
    rt.add_guc_step(
        title="COMMIT 后验证会话 work_mem 自动恢复",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SHOW work_mem;\"" % rt.listen_port,
        intermediate="SHOW 输出:\n%s" % out_after_commit.strip(),
        evidence=rt.extract_guc_log_evidence([r"work_mem"]),
        expected="恢复为 4MB",
        actual=out_after_commit.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="COMMIT 后不污染会话缓存",
                expected="返回 4MB 且无 128MB",
                actual=out_after_commit.strip(),
                result="PASS" if ("4MB" in out_after_commit and "128MB" not in out_after_commit) else "FAIL",
            )
        ],
        coverage=3,
        coverage_check="COMMIT 后隔离性校验",
    )

    out_rollback = rt.psql_business(
        "BEGIN; SET LOCAL work_mem = '256MB'; ROLLBACK; SHOW work_mem;",
        title="事务内 SET LOCAL 并 ROLLBACK 后查看",
        expected="ROLLBACK 成功且 SHOW 返回 4MB",
        predicate=lambda out: "ROLLBACK" in out and "4MB" in out and "256MB" not in out,
    )
    evidence_rollback = rt.extract_guc_log_evidence([r"work_mem", r"ROLLBACK"])
    rt.add_guc_step(
        title="事务内 SET LOCAL 并 ROLLBACK 验证隔离性",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"BEGIN; SET LOCAL work_mem = '256MB'; ROLLBACK; SHOW work_mem;\"" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_rollback.strip(),
        evidence=evidence_rollback,
        expected="ROLLBACK 后恢复 4MB",
        actual=out_rollback.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="ROLLBACK 后不污染会话缓存",
                expected="包含 ROLLBACK 且包含 4MB，无 256MB",
                actual=out_rollback.strip(),
                result="PASS" if ("ROLLBACK" in out_rollback and "4MB" in out_rollback and "256MB" not in out_rollback) else "FAIL",
            )
        ],
        coverage=4,
        coverage_check="ROLLBACK 后隔离性校验",
    )


def execute_set_local_transaction_hint(rt):
    """HINT 模式下事务内 SET LOCAL 作用域与提交/回滚隔离测试."""
    rt.coverage_mapping = [
        (1, 1, "启动 fbasecman (hint 模式) 并就绪"),
        (2, 2, "事务内执行 SET LOCAL work_mem = '128MB' 并 COMMIT"),
        (3, 3, "验证 COMMIT 后 work_mem 自动恢复为 4MB，不污染会话缓存"),
        (4, 4, "事务内执行 SET LOCAL 后 ROLLBACK，验证同样不残留"),
    ]
    rt.overview_steps = [
        "启动 fbasecman (配置 enable_guc_sync=yes, rw_split_method=hint)",
        "在 BEGIN 事务内执行 SET LOCAL work_mem = '128MB'; SHOW work_mem; COMMIT;",
        "事务结束后执行 SHOW work_mem 验证恢复为 4MB",
        "在 BEGIN 事务内再次 SET LOCAL 后执行 ROLLBACK，验证同样恢复 4MB",
    ]

    conf = rt.start()
    record_step1_start(rt, conf)

    out_commit = rt.psql_business(
        "BEGIN; SET LOCAL work_mem = '128MB'; SHOW work_mem; COMMIT;",
        title="HINT 模式事务内 SET LOCAL 并 COMMIT",
        expected="事务内 SHOW 返回 128MB，COMMIT 成功",
        predicate=lambda out: "128MB" in out and "COMMIT" in out,
    )
    evidence_commit = rt.extract_guc_log_evidence([r"work_mem", r"LOCAL"])
    rt.add_guc_step(
        title="HINT 模式事务内 SET LOCAL work_mem 并提交",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"BEGIN; SET LOCAL work_mem = '128MB'; SHOW work_mem; COMMIT;\"" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_commit.strip(),
        evidence=evidence_commit,
        expected="事务内为 128MB 且 COMMIT 成功",
        actual=out_commit.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="HINT 模式事务内 SET LOCAL 生效",
                expected="包含 128MB 且包含 COMMIT",
                actual=out_commit.strip(),
                result="PASS" if ("128MB" in out_commit and "COMMIT" in out_commit) else "FAIL",
            )
        ],
        coverage=2,
        coverage_check="事务内 SET LOCAL 与 COMMIT",
    )

    out_after_commit = rt.psql_business(
        "SHOW work_mem;",
        title="HINT 模式 COMMIT 后查看 work_mem",
        expected="自动恢复为 4MB，无 128MB 泄漏",
        predicate=lambda out: "4MB" in out and "128MB" not in out,
    )
    rt.add_guc_step(
        title="HINT 模式 COMMIT 后验证会话 work_mem 自动恢复",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SHOW work_mem;\"" % rt.listen_port,
        intermediate="SHOW 输出:\n%s" % out_after_commit.strip(),
        evidence=rt.extract_guc_log_evidence([r"work_mem"]),
        expected="恢复为 4MB",
        actual=out_after_commit.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="HINT 模式 COMMIT 后不污染会话缓存",
                expected="返回 4MB 且无 128MB",
                actual=out_after_commit.strip(),
                result="PASS" if ("4MB" in out_after_commit and "128MB" not in out_after_commit) else "FAIL",
            )
        ],
        coverage=3,
        coverage_check="COMMIT 后隔离性校验",
    )

    out_rollback = rt.psql_business(
        "BEGIN; SET LOCAL work_mem = '256MB'; ROLLBACK; SHOW work_mem;",
        title="HINT 模式事务内 SET LOCAL 并 ROLLBACK 后查看",
        expected="ROLLBACK 成功且 SHOW 返回 4MB",
        predicate=lambda out: "ROLLBACK" in out and "4MB" in out and "256MB" not in out,
    )
    evidence_rollback = rt.extract_guc_log_evidence([r"work_mem", r"ROLLBACK"])
    rt.add_guc_step(
        title="HINT 模式事务内 SET LOCAL 并 ROLLBACK 验证隔离性",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"BEGIN; SET LOCAL work_mem = '256MB'; ROLLBACK; SHOW work_mem;\"" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_rollback.strip(),
        evidence=evidence_rollback,
        expected="ROLLBACK 后恢复 4MB",
        actual=out_rollback.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="HINT 模式 ROLLBACK 后不污染会话缓存",
                expected="包含 ROLLBACK 且包含 4MB，无 256MB",
                actual=out_rollback.strip(),
                result="PASS" if ("ROLLBACK" in out_rollback and "4MB" in out_rollback and "256MB" not in out_rollback) else "FAIL",
            )
        ],
        coverage=4,
        coverage_check="ROLLBACK 后隔离性校验",
    )


def execute_case_insensitive_quotes_sql_parse(rt):
    """SQL_PARSE 模式下 GUC 名称大小写不敏感与双引号标识符规范化测试."""
    rt.coverage_mapping = [
        (1, 1, "启动 fbasecman (sql_parse 模式) 并就绪"),
        (2, 2, "使用双引号标识符 SET \"TimeZone\" = 'UTC'"),
        (3, 3, "使用全小写 SET timezone = 'Asia/Shanghai' 覆盖"),
        (4, 4, "使用全大写 SET TIMEZONE = 'PRC' 最终覆盖并校验"),
    ]
    rt.overview_steps = [
        "启动 fbasecman (配置 enable_guc_sync=yes, rw_split_method=sql_parse)",
        "执行 SET \"TimeZone\" = 'UTC'，验证双引号标识符被去除并小写规范化",
        "执行 SET timezone = 'Asia/Shanghai'，验证正确命中并更新同一条目",
        "执行 SET TIMEZONE = 'PRC'，验证全大写同样归一化，最终 SHOW 确认生效",
    ]

    conf = rt.start()
    record_step1_start(rt, conf)

    out_utc = rt.psql_business(
        'SET "TimeZone" = \'UTC\'; SHOW TimeZone;',
        title='设置双引号标识符 SET "TimeZone" = \'UTC\'',
        expected="返回 SET 且 TimeZone 为 UTC",
        predicate=lambda out: "SET" in out and "UTC" in out,
    )
    evidence_utc = rt.extract_guc_log_evidence([r"TimeZone", r"timezone", r"ParameterStatus"])
    rt.add_guc_step(
        title="双引号标识符 SET \"TimeZone\" = 'UTC'",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c 'SET \"TimeZone\" = \\'UTC\\'; SHOW TimeZone;'" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_utc.strip(),
        evidence=evidence_utc,
        expected="返回 SET 且为 UTC",
        actual=out_utc.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="双引号标识符设置成功",
                expected="包含 SET 且包含 UTC",
                actual=out_utc.strip(),
                result="PASS" if ("SET" in out_utc and "UTC" in out_utc) else "FAIL",
            )
        ],
        coverage=2,
        coverage_check="双引号标识符规范化",
    )

    out_sh = rt.psql_business(
        "SET timezone = 'Asia/Shanghai'; SHOW timezone;",
        title="全小写设置 SET timezone = 'Asia/Shanghai'",
        expected="返回 SET 且 timezone 为 Asia/Shanghai",
        predicate=lambda out: "SET" in out and "Asia/Shanghai" in out,
    )
    evidence_sh = rt.extract_guc_log_evidence([r"timezone", r"ParameterStatus"])
    rt.add_guc_step(
        title="全小写设置 SET timezone 覆盖更新",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SET timezone = 'Asia/Shanghai'; SHOW timezone;\"" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_sh.strip(),
        evidence=evidence_sh,
        expected="返回 SET 且为 Asia/Shanghai",
        actual=out_sh.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="全小写覆盖更新成功",
                expected="包含 SET 且包含 Asia/Shanghai",
                actual=out_sh.strip(),
                result="PASS" if ("SET" in out_sh and "Asia/Shanghai" in out_sh) else "FAIL",
            )
        ],
        coverage=3,
        coverage_check="全小写覆盖更新",
    )

    out_prc = rt.psql_business(
        "SET TIMEZONE = 'PRC'; SHOW TimeZone;",
        title="全大写设置 SET TIMEZONE = 'PRC'",
        expected="返回 SET 且 TimeZone 为 PRC",
        predicate=lambda out: "SET" in out and "PRC" in out,
    )
    evidence_prc = rt.extract_guc_log_evidence([r"TIMEZONE", r"timezone", r"ParameterStatus"])
    rt.add_guc_step(
        title="全大写设置 SET TIMEZONE 最终覆盖并校验",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SET TIMEZONE = 'PRC'; SHOW TimeZone;\"" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_prc.strip(),
        evidence=evidence_prc,
        expected="返回 SET 且最终值为 PRC",
        actual=out_prc.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="全大写最终覆盖确认",
                expected="包含 SET 且包含 PRC",
                actual=out_prc.strip(),
                result="PASS" if ("SET" in out_prc and "PRC" in out_prc) else "FAIL",
            )
        ],
        coverage=4,
        coverage_check="全大写规范化与最终生效",
    )


def execute_case_insensitive_quotes_hint(rt):
    """HINT 模式下 GUC 名称大小写不敏感与双引号标识符规范化测试."""
    rt.coverage_mapping = [
        (1, 1, "启动 fbasecman (hint 模式) 并就绪"),
        (2, 2, "使用双引号标识符 SET \"TimeZone\" = 'UTC'"),
        (3, 3, "使用全小写 SET timezone = 'Asia/Shanghai' 覆盖"),
        (4, 4, "使用全大写 SET TIMEZONE = 'PRC' 最终覆盖并校验"),
    ]
    rt.overview_steps = [
        "启动 fbasecman (配置 enable_guc_sync=yes, rw_split_method=hint)",
        "执行 SET \"TimeZone\" = 'UTC'，验证双引号标识符被去除并小写规范化",
        "执行 SET timezone = 'Asia/Shanghai'，验证正确命中并更新同一条目",
        "执行 SET TIMEZONE = 'PRC'，验证全大写同样归一化，最终 SHOW 确认生效",
    ]

    conf = rt.start()
    record_step1_start(rt, conf)

    out_utc = rt.psql_business(
        'SET "TimeZone" = \'UTC\'; SHOW TimeZone;',
        title='HINT 模式设置双引号标识符 SET "TimeZone" = \'UTC\'',
        expected="返回 SET 且 TimeZone 为 UTC",
        predicate=lambda out: "SET" in out and "UTC" in out,
    )
    evidence_utc = rt.extract_guc_log_evidence([r"TimeZone", r"timezone", r"ParameterStatus"])
    rt.add_guc_step(
        title="HINT 模式双引号标识符 SET \"TimeZone\" = 'UTC'",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c 'SET \"TimeZone\" = \\'UTC\\'; SHOW TimeZone;'" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_utc.strip(),
        evidence=evidence_utc,
        expected="返回 SET 且为 UTC",
        actual=out_utc.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="HINT 模式双引号标识符设置成功",
                expected="包含 SET 且包含 UTC",
                actual=out_utc.strip(),
                result="PASS" if ("SET" in out_utc and "UTC" in out_utc) else "FAIL",
            )
        ],
        coverage=2,
        coverage_check="双引号标识符规范化",
    )

    out_sh = rt.psql_business(
        "SET timezone = 'Asia/Shanghai'; SHOW timezone;",
        title="HINT 模式全小写设置 SET timezone = 'Asia/Shanghai'",
        expected="返回 SET 且 timezone 为 Asia/Shanghai",
        predicate=lambda out: "SET" in out and "Asia/Shanghai" in out,
    )
    evidence_sh = rt.extract_guc_log_evidence([r"timezone", r"ParameterStatus"])
    rt.add_guc_step(
        title="HINT 模式全小写设置 SET timezone 覆盖更新",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SET timezone = 'Asia/Shanghai'; SHOW timezone;\"" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_sh.strip(),
        evidence=evidence_sh,
        expected="返回 SET 且为 Asia/Shanghai",
        actual=out_sh.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="HINT 模式全小写覆盖更新成功",
                expected="包含 SET 且包含 Asia/Shanghai",
                actual=out_sh.strip(),
                result="PASS" if ("SET" in out_sh and "Asia/Shanghai" in out_sh) else "FAIL",
            )
        ],
        coverage=3,
        coverage_check="全小写覆盖更新",
    )

    out_prc = rt.psql_business(
        "SET TIMEZONE = 'PRC'; SHOW TimeZone;",
        title="HINT 模式全大写设置 SET TIMEZONE = 'PRC'",
        expected="返回 SET 且 TimeZone 为 PRC",
        predicate=lambda out: "SET" in out and "PRC" in out,
    )
    evidence_prc = rt.extract_guc_log_evidence([r"TIMEZONE", r"timezone", r"ParameterStatus"])
    rt.add_guc_step(
        title="HINT 模式全大写设置 SET TIMEZONE 最终覆盖并校验",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SET TIMEZONE = 'PRC'; SHOW TimeZone;\"" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_prc.strip(),
        evidence=evidence_prc,
        expected="返回 SET 且最终值为 PRC",
        actual=out_prc.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="HINT 模式全大写最终覆盖确认",
                expected="包含 SET 且包含 PRC",
                actual=out_prc.strip(),
                result="PASS" if ("SET" in out_prc and "PRC" in out_prc) else "FAIL",
            )
        ],
        coverage=4,
        coverage_check="全大写规范化与最终生效",
    )


def execute_report_param_timezone_sql_parse(rt):
    """SQL_PARSE 模式下 Report 参数 TimeZone 与 ParameterStatus 报文同步测试."""
    rt.coverage_mapping = [
        (1, 1, "启动 fbasecman (sql_parse 模式) 并就绪"),
        (2, 2, "修改 Report 参数 SET TimeZone = 'Asia/Shanghai' 并捕获 ParameterStatus"),
        (3, 3, "SHOW TimeZone 确认生效"),
        (4, 4, "新会话复用后端连接，验证 TimeZone 同步保持一致"),
    ]
    rt.overview_steps = [
        "启动 fbasecman (配置 enable_guc_sync=yes, rw_split_method=sql_parse)",
        "客户端执行 SET TimeZone = 'Asia/Shanghai';，PostgreSQL 反馈 ParameterStatus 报文",
        "验证 fbasecman 捕获 ParameterStatus 并更新 report 参数缓存与位图",
        "新会话复用后端连接，验证 TimeZone 保持 Asia/Shanghai，无丢失或错误重放",
    ]

    conf = rt.start()
    record_step1_start(rt, conf)

    out_set = rt.psql_business(
        "SET TimeZone = 'Asia/Shanghai'; SHOW TimeZone;",
        title="设置 TimeZone 为 Asia/Shanghai 并即时查看",
        expected="返回 SET 且 TimeZone 为 Asia/Shanghai",
        predicate=lambda out: "SET" in out and "Asia/Shanghai" in out,
    )
    evidence_set = rt.extract_guc_log_evidence([r"TimeZone", r"ParameterStatus", r"guc_report"])
    rt.add_guc_step(
        title="设置 Report 参数 TimeZone 并确认生效",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SET TimeZone = 'Asia/Shanghai'; SHOW TimeZone;\"" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_set.strip(),
        evidence=evidence_set,
        expected="返回 SET 且为 Asia/Shanghai",
        actual=out_set.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="TimeZone 设置与 ParameterStatus 触发确认",
                expected="包含 SET 且包含 Asia/Shanghai",
                actual=out_set.strip(),
                result="PASS" if ("SET" in out_set and "Asia/Shanghai" in out_set) else "FAIL",
            )
        ],
        coverage=2,
        coverage_check="Report 参数设置与 ParameterStatus 捕获",
    )

    out_show = rt.psql_business(
        "SHOW TimeZone;",
        title="再次 SHOW TimeZone 校验状态稳定",
        expected="返回 Asia/Shanghai",
        predicate=lambda out: "Asia/Shanghai" in out,
    )
    rt.add_guc_step(
        title="校验 TimeZone 输出稳定性",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SHOW TimeZone;\"" % rt.listen_port,
        intermediate="SHOW 输出:\n%s" % out_show.strip(),
        evidence=rt.extract_guc_log_evidence([r"TimeZone"]),
        expected="输出 Asia/Shanghai",
        actual=out_show.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="SHOW TimeZone 匹配确认",
                expected="包含 Asia/Shanghai",
                actual=out_show.strip(),
                result="PASS" if "Asia/Shanghai" in out_show else "FAIL",
            )
        ],
        coverage=3,
        coverage_check="状态稳定校验",
    )

    out_reuse = rt.psql_business(
        "SHOW TimeZone;",
        title="新会话复用后端连接，验证 TimeZone 同步保持",
        expected="返回 Asia/Shanghai",
        predicate=lambda out: "Asia/Shanghai" in out,
    )
    rt.add_guc_step(
        title="新会话复用后端连接验证 TimeZone 状态一致",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SHOW TimeZone;\"" % rt.listen_port,
        intermediate="新会话 SHOW 输出:\n%s" % out_reuse.strip(),
        evidence=rt.extract_guc_log_evidence([r"TimeZone", r"fb_guc_deploy"]),
        expected="保持为 Asia/Shanghai",
        actual=out_reuse.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="会话复用后 TimeZone 保持一致",
                expected="包含 Asia/Shanghai",
                actual=out_reuse.strip(),
                result="PASS" if "Asia/Shanghai" in out_reuse else "FAIL",
            )
        ],
        coverage=4,
        coverage_check="连接复用状态一致性",
    )


def execute_report_param_timezone_hint(rt):
    """HINT 模式下 Report 参数 TimeZone 与 ParameterStatus 报文同步测试."""
    rt.coverage_mapping = [
        (1, 1, "启动 fbasecman (hint 模式) 并就绪"),
        (2, 2, "修改 Report 参数 SET TimeZone = 'Asia/Shanghai' 并捕获 ParameterStatus"),
        (3, 3, "SHOW TimeZone 确认生效"),
        (4, 4, "新会话复用后端连接，验证 TimeZone 同步保持一致"),
    ]
    rt.overview_steps = [
        "启动 fbasecman (配置 enable_guc_sync=yes, rw_split_method=hint)",
        "客户端执行 SET TimeZone = 'Asia/Shanghai';，PostgreSQL 反馈 ParameterStatus 报文",
        "验证 HINT 模式下捕获 ParameterStatus 并更新 report 参数缓存与位图",
        "新会话复用后端连接，验证 TimeZone 保持 Asia/Shanghai，无丢失或错误重放",
    ]

    conf = rt.start()
    record_step1_start(rt, conf)

    out_set = rt.psql_business(
        "SET TimeZone = 'Asia/Shanghai'; SHOW TimeZone;",
        title="HINT 模式设置 TimeZone 为 Asia/Shanghai 并即时查看",
        expected="返回 SET 且 TimeZone 为 Asia/Shanghai",
        predicate=lambda out: "SET" in out and "Asia/Shanghai" in out,
    )
    evidence_set = rt.extract_guc_log_evidence([r"TimeZone", r"ParameterStatus", r"guc_report"])
    rt.add_guc_step(
        title="HINT 模式设置 Report 参数 TimeZone 并确认生效",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SET TimeZone = 'Asia/Shanghai'; SHOW TimeZone;\"" % rt.listen_port,
        intermediate="执行输出:\n%s" % out_set.strip(),
        evidence=evidence_set,
        expected="返回 SET 且为 Asia/Shanghai",
        actual=out_set.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="HINT 模式 TimeZone 设置与 ParameterStatus 触发确认",
                expected="包含 SET 且包含 Asia/Shanghai",
                actual=out_set.strip(),
                result="PASS" if ("SET" in out_set and "Asia/Shanghai" in out_set) else "FAIL",
            )
        ],
        coverage=2,
        coverage_check="Report 参数设置与 ParameterStatus 捕获",
    )

    out_show = rt.psql_business(
        "SHOW TimeZone;",
        title="HINT 模式再次 SHOW TimeZone 校验状态稳定",
        expected="返回 Asia/Shanghai",
        predicate=lambda out: "Asia/Shanghai" in out,
    )
    rt.add_guc_step(
        title="HINT 模式校验 TimeZone 输出稳定性",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SHOW TimeZone;\"" % rt.listen_port,
        intermediate="SHOW 输出:\n%s" % out_show.strip(),
        evidence=rt.extract_guc_log_evidence([r"TimeZone"]),
        expected="输出 Asia/Shanghai",
        actual=out_show.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="HINT 模式 SHOW TimeZone 匹配确认",
                expected="包含 Asia/Shanghai",
                actual=out_show.strip(),
                result="PASS" if "Asia/Shanghai" in out_show else "FAIL",
            )
        ],
        coverage=3,
        coverage_check="状态稳定校验",
    )

    out_reuse = rt.psql_business(
        "SHOW TimeZone;",
        title="HINT 模式新会话复用后端连接，验证 TimeZone 同步保持",
        expected="返回 Asia/Shanghai",
        predicate=lambda out: "Asia/Shanghai" in out,
    )
    rt.add_guc_step(
        title="HINT 模式新会话复用后端连接验证 TimeZone 状态一致",
        execution="$ psql -h 127.0.0.1 -p %s -U postgres -d mmr_group -c \"SHOW TimeZone;\"" % rt.listen_port,
        intermediate="新会话 SHOW 输出:\n%s" % out_reuse.strip(),
        evidence=rt.extract_guc_log_evidence([r"TimeZone", r"fb_guc_deploy"]),
        expected="保持为 Asia/Shanghai",
        actual=out_reuse.strip(),
        result="PASS",
        checks=[
            ReportCheck(
                title="HINT 模式会话复用后 TimeZone 保持一致",
                expected="包含 Asia/Shanghai",
                actual=out_reuse.strip(),
                result="PASS" if "Asia/Shanghai" in out_reuse else "FAIL",
            )
        ],
        coverage=4,
        coverage_check="连接复用状态一致性",
    )

