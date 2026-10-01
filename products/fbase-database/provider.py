"""FBase database test and PostgreSQL runtime observation provider."""

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import psycopg
import yaml
from platform_app.event_contracts import SceneObservationError
from platform_app.postgresql_observations import parse_replication
from platform_app.providers import CommandSpec, Observation
from platform_app.scene import emit_observation, endpoint_id
from platform_app import topology as topology_api


def _load_cases_module():
    """Load this product's platform case module."""
    path = Path(__file__).with_name("cases.py")
    spec = importlib.util.spec_from_file_location("_fbase_platform_cases", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 FBase 平台用例")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migrated_cases():
    """Load metadata from this product's new SDK case module."""
    return _load_cases_module().CASE_METADATA


def native_case(target):
    """Return this target's platform case instance, or None if unregistered."""
    return _load_cases_module().CASES.get(target)


def _node_roles(groups):
    """Node roles like legacy Topology.roles(): streaming -> primary/standby,
    logical -> publisher/logical_subscriber, mmr -> mmr_primary:<member> and
    mmr_standby:<member>."""
    roles = {}
    relation = groups.get("streaming") or {}
    if relation.get("primary"):
        roles[relation["primary"]] = "primary"
    for standby in relation.get("standbys") or []:
        roles[standby] = "standby"
    logical = groups.get("logical") or {}
    if logical.get("publisher"):
        roles[logical["publisher"]] = "publisher"
    for subscriber in logical.get("subscribers") or {}:
        roles[subscriber] = "logical_subscriber"
    mmr = groups.get("mmr") or {}
    for member, relation in (mmr.get("members") or {}).items():
        if relation.get("primary"):
            roles[relation["primary"]] = "mmr_primary:%s" % member
        for standby in relation.get("standbys") or []:
            roles[standby] = "mmr_standby:%s" % member
    return roles


def _cluster_context(settings, cluster, with_nodes=True, environment=None):
    """Read product plugins, binaries, nodes and relation groups from the
    legacy regress config (the same source the old executor used)."""
    context = {"plugins": [], "plugins_detail": {}, "node_groups": {},
               "node_order": [], "cluster_name": cluster}
    path = Path(settings.product_regress_root("fbase-database")) / "regress.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        context["topology_error"] = "无法读取回归集群定义: %s" % path
        return context
    if environment and environment.get("deployment_config"):
        override = Path(environment["deployment_config"]).parent / "regress.override.yaml"
        if override.is_file():
            changes = yaml.safe_load(override.read_text()) or {}
            data.setdefault("postgres", {}).update(changes.get("postgres") or {})
            for name, values in (changes.get("clusters") or {}).items():
                if name in data.get("clusters", {}):
                    data["clusters"][name].setdefault("nodes", {}).update(values.get("nodes") or {})
    clusters = data.get("clusters") or {}
    cluster_config = clusters.get(cluster)
    if cluster_config is None:
        context["topology_error"] = "回归集群 %s 未定义" % cluster
        return context
    plugins = cluster_config.get("plugins") or {}
    context["plugins"] = sorted(plugins.keys())
    context["plugins_detail"] = plugins
    groups = cluster_config.get("groups") or {}
    context["node_groups"] = groups
    context["transport"] = cluster_config.get("transport") or {}
    home = str((data.get("postgres") or {}).get("home") or "")
    home = re.sub(r"\$\{(\w+)\}",
                  lambda match: os.environ.get(match.group(1), ""), home)
    if home:
        context["db_home"] = home
        if Path(home).is_dir():
            context["db_bin_dir"] = str(Path(home) / "bin")
    # Environment-overridable literals consumed by declarative steps:
    # {env.tmp_root} / {env.local_host} / {env.license_file} / {env.contrib_root}
    context["tmp_root"] = str(data.get("tmp_root") or
                              os.environ.get("FBASE_REGRESS_TMP_ROOT", "/tmp/fbase_regress"))
    context["local_host"] = str(data.get("local_host") or
                                os.environ.get("FBASE_LOCAL_HOST", "127.0.0.1"))
    postgres_cfg = data.get("postgres") or {}
    license_file = (postgres_cfg.get("license_file") or
                    os.environ.get("FBASE_LICENSE_FILE"))
    if license_file:
        context["license_file"] = str(license_file)
    contrib_root = (postgres_cfg.get("contrib_root") or
                    os.environ.get("FBASE_CONTRIB_ROOT"))
    if contrib_root:
        context["contrib_root"] = str(contrib_root)
    roles = _node_roles(groups)
    cluster_nodes = {}
    for name, node in (cluster_config.get("nodes") or {}).items():
        endpoint = {"host": node["host"], "port": node["port"],
                    "data_dir": node.get("data_dir") or "",
                    "role": roles.get(name, "standalone")}
        cluster_nodes[name] = endpoint
    # Full node map is always available for {node.<name>.<field>} expansion in
    # declarative steps, even when ``nodes`` carries only selector endpoints.
    context["cluster_nodes"] = cluster_nodes
    if not with_nodes:
        context.pop("topology_error", None)
        return context
    context["nodes"] = cluster_nodes
    context["node_order"] = list(cluster_nodes)
    # Convenience aliases keep hand-written native cases working inside suite
    # runs where the full node map replaces the minimal selector map. They are
    # resolved last in resolve_selector, so real selectors always win.
    from platform_regress.sdk import resolve_selector
    aliases = {}
    candidates = ["primary", "writable", "standby", "publisher",
                  "subscriber", "logical_subscriber"]
    prefixed = {
        member: "mmr:" + member
        for member in (groups.get("mmr") or {}).get("members") or {}
    }
    for candidate in candidates + list(prefixed):
        if candidate in cluster_nodes:
            continue
        try:
            aliases[candidate] = resolve_selector(
                context, prefixed.get(candidate, candidate))
        except Exception:
            continue
    context["node_aliases"] = aliases
    return context


def exported_cases():
    """Discover the full product catalog without importing the old executor."""
    path = Path(__file__).parent / "regression" / "cases.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("FBase 用例目录版本无效")
    suites = payload["suites"]
    return [{
        "suite": case["id"].split(".", 1)[0],
        "suite_title": suites[case["id"].split(".", 1)[0]]["title"],
        "suite_description": suites[case["id"].split(".", 1)[0]]["description"],
        "target": case["id"], "name": case["id"],
        "title": case.get("name", case["id"]),
        "core_id": case.get("core_id", ""),
        "summary": case.get("name", case["id"]),
        "enabled": True, "tags": [case.get("group", "")],
    } for case in payload["cases"]]


ALL_CASE_TARGETS = frozenset(case["target"] for case in exported_cases())


class FbaseProvider:
    """FBase target validation, test command, and database observations."""

    def deployment_templates(self, settings):
        return importlib.import_module("products.fbase-database.deployment.templates").templates(settings)

    def compile_deployment(self, settings, spec, environment_id):
        return importlib.import_module("products.fbase-database.deployment.templates").compile_template(settings, spec, environment_id)

    def deployment_import_files(self, settings, facts, target, environment_id=None):
        return importlib.import_module("products.fbase-database.deployment.templates").import_files(settings, facts, target, environment_id)

    def validate_target(self, settings, target):
        targets = {case["target"] for case in exported_cases()}
        return target == "all" or target in targets or any(item.startswith(target + ".") for item in targets)

    def discover(self, settings):
        native = {entry["target"]: entry for entry in migrated_cases()}
        return [native.get(case["target"], case) for case in exported_cases()]

    def after_command(self, store, settings, environment, task_id, action, success):
        """Capture database replication facts without changing test verdict."""
        if action != "tests.fbase" or not success:
            return
        try:
            for observation in self.observe_runtime(settings, environment):
                emit_observation(
                    store, task_id,
                    observation.details.get("entity_id", endpoint_id(environment)),
                    observation.state, observation.kind, observation.details,
                )
        except Exception as exc:  # noqa: BLE001 - observation is supplemental
            store.tasks.add_event(task_id, "scene.observation.error", SceneObservationError(
                source="product.runtime", message=str(exc),
            ).model_dump())

    def publish_result(self, store, settings, environment, task, terminal, reason):
        if task["action"] != "tests.fbase":
            return terminal, reason
        from platform_app.result_publication import publish_regression_results
        parameters = json.loads(task["parameters"])
        return publish_regression_results(store, settings, environment, task, terminal, reason,
                                          case_targets=ALL_CASE_TARGETS,
                                          profile=parameters.get("profile", "default"))

    def observe_runtime(self, settings, environment):
        connection = psycopg.connect(
            host=environment["host"], port=environment["port"],
            dbname=environment.get("database_name") or "postgres",
            user=environment.get("database_user") or "postgres",
            connect_timeout=4, options="-c statement_timeout=4000",
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT application_name, client_addr::text, state, sync_state "
                    "FROM pg_stat_replication ORDER BY application_name"
                )
                rows = cursor.fetchall()
            text = "application_name\tclient_addr\tstate\tsync_state\n" + "\n".join(
                "\t".join("" if value is None else str(value) for value in row)
                for row in rows
            )
            return self.parse_runtime("replication", text)
        finally:
            connection.close()

    def parse_runtime(self, name, text):
        if name != "replication":
            raise ValueError("FBase 未注册观测: %s" % name)
        return parse_replication(text)

    def observe_database(self, environment):
        connection = psycopg.connect(
            host=environment["host"], port=environment["port"],
            dbname=environment["database_name"], user=environment["database_user"],
            connect_timeout=4, options="-c statement_timeout=4000",
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT version()")
                version = cursor.fetchone()[0]
            return [Observation("sql.version", "ready", {"version": version})]
        finally:
            connection.close()

    def _test_context(self, settings, environment, cluster, with_topology,
                      extended=False):
        """Build the platform CLI context for a single target or suite."""
        if extended:
            # Exported cases need the complete legacy node graph: every
            # instance with data_dir/role, relation groups and plugin details
            # straight from regress.yaml so all selectors resolve like the
            # old executor.
            context = {"user": environment.get("database_user") or "postgres",
                       "users": environment.get("database_users") or {},
                       "cluster": cluster}
            context.update(_cluster_context(settings, cluster, environment=environment))
            return context
        nodes, topology_error = {}, None
        if with_topology:
            try:
                topology = topology_api.configured_topology(settings, environment)
                if cluster == "mmr":
                    primary = {
                        node.get("group"): node for node in topology["nodes"]
                        if node.get("role") == "primary"
                    }
                    nodes = {
                        name: {"host": primary[name]["host"], "port": primary[name]["port"]}
                        for name in ("mmr1", "mmr2", "mmr3") if name in primary
                    }
                else:
                    publishers = {edge["source"] for edge in topology.get("edges", []) if edge.get("kind") == "logical"}
                    primary = [node for node in topology["nodes"] if node.get("role") == "primary"
                               and (not publishers or node["id"] in publishers)]
                    if len(primary) != 1:
                        raise ValueError("等保测试需要唯一的可写主节点")
                    nodes = {"primary": {"host": primary[0]["host"], "port": primary[0]["port"]}}
            except (ValueError, FileNotFoundError) as exc:
                topology_error = str(exc)
        context = {"nodes": nodes, "user": environment.get("database_user") or "postgres",
                   "users": environment.get("database_users") or {},
                   "cluster": cluster}
        context.update(_cluster_context(settings, cluster,
                                               with_nodes=False, environment=environment))
        if topology_error:
            context["topology_error"] = topology_error
        return context

    def command(self, settings, environment, action, target, parameters):
        if action != "tests.fbase":
            raise ValueError("FBase 未注册操作: %s" % action)
        cluster = parameters.get("cluster")
        if cluster not in {"mac", "mmr"}:
            raise ValueError("FBase 测试需要选择 mac 或 mmr 集群")
        if target in ALL_CASE_TARGETS:
            if cluster != target.split(".", 1)[0]:
                raise ValueError("用例目标与所选集群不一致")
            case_impl = native_case(target)
            context = self._test_context(
                settings, environment, cluster,
                with_topology=case_impl is not None,
                extended=type(case_impl).__name__ == "ExportedCommandCase")
            context["state_root"] = str(
                settings.artifact_dir("fbase-database", environment["id"]))
            output = settings.artifact_dir("fbase-database", environment["id"])
            target_args = ["--suite", target] if "." not in target else [target]
            return CommandSpec([
                sys.executable, "-m", "platform_regress.cli",
                "--product-dir", str(Path(__file__).resolve().parent),
                "--output-dir", str(output),
                "--context-json", json.dumps(context, ensure_ascii=False), *target_args,
            ], settings.data_dir)
        if target == "all" or any(item.startswith(target + ".")
                                  for item in ALL_CASE_TARGETS):
            # Suite/prefix and aggregate runs inject the extended node map so
            # every target gets its selectors for free. "all" maps to the
            # cluster prefix since catalog suites are keyed by it.
            suite = cluster if target == "all" else target
            context = self._test_context(settings, environment, cluster,
                                         with_topology=True, extended=True)
            context["state_root"] = str(
                settings.artifact_dir("fbase-database", environment["id"]))
            output = settings.artifact_dir("fbase-database", environment["id"])
            return CommandSpec([
                sys.executable, "-m", "platform_regress.cli",
                "--product-dir", str(Path(__file__).resolve().parent),
                "--output-dir", str(output), "--context-json",
                json.dumps(context, ensure_ascii=False), "--suite", suite,
            ], settings.data_dir)
        raise ValueError("未知 FBase 测试目标: %s" % target)


PROVIDER = FbaseProvider()
