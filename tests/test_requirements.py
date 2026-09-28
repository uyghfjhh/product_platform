"""Platform tests for the requirement gate registry."""

import unittest

from platform_regress import requirements
from platform_regress.engine import Blocked


class FakeResult(object):
    def __init__(self, rows=(), returncode=0):
        self.rows = rows
        self.returncode = returncode


class FakeContext(object):
    def __init__(self, environment=None, sql_rows=(), command_rc=0, resolvable=None):
        self.environment = environment or {}
        self._sql_rows = sql_rows
        self._command_rc = command_rc
        self._resolvable = resolvable or {"primary"}
        self.resolved = []

    def resolve_node(self, selector):
        self.resolved.append(selector)
        if selector not in self._resolvable:
            raise Blocked("无法解析测试节点: %s" % selector)
        return selector

    def sql(self, node, query, **kwargs):
        return FakeResult(rows=self._sql_rows)

    def command(self, argv, **kwargs):
        return FakeResult(returncode=self._command_rc)


class RequirementGateTest(unittest.TestCase):
    def context(self, **kwargs):
        environment = {"cluster": "mmr", "cluster_name": "mmr",
                       "plugins": [], "node_groups": {}}
        environment.update(kwargs.pop("environment", {}))
        return FakeContext(environment=environment, **kwargs)

    def test_empty_requirements_only_resolves_primary(self):
        context = self.context()
        requirements.evaluate_requirements(context, {})
        self.assertEqual(["primary"], context.resolved)

    def test_clusters_blocks_unlisted_cluster(self):
        with self.assertRaisesRegex(Blocked, "用例仅支持 cluster=mac"):
            requirements.evaluate_requirements(
                self.context(), {"clusters": ["mac"]})

    def test_clusters_allows_listed_cluster(self):
        context = self.context()
        requirements.evaluate_requirements(context, {"clusters": ["mmr"]})
        self.assertEqual(["primary"], context.resolved)

    def test_commands_blocks_missing_binary(self):
        with self.assertRaisesRegex(Blocked, "缺少命令: definitely-missing-cmd"):
            requirements.evaluate_requirements(
                self.context(), {"commands": ["definitely-missing-cmd"]})

    def test_commands_rejects_empty_name(self):
        with self.assertRaises(ValueError):
            requirements.evaluate_requirements(self.context(), {"commands": [""]})

    def test_plugins_blocks_missing_plugin(self):
        with self.assertRaisesRegex(Blocked, "cluster mmr 未启用插件: tde"):
            requirements.evaluate_requirements(
                self.context(), {"plugins": ["tde"]})

    def test_groups_blocks_missing_group(self):
        with self.assertRaisesRegex(Blocked, "cluster mmr 缺少关系组: mmr"):
            requirements.evaluate_requirements(
                self.context(), {"groups": ["mmr"]})

    def test_nodes_resolves_each_selector(self):
        context = self.context(resolvable={"primary", "s1"})
        requirements.evaluate_requirements(context, {"nodes": ["s1"]})
        self.assertEqual(["s1", "primary"], context.resolved)

    def test_system_time_control_blocks_without_sudo(self):
        with self.assertRaisesRegex(Blocked, "免交互 sudo"):
            requirements.evaluate_requirements(
                self.context(command_rc=1), {"system_time_control": True})

    def test_roles_checks_pg_roles(self):
        with self.assertRaisesRegex(Blocked, "缺少数据库角色: auditor"):
            requirements.evaluate_requirements(
                self.context(sql_rows=[["0"]]), {"roles": ["auditor"]})

    def test_roles_passes_when_count_is_one(self):
        context = self.context(sql_rows=[["1"]])
        requirements.evaluate_requirements(context, {"roles": ["auditor"]})

    def test_roles_error_becomes_blocked(self):
        class BoomContext(FakeContext):
            def sql(self, node, query, **kwargs):
                raise RuntimeError("conn refused")

        with self.assertRaisesRegex(Blocked, "无法检查角色 auditor"):
            requirements.evaluate_requirements(
                BoomContext(environment={"cluster": "mmr"}), {"roles": ["auditor"]})

    def test_extensions_checks_pg_available_extensions(self):
        with self.assertRaisesRegex(Blocked, "缺少可用 PostgreSQL 扩展: sm3"):
            requirements.evaluate_requirements(
                self.context(sql_rows=[["0"]]), {"extensions": ["sm3"]})

    def test_order_clusters_before_plugins(self):
        context = self.context()
        with self.assertRaisesRegex(Blocked, "用例仅支持 cluster"):
            requirements.evaluate_requirements(context, {
                "clusters": ["mac"], "plugins": ["missing"]})

    def test_product_evaluator_registration_and_order(self):
        calls = []

        def custom(context, requirements):
            calls.append("custom")
            if requirements.get("custom_key"):
                raise Blocked("custom blocker")

        def marker(context, requirements):
            calls.append("marker")

        requirements.register_requirement("custom_key", custom,
                                          before="system_time_control")
        requirements.register_requirement("marker_key", marker)
        try:
            order = [k for k, _ in requirements._EVALUATORS]
            self.assertLess(order.index("node"), order.index("custom_key"))
            self.assertLess(order.index("custom_key"),
                            order.index("system_time_control"))
            context = self.context()
            requirements.evaluate_requirements(
                context, {"custom_key": True})
            self.fail("expected Blocked")
        except Blocked as exc:
            self.assertEqual("custom blocker", str(exc))
            # custom sits before system_time_control which no-ops.
            self.assertEqual(["custom"], calls)
        finally:
            requirements._EVALUATORS[:] = [
                e for e in requirements._EVALUATORS
                if e[0] not in ("custom_key", "marker_key")
            ]


if __name__ == "__main__":
    unittest.main()
