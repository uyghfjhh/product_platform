"""Tmp regression test cases manifest."""

from .case import TmpCase


TMP_CASES = (
    TmpCase(
        name="reload_disable_monitor_route_loss",
        summary="fbasecman 在 reload 关闭监控（check=none 且 monitor_enabled=no）后业务路由丢失",
        executor="reload_disable_monitor_route_loss",
        notes=(
            "【Bug复现】fbasecman 在 reload 关闭监控（check=none 且 monitor_enabled=no）后业务路由丢失",
            "一、问题描述：fbasecman 在正常运行且拓扑状态为 VALID 的情况下，若修改配置文件将监控关闭（monitor_enabled no）并将分组探测设为无（check \"none\"），执行控制台 reload; 后，虽然控制台 SHOW CLUSTERS; 显示拓扑状态仍为 VALID，但业务客户端连接该分组时报错 route for '...' is not found，导致业务请求全部无法路由。",
            "二、前置环境与测试对象：",
            "  - fbasecman 监听地址与端口：动态分配测试端口",
            "  - 控制台管理员账号：admin",
            "  - 底层数据库节点：测试集群 MMR 双集群（pg_cluster_1, pg_cluster_2）",
            "  - 业务账号与数据库：postgres 用户，业务 Group 为 mmr_group",
            "三、复现详细操作步骤：",
            "  - 步骤 1：确认 fbasecman 在正常配置下运行（监控开启，业务通畅）：SHOW CLUSTERS 拓扑均为 VALID，monitor_enabled 为 true；业务 SELECT 1 正常返回。",
            "  - 步骤 2：修改配置文件 fbasecman.conf，将 monitor_enabled 设为 no，group mmr_group 的 check 设为 none。",
            "  - 步骤 3：向控制台发送 reload 命令热加载配置。",
            "  - 步骤 4：确认控制台状态变更：SHOW CLUSTERS 显示 monitor_enabled 为 false 但 topology_state 为 VALID；SHOW ENDPOINT_MONITOR 显示 probe_state 为 PENDING，topology_state 为 UNINITIALIZED。",
            "  - 步骤 5：发起业务查询：使用业务账号连接 mmr_group 执行 SELECT 1，复现报错 route for 'mmr_group.postgres' is not found。",
            "四、预期行为：",
            "  - 在线热重载（reload）前集群已经建立并发布了处于 VALID 的健康拓扑基线。",
            "  - 将 monitor_enabled 设为 no 且 check 设为 none 时，fbasecman 应当抑制周期的主动探测，但应当保留继承原先已有的有效业务路由投影，业务请求应当仍能正常路由转发，而不应返回 route not found。",
        ),
    ),
)


def case_items():
    return list(TMP_CASES)


def find_case(target):
    name = target.split(".", 1)[1] if "." in target else target
    for case in TMP_CASES:
        if case.name == name:
            return case
    raise KeyError("unknown tmp case: %s" % target)
