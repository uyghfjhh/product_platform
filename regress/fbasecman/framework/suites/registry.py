"""Unified Test Suite Registry and Discovery Mechanism.

Provides a single source of truth for all test suites in fbasecman_regress_v2,
shared between the CLI (tools/cli.py) and the Web Console (tools/web_server.py).
"""

import importlib
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class SuiteDefinition:
    """Metadata and execution delegate for a test suite."""

    def __init__(
        self,
        id: str,
        title: str,
        description: str,
        suite_module: str,
        manifest_module: str,
        case_getter: str = "case_items",
        prefix: Optional[str] = None,
        custom_case_filter: Optional[Callable[[List[Any]], List[Any]]] = None,
    ):
        self.id = id
        self.title = title
        self.description = description
        self.suite_module_name = suite_module
        self.manifest_module_name = manifest_module
        self.case_getter_name = case_getter
        self.prefix = prefix or f"{id}."
        self.custom_case_filter = custom_case_filter
        self._suite_module = None
        self._manifest_module = None

    def get_suite_module(self):
        if self._suite_module is None:
            self._suite_module = importlib.import_module(self.suite_module_name)
        return self._suite_module

    def get_manifest_module(self):
        if self._manifest_module is None:
            self._manifest_module = importlib.import_module(self.manifest_module_name)
        return self._manifest_module

    def get_cases(self) -> List[Any]:
        manifest_mod = self.get_manifest_module()
        getter = getattr(manifest_mod, self.case_getter_name, None)
        if getter is None:
            suite_mod = self.get_suite_module()
            getter = getattr(suite_mod, self.case_getter_name, None)

        if getter is not None:
            cases = getter()
        else:
            # Fallback to uppercase convention e.g. GUC_CASES
            cases = getattr(manifest_mod, f"{self.id.upper()}_CASES", [])

        if self.custom_case_filter:
            cases = self.custom_case_filter(cases)
        return list(cases)

    def get_targets(self) -> List[str]:
        cases = self.get_cases()
        targets = []
        for case in cases:
            target = getattr(case, "target", None)
            if not target:
                name = getattr(case, "name", str(case))
                target = f"{self.id}.{name}"
            targets.append(target)
        return targets

    def show(self) -> str:
        suite_mod = self.get_suite_module()
        if hasattr(suite_mod, "show"):
            return suite_mod.show()
        lines = [f"{self.id} - {self.title}"]
        for target in self.get_targets():
            lines.append(f"  - {target}")
        return "\n".join(lines)

    def run(self, root_dir: Path, target: Optional[str] = None) -> int:
        suite_mod = self.get_suite_module()
        try:
            if target:
                res = suite_mod.run(root_dir, target=target)
            else:
                res = suite_mod.run(root_dir)
            if res is False:
                return 1
            return 0
        except Exception as exc:
            print(f"[{self.id}] Suite execution raised exception: {exc}", file=sys.stderr)
            return 1


class SuiteRegistry:
    """Registry managing all test suites."""

    def __init__(self):
        self._suites: Dict[str, SuiteDefinition] = {}
        self._order: List[str] = []

    def register(self, suite_def: SuiteDefinition) -> None:
        if suite_def.id in self._suites:
            raise ValueError(f"Suite '{suite_def.id}' is already registered.")
        self._suites[suite_def.id] = suite_def
        self._order.append(suite_def.id)

    def get(self, suite_id: str) -> Optional[SuiteDefinition]:
        return self._suites.get(suite_id)

    def all_suites(self) -> List[SuiteDefinition]:
        return [self._suites[sid] for sid in self._order]

    def suite_ids(self) -> List[str]:
        return list(self._order)

    def suite_targets(self, suite_id: str) -> List[str]:
        suite_def = self.get(suite_id)
        if not suite_def:
            return []
        return suite_def.get_targets()

    def selected_targets(self, target: str) -> List[str]:
        if "." in target:
            suite_id, _, case_name = target.partition(".")
            suite_def = self.get(suite_id)
            if suite_def:
                targets = suite_def.get_targets()
                if target in targets:
                    return [target]
            return []
        suite_def = self.get(target)
        if suite_def:
            return suite_def.get_targets()
        return []

    def run_target(self, root_dir: Path, target: str, sanitize: bool = True) -> int:
        if sanitize:
            try:
                from framework.environment.sanitizer import preflight_health_check
                preflight_health_check(root_dir, auto_heal=True)
            except Exception:
                pass

        if "." in target:
            suite_id, _, sub_target = target.partition(".")
            suite_def = self.get(suite_id)
            if suite_def:
                return suite_def.run(root_dir, target=sub_target)
            print(f"Unknown suite '{suite_id}' in target '{target}'", file=sys.stderr)
            return 2

        suite_def = self.get(target)
        if suite_def:
            return suite_def.run(root_dir)

        print(f"Run target not implemented or not registered: {target}", file=sys.stderr)
        return 2

    def show_target(self, target: Optional[str]) -> Optional[str]:
        if not target or target == "all":
            parts = []
            for suite_def in self.all_suites():
                parts.append(suite_def.show())
            return "\n\n".join(parts)
        suite_def = self.get(target)
        if suite_def:
            return suite_def.show()
        return None

    def to_web_definitions(self) -> List[Dict[str, Any]]:
        """Export suite definitions for the Web Console."""
        results = []
        for suite_def in self.all_suites():
            cases = suite_def.get_cases()
            results.append({
                "id": suite_def.id,
                "title": suite_def.title,
                "description": suite_def.description,
                "items": cases,
                "prefix": suite_def.prefix,
            })
        return results


