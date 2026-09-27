import unittest
from pathlib import Path

from tools.architecture import architecture_violations


ROOT = Path(__file__).resolve().parents[1]


class ArchitectureTest(unittest.TestCase):
    def test_target_packages_follow_dependency_and_size_rules(self):
        self.assertEqual([], architecture_violations(ROOT))
