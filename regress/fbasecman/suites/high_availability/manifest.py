"""Manifest of High Availability regression cases covering Chapter 4."""

from .case import HighAvailabilityCase

HIGH_AVAILABILITY_CASES = (
    HighAvailabilityCase(
        name="core_13_monitor_confirm",
        core_id="CORE-13",
        summary="monitor 故障和恢复确认防抖及周期探测验证",
        notes=(
            "记录 monitor_period、monitor_retry_period_ms、失败/恢复确认次数与超时。",
            "停止 A1 备库，持续轮询 SHOW CLUSTERS/GROUP_ROUTING，记录状态变化时间。",
            "故障确认前恢复一次验证连续计数重置；持续故障直到正式确认屏蔽。",
            "启动 A1 制造抖动后持续成功直到确认恢复，重新纳入只读候选。",
        ),
    ),
    HighAvailabilityCase(
        name="core_14_rep_standby_failure",
        core_id="CORE-14",
        summary="复制组备机故障时读回退主库及恢复重新承担读",
        notes=(
            "qa_rep 正常执行读写并留存基线快照。",
            "停止 A1 备库，在故障确认前后分别持续发送读写。",
            "写持续使用 A0，读回退 A0；原绑定 A1 的连接安全失败不假成功。",
            "恢复 A1，满足门禁后重新承担只读业务。",
        ),
    ),
    HighAvailabilityCase(
        name="core_15_rep_primary_failure",
        core_id="CORE-15",
        summary="复制组主库故障但暂不升主时备库继续提供只读",
        notes=(
            "停止 A0 主库，不执行 PARTED、不升主。",
            "持续发送读写，写请求明确失败报错。",
            "健康且保留已确认上游关系的 A1 备库在 NO_PRIMARY/UNRESOLVED 下继续提供只读。",
            "重启 A0 恢复复制后 REFRESH 恢复正常。",
        ),
    ),
    HighAvailabilityCase(
        name="core_16_rep_failover",
        core_id="CORE-16",
        summary="复制组主备切换、SET NODE PARTED 隔离与持久化",
        notes=(
            "停止/隔离 A0 主库，并在控制台执行 SET NODE PARTED A0 核对持久化落盘。",
            "数据库层将 A1 升主，控制台执行 REFRESH CLUSTER site_a。",
            "轮询 SHOW 直到新唯一主和路由可信，新写请求定向到 A1。",
            "将旧 A0 重建为备机后 ACTIVE+REFRESH 恢复拓扑。",
        ),
    ),
    HighAvailabilityCase(
        name="core_17_mmr_write_center_failover",
        core_id="CORE-17",
        summary="MMR 写中心切换、唯一写目标与 SET NODE WRITE 持久化",
        notes=(
            "qa_mmr 初始写向 site_a (A0)。",
            "停止 site_a 当前 primary，观察按 promoted_cluster 自动选择 site_b 当前 primary 承担新写。",
            "验证任何时刻组内只有一个有效写目标，故障中旧事务不自动重放。",
            "恢复 site_a 后，执行 SET NODE WRITE <site_b当前主> IN GROUP qa_mmr 验证人工切换及持久化。",
        ),
    ),
    HighAvailabilityCase(
        name="core_18_balance_single_failure",
        core_id="CORE-18",
        summary="balance/single 节点故障与只读回退策略",
        notes=(
            "记录 qa_bal_rw、qa_bal_ro、qa_single_rw、qa_single_ro 初始路由落点。",
            "停止 B0 验证 balance read_write 重选；停止 B1 验证 balance read_only 仍使用 A1 备机。",
            "停止 A1 验证 single_ro 回退 A 库健康 primary，无候选时明确报错。",
        ),
    ),
    HighAvailabilityCase(
        name="core_19_set_node_atomicity",
        core_id="CORE-19",
        summary="SET NODE 批量原子性、非法节点整体拒绝与重启持久化",
        notes=(
            "SET NODE PARTED A1，检查 SHOW、路由及主配置文件持久化。",
            "SET NODE ACTIVE A1 后处于待恢复状态，REFRESH 至 READY。",
            "执行合法批量 PARTED/ACTIVE 并验证重复节点去重。",
            "执行含不存在节点的批量命令，验证整体拒绝，不允许部分写入。",
            "重启 fbasecman 验证运行态与磁盘配置完全一致。",
        ),
    ),
    HighAvailabilityCase(
        name="core_20_write_promoted_refresh_show",
        core_id="CORE-20",
        summary="SET NODE WRITE/PROMOTED、REFRESH CLUSTER 与 SHOW 状态展示",
        notes=(
            "对 qa_mmr 执行合法 SET NODE PROMOTED 与 SET NODE WRITE ... IN GROUP qa_mmr。",
            "非法命令（不存在的节点/组、对 balance 执行 WRITE 等）整体拒绝且不落盘。",
            "执行 REFRESH CLUSTER 校验复探调用与状态变化。",
            "检查 SHOW DATASOURCES/CLUSTERS/GROUP_MEMBERS/GROUP_ROUTING 字段正确性。",
        ),
    ),
    HighAvailabilityCase(
        name="core_21_reload_parameters_and_structure",
        core_id="CORE-21",
        summary="Reload：无变化、运行参数修改与结构变化",
        notes=(
            "无变化 Reload 前后业务查询成功，PID 保持稳定。",
            "修改 monitor_period 后 Reload，SHOW CONFIG_STATUS 显示 IN_PLACE/SUCCESS。",
            "新增测试组后 Reload，SHOW GROUP_ROUTING 立即出现新组且配置代次递增。",
            "恢复原配置后 Reload，临时组查询明确返回不存在。",
        ),
    ),
    HighAvailabilityCase(
        name="core_22_reload_failure_protection",
        core_id="CORE-22",
        summary="Reload 语法错误与持久化目录只读时的失败保护",
        notes=(
            "构造配置文件语法错误/不存在 cluster，Reload 失败报错且旧配置继续服务。",
            "模拟持久化目录只读，执行 SET NODE 明确返回错误，不产生半写入文件。",
            "恢复目录后重新执行合法操作均成功，旧运行态不被污染。",
        ),
    ),
)


def case_items():
    return list(HIGH_AVAILABILITY_CASES)


def find_case(name):
    for case in HIGH_AVAILABILITY_CASES:
        if case.name == name:
            return case
    raise KeyError("unknown high_availability case: %s" % name)


def validate_manifest():
    if len(HIGH_AVAILABILITY_CASES) != 10:
        return False
    names = [c.name for c in HIGH_AVAILABILITY_CASES]
    return len(names) == len(set(names))
