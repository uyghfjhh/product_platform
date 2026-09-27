import os
from pathlib import Path
import unittest

from framework.document_coverage import section_covers_point
from suites.mac.document_coverage import (
    DOCUMENT_TEST_POINTS, exempted_test_points, implemented_case_ids,
    pending_test_points,
)
from suites.mac.suite import SUITE
from suites.mmr.document_coverage import (
    DOCUMENT_TEST_POINTS as MMR_DOCUMENT_TEST_POINTS,
    MMR_AUTOTEST_EXEMPTIONS,
    MMR_AUTOTEST_SCENARIOS,
    exempted_test_points as mmr_exempted_test_points,
    implemented_case_ids as mmr_implemented_case_ids,
    pending_test_points as mmr_pending_test_points,
)
from suites.mmr.suite import SUITE as MMR_SUITE


class DocumentCoverageTest(unittest.TestCase):
    def test_all_eight_transfer_documents_are_registered(self):
        self.assertEqual(len(DOCUMENT_TEST_POINTS), 8)
        self.assertTrue(all(points for points in DOCUMENT_TEST_POINTS.values()))

    def test_every_implemented_case_has_a_document_mapping(self):
        catalog_ids = {case["id"] for case in SUITE["cases"]}
        self.assertEqual(implemented_case_ids(), catalog_ids)

    def test_mapped_cases_reference_the_same_document_as_catalog(self):
        catalog = {case["id"]: case for case in SUITE["cases"]}
        for document, points in DOCUMENT_TEST_POINTS.items():
            for case_ids in points.values():
                for case_id in case_ids:
                    self.assertIn(case_id, catalog)
                    self.assertEqual(catalog[case_id]["document"], document)

    def test_every_unimplemented_point_has_an_explicit_exemption(self):
        self.assertEqual(pending_test_points(), [])
        exemptions = exempted_test_points()
        self.assertEqual(len(exemptions), 7)
        self.assertIn(("三权分立功能转测.md", "5.4.4"), exemptions)

    def test_each_document_point_maps_to_at_most_one_case(self):
        for points in DOCUMENT_TEST_POINTS.values():
            self.assertTrue(all(len(case_ids) <= 1 for case_ids in points.values()))

    def test_section_ranges_cover_only_their_declared_points(self):
        self.assertTrue(section_covers_point("2.2.1-2.2.5,2.2.10", "2.2.4"))
        self.assertTrue(section_covers_point("2.2.1-2.2.5,2.2.10", "2.2.10"))
        self.assertFalse(section_covers_point("2.2.1-2.2.5,2.2.10", "2.2.6"))

    def test_mmr_coverage_maps_only_the_two_transfer_documents(self):
        self.assertEqual(
            set(MMR_DOCUMENT_TEST_POINTS),
            {"多活功能测试文档.md", "多活streaming冲突处理测试文档.md"},
        )

    def test_every_mmr_implemented_case_has_a_document_mapping(self):
        catalog_ids = {case["id"] for case in MMR_SUITE["cases"]}
        self.assertEqual(mmr_implemented_case_ids(), catalog_ids)

    def test_mmr_mapped_cases_reference_the_same_document_as_catalog(self):
        catalog = {case["id"]: case for case in MMR_SUITE["cases"]}
        for document, points in MMR_DOCUMENT_TEST_POINTS.items():
            for point, case_ids in points.items():
                for case_id in case_ids:
                    self.assertIn(case_id, catalog)
                    self.assertEqual(catalog[case_id]["document"], document)
                    self.assertTrue(section_covers_point(
                        catalog[case_id]["section"], point))

    def test_mmr_document_backlog_is_explicit(self):
        self.assertEqual(mmr_pending_test_points(), [])
        exemptions = mmr_exempted_test_points()
        self.assertEqual(set(exemptions), {
            ("多活功能测试文档.md", "9.4.2"),
            ("多活功能测试文档.md", "12"),
        })

    def test_mmr_autotest_scenarios_have_registered_cases(self):
        catalog_ids = {case["id"] for case in MMR_SUITE["cases"]}
        source_root = Path(os.environ.get(
            "FBASE_MMR_AUTOTEST_SOURCE",
            str(Path(__file__).resolve().parents[6] / "postgresql_for_fbase_dev" / "mmr-autotest"),
        ))
        if not source_root.is_dir():
            self.skipTest("MMR autotest reference source unavailable: %s" % source_root)
        discovered = {
            (path.name if path.parent.name == "mmr_conflict_tests"
             else path.relative_to(source_root).as_posix())
            for pattern in ("test_*.py", "mmr_conflict_tests/test_*.py",
                            "fdd_mmr_join_jions/tests/test_*.py")
            for path in source_root.glob(pattern)
        }
        accounted = set(MMR_AUTOTEST_SCENARIOS) | set(MMR_AUTOTEST_EXEMPTIONS)
        self.assertEqual(discovered, accounted)
        for source, case_ids in MMR_AUTOTEST_SCENARIOS.items():
            self.assertTrue(source.endswith(".py"))
            self.assertTrue(case_ids)
            self.assertTrue(set(case_ids).issubset(catalog_ids), source)
        self.assertEqual(set(MMR_AUTOTEST_EXEMPTIONS), {"test_multi_node_demo.py"})


if __name__ == "__main__":
    unittest.main()
