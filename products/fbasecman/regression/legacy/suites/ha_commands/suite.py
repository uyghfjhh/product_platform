"""HA console command suite entry point."""

from platform_regress.suites.runner import run_cases, run_runtime_case
from .manifest import HA_COMMAND_CASES, case_items, find_case, validate_manifest
from .runtime import HaCommandRuntime
from .helpers import *
from .dispatch import EXECUTORS




def show():
    validate_manifest()
    lines = ["ha_commands - 高可用控制台命令及持久化测试"]
    for case in HA_COMMAND_CASES:
        lines.append("  - %-42s 来源=%s %s" % (
            case.target, ",".join(case.source_sections), case.summary,
        ))
    lines.append("")
    lines.append("Default gate: %d / %d" % (len(case_items()), len(HA_COMMAND_CASES)))
    return "\n".join(lines)


def run_case(root, case):
    return run_runtime_case(
        root, case, HaCommandRuntime, EXECUTORS,
        "命令输入输出及用例声明的配置、日志和运行态证据均符合预期。",
        initialization_step="<runtime initialization failed before steps could run>",
    )


def run(root, target=None):
    validate_manifest()
    selected = [find_case(target)] if target else case_items()
    return run_cases(root, selected, run_case)
