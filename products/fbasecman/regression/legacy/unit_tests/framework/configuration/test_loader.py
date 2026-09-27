import tempfile
import unittest
from pathlib import Path

from framework.configuration import (
    ConfigurationError, isolation_errors, load_config, load_regression_config,
    validate_config,
)


class ConfigurationLoaderTest(unittest.TestCase):
    def test_merges_local_and_explicit_overrides_and_builds_artifact_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "regress.yaml").write_text(
                "framework:\n  output_dir: output\ndatabase:\n  host: base\n  port: 1\n",
                encoding="utf-8",
            )
            (root / "regress.local.yaml").write_text(
                "database:\n  host: local\n", encoding="utf-8"
            )
            extra = root / "extra.yaml"
            extra.write_text("database:\n  port: 2\n", encoding="utf-8")

            loaded = load_regression_config(root, [extra], validate=False)

        self.assertEqual({"host": "local", "port": 2}, loaded.config["database"])
        self.assertEqual(root / "output" / "env" / "logs", loaded.env_logs_dir)
        self.assertEqual(root / "output" / "env" / "test_context.yaml", loaded.test_context_file)
        self.assertEqual(
            (root / "regress.yaml", root / "regress.local.yaml", extra),
            loaded.sources,
        )

    def test_can_select_an_independent_base_and_local_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "regress.yaml").write_text("framework:\n  output_dir: regress-output\n", encoding="utf-8")
            (root / "stable.yaml").write_text(
                "framework:\n  output_dir: stable-output\nstable:\n  duration: 40m\n", encoding="utf-8"
            )
            (root / "stable.local.yaml").write_text("stable:\n  duration: 20m\n", encoding="utf-8")
            extra = root / "extra.yaml"
            extra.write_text("stable:\n  interval: 60\n", encoding="utf-8")
            loaded = load_config(root, "stable.yaml", extra_configs=[extra])

        self.assertEqual("20m", loaded.config["stable"]["duration"])
        self.assertEqual(60, loaded.config["stable"]["interval"])
        self.assertEqual(root / "stable-output", loaded.output_dir)

    def test_environment_output_directory_can_be_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "stable.yaml").write_text(
                "framework:\n  output_dir: output\n  environment_output_dir: output/stable/env\n",
                encoding="utf-8",
            )
            loaded = load_config(root, "stable.yaml")

        self.assertEqual(root / "output" / "stable" / "env", loaded.env_output_dir)
        self.assertEqual(root / "output" / "stable" / "env" / "logs", loaded.env_logs_dir)

    def test_schema_rejects_unknown_fields_and_duplicate_ports(self):
        import copy
        root = Path(__file__).resolve().parents[3]
        config = load_config(root, "stable.yaml").config
        unknown = copy.deepcopy(config)
        unknown["framework"]["typo_timeout"] = 1
        with self.assertRaisesRegex(ConfigurationError, "typo_timeout"):
            validate_config(unknown, "stable")

        duplicate = copy.deepcopy(config)
        duplicate["database"]["ports"]["mmr2"] = duplicate["database"]["ports"]["mmr1"]
        with self.assertRaisesRegex(ConfigurationError, "ports must be unique"):
            validate_config(duplicate, "stable")

    def test_isolation_detects_cross_profile_ports_and_nested_roots(self):
        import copy
        root = Path(__file__).resolve().parents[3]
        regress = load_config(root, "regress.yaml").config
        stable = copy.deepcopy(load_config(root, "stable.yaml").config)
        stable["database"]["ports"]["mmr1"] = regress["database"]["ports"]["mmr1"]
        data_root = regress["database"].get("mmr_data_root", regress["database"]["mmr_postgres_dir"])
        stable["database"]["mmr_data_root"] = data_root + "/stable"

        errors = isolation_errors(root, regress, stable)

        self.assertTrue(any("ports overlap" in item for item in errors))
        self.assertTrue(any("PGDATA roots overlap" in item for item in errors))
