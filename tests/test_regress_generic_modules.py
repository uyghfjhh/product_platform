"""Platform-native tests for product-neutral regression modules.

Covers: configuration loader/validation/reload, environment registry/sanitizer,
suite registry, and psql client helpers — the generic machinery products build on.
"""

import os
from pathlib import Path

import pytest
import yaml

from platform_regress.clients import assert_table_rows, build_psql_command, parse_psql_table
from platform_regress.configuration import (
    ConfigurationError, ReloadConfigError, has_config_value, install_reload_config,
    load_config, load_regression_config, port_value, reject_unknown, require_mapping,
    require_text, validate_profile_isolation,
)
from platform_regress.environment import (
    EnvironmentProvider, create_environment_provider, preflight_health_check,
    register_environment_provider,
)
from platform_regress.environment import registry as env_registry
from platform_regress.suites import (
    SuitePlugin, SuiteRegistry, case_status, failed_targets, read_last_failed,
    rerun_failed, set_default_preflight_check, set_default_quiet_env_var,
    write_last_failed,
)


# ---------------------------------------------------------------- configuration


def _write_yaml(path, data):
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


class TestConfigurationLoader:
    def test_layered_merge_deep_merges_dicts(self, tmp_path):
        _write_yaml(tmp_path / "regress.yaml", {
            "framework": {"output_dir": "out"},
            "database": {"ports": {"mmr1": 15011}, "host": "127.0.0.1"},
        })
        _write_yaml(tmp_path / "override.yaml", {
            "database": {"ports": {"mmr1": 26011}},
        })
        cfg = load_regression_config(tmp_path, extra_configs=[tmp_path / "override.yaml"], validate=False)
        # deep merge: untouched sibling keys survive
        assert cfg.config["database"]["host"] == "127.0.0.1"
        assert cfg.config["database"]["ports"]["mmr1"] == 26011
        assert cfg.profile == "regression"
        assert len(cfg.sources) == 2

    def test_local_yaml_is_merged_automatically(self, tmp_path):
        _write_yaml(tmp_path / "regress.yaml", {"framework": {"output_dir": "o"}})
        _write_yaml(tmp_path / "regress.local.yaml", {"framework": {"output_dir": "o2"}})
        cfg = load_regression_config(tmp_path, validate=False)
        assert cfg.config["framework"]["output_dir"] == "o2"

    def test_legacy_shell_config_translation(self, tmp_path):
        (tmp_path / "legacy.conf").write_text(
            'MMR_HOST="10.0.0.1"  # comment\n'
            "MMR_PORTS=( 111 222 )\n"
            "PLAIN=abc\n",
            encoding="utf-8",
        )
        _write_yaml(tmp_path / "regress.yaml", {
            "framework": {"output_dir": "o", "legacy_config": "legacy.conf"},
        })

        def mapper(values):
            return {"database": {
                "host": values.get("MMR_HOST"),
                "ports": values.get("MMR_PORTS"),
                "plain": values.get("PLAIN"),
            }}

        cfg = load_regression_config(tmp_path, validate=False, legacy_mapper=mapper)
        assert cfg.config["database"]["host"] == "10.0.0.1"
        assert cfg.config["database"]["ports"] == ["111", "222"]
        assert cfg.config["database"]["plain"] == "abc"

    def test_artifact_paths(self, tmp_path):
        _write_yaml(tmp_path / "regress.yaml", {"framework": {"output_dir": "out"}})
        cfg = load_regression_config(tmp_path, validate=False)
        assert cfg.output_dir == tmp_path / "out"
        assert cfg.env_state_file == tmp_path / "out" / "env" / "env_state.yaml"
        assert cfg.test_context_file == tmp_path / "out" / "env" / "test_context.yaml"

    def test_profile_isolation_blocks_shared_resources(self, tmp_path):
        _write_yaml(tmp_path / "regress.yaml", {"framework": {"output_dir": "o"},
                                                "database": {"ports": {"mmr1": 15011}}})
        _write_yaml(tmp_path / "stable.yaml", {"framework": {"output_dir": "o"},
                                               "database": {"ports": {"mmr1": 15011}}})
        env = load_regression_config(tmp_path, validate=False)

        def isolation(root, regress, stable):
            shared = set(regress["database"]["ports"].values()) & set(stable["database"]["ports"].values())
            return ["shared port %s" % p for p in sorted(shared)] if shared else []

        no_op_validator = lambda config, profile: None
        loader = lambda *args, **kwargs: load_config(
            *args, **dict(kwargs, validator=no_op_validator)
        )
        with pytest.raises(ConfigurationError, match="15011"):
            validate_profile_isolation(env, loader=loader, isolation_check=isolation)

    def test_validation_helpers(self):
        with pytest.raises(ConfigurationError):
            require_mapping({"a": 1}, "a")
        assert require_mapping({"a": {}}, "a") == {}
        with pytest.raises(ConfigurationError, match="unknown field"):
            reject_unknown({"x": 1}, ["y"], "section")
        with pytest.raises(ConfigurationError):
            require_text({"n": "  "}, ["n"], "loc")
        with pytest.raises(ConfigurationError):
            port_value(True, "loc.p")
        with pytest.raises(ConfigurationError):
            port_value(70000, "loc.p")
        assert port_value(5432, "loc.p") == 5432


