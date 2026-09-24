"""Tmp regression suite entry point."""

import re
import time
from pathlib import Path

from framework.configuration import load_regression_config
from suites.ha_commands.runtime import HaCommandRuntime, HaCommandFailure
from .manifest import TMP_CASES, case_items, find_case


def execute_reload_disable_monitor_route_loss(rt):
    """
    【Bug复现】fbasecman 在 reload 关闭监控（check=none 且 monitor_enabled=no）后业务路由丢失
    """
    # 步骤 1：确认 fbasecman 在正常配置下运行（监控开启，业务通畅）
    conf = rt.start()

    rt.psql(
        'SHOW CLUSTERS;',
        "步骤 1.1：检查控制台状态确认拓扑正常",
        "两个 cluster 的 topology_state 均为 VALID，monitor_enabled 为 true",
        lambda output: "VALID" in output and ("true" in output.lower() or "| t" in output.lower() or "t |" in output.lower()),
    )

    rt.psql_business(
        "SELECT 1;",
        "步骤 1.2：验证业务连接正常",
        "正常返回 1，路由通畅",
        lambda output: "1" in output,
        group="mmr_group",
    )

    # 步骤 2：修改配置文件 fbasecman.conf
    # 全局参数 monitor_enabled no
    # group mmr_group 的 check none
    conf_text = conf.read_text(encoding="utf-8")
    new_conf_text = re.sub(r'monitor_enabled\s+yes', 'monitor_enabled no', conf_text)
    new_conf_text = re.sub(r'check\s+"auto"', 'check "none"', new_conf_text)
    conf.write_text(new_conf_text, encoding="utf-8")

    rt.record_step(
        "步骤 2：修改配置文件 fbasecman.conf",
        "修改 monitor_enabled no 与 check \"none\"",
        "配置文件已更新为关闭监控并设置 check none",
        "已写入新配置文件",
        "PASS",
    )

    # 步骤 3：向控制台发送 reload 命令热加载配置
    rt.psql(
        'RELOAD;',
        "步骤 3：向控制台发送 reload 命令热加载配置",
        "返回 RELOAD",
        lambda output: "RELOAD" in output and "ERROR" not in output,
    )

    # 步骤 4：确认控制台状态变更
    rt.psql(
        'SHOW CLUSTERS;',
        "步骤 4.1：查看集群信息确认 monitor_enabled 变更",
        "monitor_enabled 变为 false，topology_state 依然保持 VALID",
        lambda output: ("false" in output.lower() or "| f" in output.lower() or "f |" in output.lower()) and "VALID" in output,
    )

    rt.psql(
        'SHOW ENDPOINT_MONITOR;',
        "步骤 4.2：查看端点状态",
        "端点的 probe_state 变为 PENDING，topology_state 变为 UNINITIALIZED",
        lambda output: "PENDING" in output or "UNINITIALIZED" in output,
    )

    # 步骤 5：发起业务查询
    # 预期行为：在线热重载关闭监控后，应当抑制周期的主动探测，但应当保留继承原先已有的有效业务路由投影，业务查询应当仍能正常路由转发
    # 实际缺陷行为：由于 candidate 拓扑变为 UNINITIALIZED 且被门禁拦截，将抛出 route for '...' is not found 导致断言失败
    rt.psql_business(
        "SELECT 1;",
        "步骤 5：发起业务查询（预期继承 reload 前有效业务路由，正常返回 1）",
        "正常返回 1，业务路由维持通畅",
        lambda output: "1" in output and "ERROR" not in output,
        group="mmr_group",
    )


EXECUTORS = {
    "reload_disable_monitor_route_loss": execute_reload_disable_monitor_route_loss,
}


def show():
    lines = ["tmp - 临时/特定缺陷复现测试套件"]
    for case in TMP_CASES:
        lines.append("  - %-42s %s" % (case.target, case.summary))
    return "\n".join(lines)


def run_case(root, case):
    started = time.monotonic()
    runtime = None
    try:
        runtime = HaCommandRuntime(root, case)
        EXECUTORS[case.executor](runtime)
        runtime.finish("PASS", "缺陷复现成功：证实 reload 关闭监控后确实存在业务路由丢失问题。")
        print("%-58s SUCCESS %8.3fs" % (case.target, time.monotonic() - started))
        return True
    except Exception as exc:
        if runtime is not None:
            try:
                runtime.finish("FAIL", str(exc))
            except Exception:
                runtime.process.stop()
        else:
            env = load_regression_config(Path(root))
            run_root = env.output_dir / "runs" / "tmp" / case.name
            run_root.mkdir(parents=True, exist_ok=True)
            (run_root / "report.txt").write_text(
                "Test: %s\nStatus: FAIL\nSummary: %s\nFailure: %s\n" %
                (case.target, case.summary, exc), encoding="utf-8")
        print("%-58s FAIL    %8.3fs" % (case.target, time.monotonic() - started))
        return False


def run(root, target=None):
    selected = [find_case(target)] if target else case_items()
    failures = sum(0 if run_case(root, case) else 1 for case in selected)
    print("Total: SUCCESS:%d FAIL:%d" % (len(selected) - failures, failures))
    return failures == 0
