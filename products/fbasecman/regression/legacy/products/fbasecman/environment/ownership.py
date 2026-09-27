"""Environment ownership manifests and deterministic cleanup plans."""

import hashlib
import shlex
from pathlib import PurePosixPath
from textwrap import dedent

from .topology import topology_nodes


MARKER_NAME = ".fbasecman-test-environment"
class EnvironmentOwnershipError(RuntimeError):
    pass


class CleanupNode(object):
    def __init__(self, topology, name, host, user, postgres_dir, data_root):
        self.topology = topology
        self.name = name
        self.host = host
        self.user = user
        self.postgres_dir = postgres_dir
        self.data_root = data_root
        self.pgdata = "%s/%s" % (data_root.rstrip("/"), name)


class OwnershipScope(object):
    def __init__(self, profile, host, user, data_root, nodes):
        self.profile = profile
        self.host = host
        self.user = user
        self.data_root = data_root
        self.nodes = tuple(nodes)
        material = "\n".join(
            ["version=1", "profile=%s" % profile, "host=%s" % host,
             "root=%s" % data_root] + ["node=%s" % node.pgdata for node in self.nodes]
        )
        self.token = hashlib.sha256(material.encode("utf-8")).hexdigest()
        self.marker = "%s/%s" % (data_root.rstrip("/"), MARKER_NAME)


class CleanupPlan(object):
    def __init__(self, profile, scopes):
        self.profile = profile
        self.scopes = tuple(scopes)

    @property
    def nodes(self):
        return tuple(node for scope in self.scopes for node in scope.nodes)

    def render(self):
        lines = ["Environment cleanup plan:", "  profile: %s" % self.profile]
        for scope in self.scopes:
            lines.append("  scope: %s@%s root=%s marker=%s" % (
                scope.user, scope.host, scope.data_root, scope.marker,
            ))
            for node in scope.nodes:
                lines.append("    stop/remove %-18s %s" % (node.name, node.pgdata))
        return "\n".join(lines)


def _safe_root(value, location):
    path = PurePosixPath(str(value))
    if not path.is_absolute():
        raise EnvironmentOwnershipError("%s must be an absolute path: %s" % (location, value))
    normalized = str(path)
    if normalized in ("/", "/home", "/usr", "/usr/local", "/var", "/tmp"):
        raise EnvironmentOwnershipError("%s is too broad for managed PGDATA: %s" % (location, value))
    if ".." in path.parts:
        raise EnvironmentOwnershipError("%s must not contain '..': %s" % (location, value))
    return normalized


def build_cleanup_plan(env):
    database = env.config["database"]
    specs = [
        ("mmr", database["mmr_host"], database["mmr_pg_user"],
         database["mmr_postgres_dir"], database.get("mmr_data_root", database["mmr_postgres_dir"])),
    ]
    if "rep_host" in database and "rep_postgres_dir" in database and database["rep_host"]:
        specs.append(
            ("rep", database["rep_host"], database.get("rep_pg_user", database["mmr_pg_user"]),
             database["rep_postgres_dir"], database.get("rep_data_root", database["rep_postgres_dir"]))
        )
    grouped = {}
    for topology, host, user, postgres_dir, raw_root in specs:
        data_root = _safe_root(raw_root, "database.%s_data_root" % topology)
        key = (host, user, data_root)
        grouped.setdefault(key, [])
        if topology == "rep":
            if "ports" in database and "rep_standbys" in database["ports"]:
                rep_count = len(database["ports"]["rep_standbys"])
                names = ["test_rep"] + [f"test_rep_s{i}" for i in range(1, rep_count + 1)]
            else:
                names = ("test_rep", "test_rep_s1", "test_rep_s2")
        else:
            if "ports" in database:
                configured = topology_nodes(database)
                names = [name for name, _ in configured["mmr1"] + configured["mmr2"]]
            else:
                names = ("test_mmr1", "test_mmr1_s1", "test_mmr1_s2", "test_mmr1_s3",
                         "test_mmr2", "test_mmr2_s1", "test_mmr2_s2", "test_mmr2_s3")
        for name in names:
            grouped[key].append(CleanupNode(
                topology, name, host, user, str(postgres_dir), data_root,
            ))
    scopes = [
        OwnershipScope(env.profile, host, user, root, nodes)
        for (host, user, root), nodes in sorted(grouped.items())
    ]
    return CleanupPlan(env.profile, scopes)