class TestReloadConfig:
    def test_install_validates_source_before_copy(self, tmp_path):
        source = tmp_path / "reload.conf"
        source.write_text("work_mem 64MB\n", encoding="utf-8")
        target = tmp_path / "auto.conf"
        with pytest.raises(ReloadConfigError, match="shared_buffers"):
            install_reload_config(source, target, [("shared_buffers", "512MB")])
        assert not target.exists()  # nothing installed on failure

    def test_install_atomic_and_verifies_live(self, tmp_path):
        source = tmp_path / "reload.conf"
        source.write_text("shared_buffers 512MB\nwork_mem 64MB\n", encoding="utf-8")
        target = tmp_path / "auto.conf"
        live = install_reload_config(source, target,
                                     [("shared_buffers", "512MB"), ("work_mem", "64MB")])
        assert "shared_buffers 512MB" in live
        assert has_config_value(live, "work_mem", "64MB")
        assert not has_config_value(live, "work_mem", "128MB")
        assert not (tmp_path / "auto.conf.reload.tmp").exists()  # tmp cleaned by os.replace


# ------------------------------------------------------------------ environment


class _FakeProvider(EnvironmentProvider):
    instances = []

    def __init__(self, env, verbose=True):
        self.env = env
        self.healed = False
        _FakeProvider.instances.append(self)

    def setup(self): pass

    def clean(self): pass

    def status_text(self): return "STATUS_TEXT"

    def start(self): pass

    def restart(self): pass

    def stop(self): pass

    def heal(self):
        self.healed = True
        return {"health": {"ok": True}}


class TestEnvironmentRegistry:
    def test_register_and_create(self):
        name = "ut_provider_%d" % id(self)
        register_environment_provider(name, lambda env, verbose=True: _FakeProvider(env, verbose))
        provider = create_environment_provider(name, "ENV", verbose=False)
        assert provider.env == "ENV"

    def test_duplicate_registration_rejected(self):
        register_environment_provider("ut_dup", lambda *a, **k: None)
        with pytest.raises(ValueError, match="already registered"):
            register_environment_provider("ut_dup", lambda *a, **k: None)

    def test_unknown_provider_rejected(self):
        with pytest.raises(ValueError, match="unknown environment provider"):
            create_environment_provider("no_such_provider_ut", None)


