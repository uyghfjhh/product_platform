"""Common regression test suite manifest."""

from .case import CommonCase

COMMON_CASES = (
    CommonCase(
        name="console_commands",
        summary="控制台命令在中英文 Locale 动态反复切换下的列名翻译一致性与并发安全",
        source_sections=(
            "sources/fb_locale.c",
            "sources/fb_column_name.c",
            "sources/console.c",
        ),
        executor="console_commands",
        report_groups=("mmr_group",),
        notes=(
            "备份并恢复系统 /etc/locale.conf。",
            "循环多次在 zh_CN.UTF-8 与 en_US.UTF-8 之间热切换 locale（覆盖 LANG 与 LC_ALL）。",
            "执行 SHOW POOLS、SHOW SERVERS、SHOW GROUPS、SHOW NODES、SHOW NODE_STATUS 等宽表命令。",
            "断言列名随 locale 正确切换为中文或英文，且单次 RowDescription 内绝不混用中英文。",
            "多客户端并发执行 SHOW 并动态切换 locale，验证无崩溃、无数据竞争。",
        ),
    ),
    CommonCase(
        name="err_logger_rotation",
        summary="错误统计轮换与并发写入同步安全性、计数守恒与控制台展示 (8.59)",
        source_sections=(
            "sources/err_logger.h",
            "sources/err_logger.c",
            "sources/counter.c",
            "sources/cron.c",
            "sources/frontend.c",
            "sources/console.c",
        ),
        executor="err_logger_rotation",
        report_groups=("mmr_group",),
        notes=(
            "执行 SHOW ERRORS 和 SHOW ERRORS_PER_ROUTE，校验返回列名结构。",
            "模拟注入多类前端认证失败与未定义路由错误，验证错误计数准确累加。",
            "在后台 cron 时间桶轮换与多 worker 并发写入下，验证错误计数守恒，绝不出现新错误被清零。",
            "多客户端并发执行 SHOW ERRORS 与错误注入，验证无死锁、无断连、无崩溃。",
        ),
    ),
    CommonCase(
        name="route_stats_quantiles",
        summary="统计辅助对象初始化、分位数配置与控制台扩展池展示稳定性 (8.60)",
        source_sections=(
            "sources/route.h",
            "sources/route_pool.h",
            "sources/router.c",
            "sources/err_logger.c",
            "sources/tdigest.c",
            "sources/console.c",
        ),
        executor="route_stats_quantiles",
        report_groups=("mmr_group",),
        notes=(
            "配置 quantiles \"0.99,0.95,0.5\" 参数启动 fbasecman。",
            "执行 SHOW POOLS 与 SHOW POOLS_EXTENDED，逐列严格核对分位数与统计列名。",
            "注入真实耗时查询与事务，填充直方图样本点并验证分位数数值有效性与单调性。",
            "高频并发执行 SHOW POOLS_EXTENDED，验证直方图合并无空指针解引用、无段错误。",
        ),
    ),
    CommonCase(
        name="worker_thread_lifecycle",
        summary="worker 线程私有对象两级初始化、启停生命周期与资源释放 (8.62)",
        source_sections=(
            "sources/thread_global.c",
            "sources/ejection.c",
            "sources/worker.c",
            "sources/frontend.c",
        ),
        executor="worker_thread_lifecycle",
        report_groups=("mmr_group",),
        notes=(
            "配置多 worker (workers 8) 启动 fbasecman，断言所有 worker 私有对象均成功初始化。",
            "多客户端并发建连与请求分发，验证各 worker 线程私有变量正常服务。",
            "发送优雅停止信号，断言所有 worker 线程安全退出，主进程以 0 退出且无死锁或段错误。",
            "连续多次执行高频启停循环，验证无内存/互斥锁泄漏导致的异常崩溃。",
        ),
    ),
)


def validate_manifest():
    names = set()
    for case in COMMON_CASES:
        if case.name in names:
            raise ValueError("duplicate case in common: %s" % case.name)
        names.add(case.name)


def case_items(include_disabled=False):
    return [c for c in COMMON_CASES if include_disabled or c.enabled]


def find_case(name):
    for case in COMMON_CASES:
        if case.name == name or case.target == name:
            return case
    return None