def _build_default_registry() -> SuiteRegistry:
    registry = SuiteRegistry()

    # 1. GUC
    registry.register(SuiteDefinition(
        id="guc",
        title="GUC 规范化、多值解析与连接复用测试",
        description="search_path 多值 GUC 规范化、AST 表达式重放、Hint/SQL_PARSE 模式与连接复用防多重转义",
        suite_module="suites.guc.suite",
        manifest_module="suites.guc.manifest",
    ))

    # 2. High Availability
    registry.register(SuiteDefinition(
        id="high_availability",
        title="高可用故障切换与 monitor 探测（方案第四章）",
        description="主备切换、MMR写中心切换、防抖探测与配置重载等核心高可用能力验证",
        suite_module="suites.high_availability.suite",
        manifest_module="suites.high_availability.manifest",
    ))

    # 3. HA Commands
    registry.register(SuiteDefinition(
        id="ha_commands",
        title="高可用控制台命令及持久化",
        description="SET NODE / SET CLUSTER / REFRESH CLUSTER 等控制台命令及配置文件持久化",
        suite_module="suites.ha_commands.suite",
        manifest_module="suites.ha_commands.manifest",
    ))

    # 4. Outstanding
    registry.register(SuiteDefinition(
        id="outstanding",
        title="outstanding 队列与后端 PS 缓存一致性",
        description="outstanding 队列高并发执行、后端 PreparedStatement 缓存生命周期及一致性",
        suite_module="suites.outstanding.suite",
        manifest_module="suites.outstanding.manifest",
    ))

    # 5. Global Cache
    registry.register(SuiteDefinition(
        id="global_cache",
        title="全局 PreparedStatement 缓存回归测试",
        description="全局 PS 缓存生命周期、LRU 淘汰、跨客户端复用与 JDBC 扩展协议支持",
        suite_module="suites.global_cache.suite",
        manifest_module="suites.global_cache.manifest",
        case_getter="formal_case_items",
    ))

    # 6. Handover
    def _handover_filter(cases):
        return [c for c in cases if getattr(c, "enabled", True)]

    registry.register(SuiteDefinition(
        id="handover",
        title="fbasecman 转测文档第 4 至 12 章",
        description="转测文档核心业务能力（MMR/REP 路由模式、客户端连接与管理接口验证）",
        suite_module="suites.handover.suite",
        manifest_module="suites.handover.manifest",
        custom_case_filter=_handover_filter,
    ))

    # 7. SQL Parse
    registry.register(SuiteDefinition(
        id="sql_parse",
        title="SQL_PARSE 扩展协议测试",
        description="基于 SQL 语法解析的读写路由决策及扩展查询协议兼容性测试",
        suite_module="suites.sql_parse.suite",
        manifest_module="suites.sql_parse.manifest",
    ))

    # 8. RW Toggle
    registry.register(SuiteDefinition(
        id="rw_toggle",
        title="读写切换/读写分离回归测试",
        description="单双主拓扑、读写分离策略、Hint 标签模式与端口路由机制切换",
        suite_module="suites.rw_toggle.suite",
        manifest_module="suites.rw_toggle.manifest",
    ))

    # 9. Common
    registry.register(SuiteDefinition(
        id="common",
        title="通用能力、Locale热切换与错误轮换测试",
        description="控制台命令中英文国际化动态热切换、错误统计轮换与并发写入同步安全性",
        suite_module="suites.common.suite",
        manifest_module="suites.common.manifest",
    ))

    # 10. Tmp
    registry.register(SuiteDefinition(
        id="tmp",
        title="临时/特定缺陷复现测试",
        description="专项问题排查与临时 Bug 复现用例（如 reload 关闭监控路由丢失复现）",
        suite_module="suites.tmp.suite",
        manifest_module="suites.tmp.manifest",
    ))

    return registry


_DEFAULT_REGISTRY = None


def get_default_registry() -> SuiteRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = _build_default_registry()
    return _DEFAULT_REGISTRY