class TestPreflightSanitizer:
    def test_heal_path_reports_healed(self, tmp_path):
        _FakeProvider.instances.clear()
        result = preflight_health_check(
            tmp_path, "p", loader=lambda root: "ENV",
            provider_factory=lambda name, env, verbose=False: _FakeProvider(env, verbose),
        )
        assert result["status"] == "HEALED"
        assert _FakeProvider.instances[0].healed

    def test_no_heal_returns_checked(self, tmp_path):
        result = preflight_health_check(
            tmp_path, "p", loader=lambda root: "ENV", auto_heal=False,
            provider_factory=lambda name, env, verbose=False: _FakeProvider(env, verbose),
        )
        assert result == {"status": "CHECKED", "result": "STATUS_TEXT"}

    def test_expected_errors_fail_check(self, tmp_path):
        import types
        env = types.SimpleNamespace(config={})
        result = preflight_health_check(
            tmp_path, "p", loader=lambda root: env,
            expected_errors=lambda health, cfg: ["node X drifted"],
            provider_factory=lambda name, env, verbose=False: _FakeProvider(env, verbose),
        )
        assert result["status"] == "FAILED"
        assert "drifted" in result["reason"]

    def test_loader_failure_captures_diagnostics(self, tmp_path):
        def bad_loader(root):
            raise RuntimeError("yaml unreadable")

        result = preflight_health_check(
            tmp_path, "p", loader=bad_loader,
            provider_factory=lambda name, env, verbose=False: _FakeProvider(env, verbose),
        )
        assert result["status"] == "FAILED"
        assert "yaml unreadable" in result["reason"]


# ----------------------------------------------------------------------- suites


class _FakePlugin(SuitePlugin):
    id = "fake"
    title = "Fake Suite"
    description = "d"
    prefix = "fake"

    def __init__(self, targets=("fake.a", "fake.b")):
        self._targets = list(targets)
        self.ran = []

    def get_targets(self):
        return list(self._targets)

    def get_cases(self):
        return []

    def show(self):
        return "fake suite"

    def run(self, root_dir, target=None):
        self.ran.append(target)
        return 0


