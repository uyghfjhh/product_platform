import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ArchitectureTest(unittest.TestCase):
    def test_suites_do_not_bypass_framework_execution(self):
        forbidden = {
            "subprocess", "framework.process", "framework.postgres",
            "framework.transport", "framework.environment", "framework.runner",
        }
        violations = []
        for path in (ROOT / "suites").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [item.name for item in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    if name in forbidden or any(name.startswith(item + ".")
                                                for item in forbidden):
                        violations.append("%s imports %s" %
                                          (path.relative_to(ROOT), name))
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
