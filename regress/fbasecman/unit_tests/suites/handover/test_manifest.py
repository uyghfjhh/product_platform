import re
import unittest

from suites.handover.manifest import HANDOVER_CASES, case_items, validate_manifest


class HandoverManifestTest(unittest.TestCase):
    def test_source_sections_are_unique_and_complete(self):
        covered = validate_manifest()
        self.assertEqual(85, len(covered))
        self.assertGreaterEqual(len(HANDOVER_CASES), 30)

    def test_default_gate_excludes_long_time_cases(self):
        default = case_items(include_long_time=False)
        self.assertTrue(default)
        self.assertTrue(all(not case.long_time for case in default))
        self.assertGreater(len(case_items()), len(default))
        self.assertEqual(6, len(case_items()) - len(default))

    def test_each_case_has_a_runner_and_source(self):
        for case in HANDOVER_CASES:
            self.assertTrue(case.executor)
            self.assertTrue(case.source_sections)
            self.assertTrue(case.notes)
            self.assertTrue(case.step_mapping or case.step_rules)

    def test_every_case_has_document_content_and_stable_step_rules(self):
        for case in HANDOVER_CASES:
            if case.step_mapping:
                self.assertTrue(all(len(item) == 3 for item in case.step_mapping))
                continue
            self.assertTrue(all(len(item) == 3 for item in case.step_rules))

    def test_4277_uses_its_actual_document_heading(self):
        case = next(item for item in HANDOVER_CASES if item.name == "jdbc_4277_mmr_hint")
        self.assertIn("9.2.2/42.7.7", case.source_sections)
        self.assertNotIn("9.2.1/42.7.7", case.source_sections)

    def test_ha_fault_status_steps_have_document_mappings(self):
        case = next(item for item in HANDOVER_CASES if item.name == "ha_write_leader_failure")
        for title, expected_content in (
            ("配置故障解决方案前的 SHOW NODE_STATUS", "1"),
            ("应用故障解决方案后的 SHOW NODE_STATUS", "2"),
            ("修改数据源状态: pg_220", "2"),
            ("修改故障切换配置", "2"),
            ("删除冲突的故障切换配置", "2"),
            ("console=> RELOAD;", "2"),
            ("8.4 原文的 MMR-to-replication RELOAD", "2"),
            ("重启 fbasecman 加载降级配置", "2"),
            ("D-001 文档降级拓扑在当前模型下无法启动", "2"),
            ("重启后的只读 replication 节点状态", "2"),
        ):
            matches = [content for expression, content, _ in case.step_rules
                       if re.search(expression, title, re.IGNORECASE)]
            self.assertIn(expected_content, matches, title)

    def test_console_server_maintenance_titles_have_document_mappings(self):
        case = next(item for item in HANDOVER_CASES if item.name == "console_server_maintenance")
        for title, expected_content in (
            ("按 ptr 执行 DROP SERVER", "3"),
            ("执行 DROP SERVERS", "2"),
            ("执行 DROP UNUSE_SERVERS", "4"),
            ("PG 节点stop: mmr1", "4"),
        ):
            matches = [content for expression, content, _ in case.step_rules
                       if re.search(expression, title, re.IGNORECASE)]
            self.assertIn(expected_content, matches, title)

    def test_parse_cache_observation_maps_to_cache_cleanup_content(self):
        case = next(item for item in HANDOVER_CASES if item.name == "parse_error_single")
        title = "Parse 失败后立即检查后端 PreparedStatement"
        matches = [content for expression, content, _ in case.step_rules
                   if re.search(expression, title, re.IGNORECASE)]
        self.assertEqual("2", matches[0])