class TestSuiteRegistry:
    def test_registration_and_lookup(self):
        reg = SuiteRegistry()
        plugin = _FakePlugin()
        reg.register(plugin)
        assert reg.get("fake") is plugin
        assert reg.suite_ids() == ["fake"]
        assert reg.suite_targets("fake") == ["fake.a", "fake.b"]

    def test_duplicate_registration_rejected(self):
        reg = SuiteRegistry()
        reg.register(_FakePlugin())
        with pytest.raises(ValueError, match="already registered"):
            reg.register(_FakePlugin())

    def test_target_selection(self):
        reg = SuiteRegistry()
        reg.register(_FakePlugin())
        assert reg.selected_targets("fake") == ["fake.a", "fake.b"]
        assert reg.selected_targets("fake.a") == ["fake.a"]
        assert reg.selected_targets("fake.nope") == []
        assert reg.selected_targets("ghost") == []

    def test_run_target_unknown_suite_and_case(self, tmp_path, capsys):
        reg = SuiteRegistry()
        reg.register(_FakePlugin())
        assert reg.run_target(tmp_path, "ghost.a") == 2
        assert reg.run_target(tmp_path, "fake.nope") == 2
        assert "Unknown" in capsys.readouterr().err

    def test_invalid_target_skips_preflight(self, tmp_path):
        calls = []
        reg = SuiteRegistry(preflight_check=lambda root: calls.append(root) or {"status": "HEALED"})
        reg.register(_FakePlugin())
        reg.run_target(tmp_path, "fake.nope")
        assert calls == []  # preflight must not run for invalid targets

    def test_preflight_heal_blocks_on_failure(self, tmp_path, capsys):
        reg = SuiteRegistry(preflight_check=lambda root: {"status": "FAILED", "reason": "unhealthy"})
        plugin = _FakePlugin()
        reg.register(plugin)
        assert reg.run_target(tmp_path, "fake", preflight="heal") == 3
        assert plugin.ran == []  # suite never ran
        assert "unhealthy" in capsys.readouterr().err

    def test_preflight_warn_continues_on_failure(self, tmp_path):
        reg = SuiteRegistry(preflight_check=lambda root: {"status": "FAILED", "reason": "unhealthy"})
        plugin = _FakePlugin()
        reg.register(plugin)
        assert reg.run_target(tmp_path, "fake", preflight="warn") == 0
        assert plugin.ran == [None]

    def test_preflight_off_skips_check(self, tmp_path):
        calls = []
        reg = SuiteRegistry(preflight_check=lambda root: calls.append(root) or {"status": "HEALED"})
        plugin = _FakePlugin()
        reg.register(plugin)
        assert reg.run_target(tmp_path, "fake.a", preflight="off") == 0
        assert calls == []
        assert plugin.ran == ["a"]

    def test_bad_preflight_policy_rejected(self, tmp_path):
        reg = SuiteRegistry()
        reg.register(_FakePlugin())
        with pytest.raises(ValueError, match="preflight"):
            reg.run_target(tmp_path, "fake", preflight="bogus")

    def test_default_preflight_hook(self, tmp_path):
        seen = []
        set_default_preflight_check(lambda root: seen.append(root) or {"status": "HEALED"})
        try:
            reg = SuiteRegistry()  # no explicit checker -> uses default
            plugin = _FakePlugin()
            reg.register(plugin)
            reg.run_target(tmp_path, "fake")
            assert seen == [tmp_path]
        finally:
            set_default_preflight_check(None)

    def test_quiet_env_var_toggled_during_preflight(self, tmp_path, monkeypatch):
        observed = {}

        def checker(root):
            observed["during"] = os.environ.get("MY_QUIET_VAR")
            return {"status": "HEALED"}

        set_default_quiet_env_var("MY_QUIET_VAR")
        try:
            reg = SuiteRegistry(preflight_check=checker)
            reg.register(_FakePlugin())
            reg.run_target(tmp_path, "fake")
            assert observed["during"] == "1"
            assert "MY_QUIET_VAR" not in os.environ  # restored
        finally:
            set_default_quiet_env_var("PLATFORM_REGRESS_QUIET_ENV")

    def test_quiet_env_var_per_registry_override(self, tmp_path):
        observed = {}

        def checker(root):
            observed["custom"] = os.environ.get("CUSTOM_VAR")
            observed["default"] = os.environ.get("PLATFORM_REGRESS_QUIET_ENV")
            return {"status": "HEALED"}

        reg = SuiteRegistry(preflight_check=checker, quiet_env_var="CUSTOM_VAR")
        reg.register(_FakePlugin())
        reg.run_target(tmp_path, "fake")
        assert observed == {"custom": "1", "default": None}

    def test_web_definitions(self):
        reg = SuiteRegistry()
        reg.register(_FakePlugin())
        defs = reg.to_web_definitions()
        assert defs[0]["id"] == "fake"
        assert defs[0]["prefix"] == "fake"