def _authorization_script(scope, adopt_existing):
    node_checks = "\n".join(
        "if [ -e \"$root/%s\" ]; then existing=1; fi" % node.name
        for node in scope.nodes
    )
    return dedent(
        """
        set -eu
        root={root}
        expected="{token}"
        mkdir -p "$root"
        physical_root=$(cd "$root" && pwd -P)
        [ "$physical_root" = "$root" ] || {{
            echo "managed PGDATA root must not resolve through a symlink: $root" >&2
            exit 76
        }}
        marker="$root/{marker_name}"
        if [ -f "$marker" ]; then
            actual=$(sed -n 's/^token=//p' "$marker")
            [ "$actual" = "$expected" ] || {{
                echo "ownership marker mismatch: $marker" >&2
                exit 73
            }}
            exit 0
        fi
        existing=0
        {node_checks}
        if [ "$existing" -eq 1 ] && [ "{adopt}" != "yes" ]; then
            echo "existing PGDATA has no ownership marker: $root" >&2
            echo "inspect the cleanup plan, then rerun env setup --adopt-existing" >&2
            exit 74
        fi
        temporary="$marker.tmp.$$"
        printf 'version=1\nprofile=%s\ntoken=%s\n' "{profile}" "$expected" > "$temporary"
        mv "$temporary" "$marker"
        """
    ).format(
        root=shlex.quote(scope.data_root), marker_name=MARKER_NAME, token=scope.token,
        node_checks=node_checks, adopt="yes" if adopt_existing else "no",
        profile=scope.profile,
    ).strip()


def authorize_environment(env, runner, adopt_existing=False):
    plan = build_cleanup_plan(env)
    for index, scope in enumerate(plan.scopes, 1):
        runner.run_remote(
            scope.user, scope.host, _authorization_script(scope, adopt_existing),
            log_name="00_ownership_%02d.log" % index,
        )
    return plan


def cleanup_script(scope):
    actions = []
    for node in scope.nodes:
        actions.append("clean_pgdata %s %s" % (
            shlex.quote(node.name),
            shlex.quote("%s/bin/pg_ctl" % node.postgres_dir.rstrip("/")),
        ))
    return dedent(
        """
        set -eu
        root={root}
        physical_root=$(cd "$root" && pwd -P)
        [ "$physical_root" = "$root" ] || {{
            echo "managed PGDATA root must not resolve through a symlink: $root" >&2
            exit 76
        }}
        marker="$root/{marker_name}"
        expected="{token}"
        actual=$(sed -n 's/^token=//p' "$marker" 2>/dev/null || true)
        [ "$actual" = "$expected" ] || {{
            echo "refusing cleanup: ownership marker missing or mismatched: $marker" >&2
            echo "inspect with './stable.sh env clean --dry-run', then explicitly run './stable.sh env clean --adopt-existing'" >&2
            exit 73
        }}

        clean_pgdata() {{
            name="$1"
            pg_ctl="$2"
            pgdata="$root/$name"
            if [ -d "$pgdata" ]; then
                echo "[clean] stop/remove $name: $pgdata"
                "$pg_ctl" -D "$pgdata" -l "$pgdata/logfile" -m immediate stop || true
                rm -rf -- "$pgdata"
            else
                echo "[clean] skip missing $name: $pgdata"
            fi
        }}

        {actions}
        rm -f -- "$marker"
        """
    ).format(
        marker_name=MARKER_NAME, token=scope.token,
        root=shlex.quote(scope.data_root),
        actions="\n".join(actions),
    ).strip()