class TestFailedBookkeeping:
    def _report(self, output_dir, target, verdict, use_summary=False):
        suite, _, case = target.partition(".")
        run_root = output_dir / "runs" / suite / case
        run_root.mkdir(parents=True)
        if use_summary:
            import json
            (run_root / "summary.json").write_text(
                json.dumps({"status": verdict}), encoding="utf-8")
        else:
            (run_root / "report.txt").write_text(
                "用例: %s\n结论: %s\n" % (target, verdict), encoding="utf-8")

    def test_case_status_summary_and_report(self, tmp_path):
        self._report(tmp_path, "s.a", "PASS", use_summary=True)
        self._report(tmp_path, "s.b", "FAIL")
        assert case_status(tmp_path, "s.a") == "PASS"
        assert case_status(tmp_path, "s.b") == "FAIL"
        assert case_status(tmp_path, "s.missing") is None
        assert case_status(tmp_path, "no_case") is None

    def test_failed_targets_semantics(self, tmp_path):
        self._report(tmp_path, "s.a", "PASS", use_summary=True)
        self._report(tmp_path, "s.b", "FAIL")
        # real verdicts win
        assert failed_targets(["s.a", "s.b"], 1, tmp_path) == ["s.b"]
        # no fresh reports (preflight abort) keep all targets when run failed
        assert failed_targets(["s.x"], 3, tmp_path) == ["s.x"]
        # success + all pass -> nothing recorded
        assert failed_targets(["s.a"], 0, tmp_path) == []

    def test_last_failed_roundtrip_and_registry_filter(self, tmp_path):
        write_last_failed(tmp_path, ["fake.a", "ghost.x", "fake.b"])
        assert read_last_failed(tmp_path) == ["fake.a", "ghost.x", "fake.b"]

        reg = SuiteRegistry()
        reg.register(_FakePlugin())
        # registry filters unselectable targets
        assert read_last_failed(tmp_path, registry=reg) == ["fake.a", "fake.b"]

    def test_rerun_failed(self, tmp_path):
        write_last_failed(tmp_path, ["s.a", "s.b"])
        # s.a gets a PASS report mid-rerun; s.b stays without report
        def run_target(target):
            if target == "s.a":
                self._report(tmp_path, "s.a", "PASS", use_summary=True)
                return 0
            return 1

        targets, failures, ok = rerun_failed(tmp_path, None, run_target)
        assert targets == ["s.a", "s.b"]
        assert failures == ["s.b"]
        assert not ok
        # bookkeeping now holds only s.b
        assert read_last_failed(tmp_path) == ["s.b"]


# ------------------------------------------------------------------- psql client


class TestPsqlClient:
    def test_build_command_defaults(self):
        cmd = build_psql_command("/pg", "h", 5432, "u", "db", "SELECT 1")
        assert cmd[0].endswith("bin/psql")
        assert cmd[-2:] == ["-c", "SELECT 1"]
        assert "-t" not in cmd and "-x" not in cmd

    def test_build_command_all_options(self):
        cmd = build_psql_command("/pg", "h", 5432, "u", "db", "Q",
                                 footer=True, output_format="csv",
                                 field_separator=",", tuples_only=True, expanded=True)
        assert "-P" in cmd and "footer=on" in cmd
        assert "format=csv" in cmd
        assert cmd[cmd.index("-F") + 1] == ","
        assert "-t" in cmd and "-x" in cmd

    def test_parse_standard_table(self):
        output = (
            " node_name | group_role | state \n"
            "-----------+------------+-------\n"
            " pg_220    | write      | active\n"
            " pg_240    | read       | active\n"
            "(2 rows)\n"
        )
        rows = parse_psql_table(output)
        assert rows == [
            {"node_name": "pg_220", "group_role": "write", "state": "active"},
            {"node_name": "pg_240", "group_role": "read", "state": "active"},
        ]

    def test_parse_expanded_output(self):
        output = (
            "-[ RECORD 1 ]----\n"
            "node_name  | pg_220\n"
            "state      | active\n"
            "-[ RECORD 2 ]----\n"
            "node_name  | pg_240\n"
            "state      | parted\n"
        )
        rows = parse_psql_table(output)
        assert rows == [
            {"node_name": "pg_220", "state": "active"},
            {"node_name": "pg_240", "state": "parted"},
        ]

    def test_parse_non_table_returns_empty(self):
        assert parse_psql_table("SET NODE\n") == []
        assert parse_psql_table("") == []
        assert parse_psql_table(None) == []

    def test_assert_table_rows_pass_and_fail(self):
        output = (
            " node_name | state  | role\n"
            "-----------+--------+------\n"
            " pg_220    | active | write\n"
            " pg_240    | parted | read\n"
            "(2 rows)\n"
        )
        passed, summary, details = assert_table_rows(output, {
            "pg_220": {"state": "active", "role": "write"},
            "pg_240": {"state": "active"},          # mismatch
            "pg_999": {"state": "active"},          # missing
        })
        assert not passed
        statuses = {d["key"]: d["status"] for d in details}
        assert statuses == {"pg_220": "PASS", "pg_240": "MISMATCH", "pg_999": "MISSING"}
