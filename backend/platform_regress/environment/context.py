from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict, cast

from ..contracts import Blocked

if TYPE_CHECKING:
    from ..engine import CaseContext


class NodeEndpoint(TypedDict):
    host: str
    port: int
    data_dir: NotRequired[str]
    role: NotRequired[str]


class CaseEnvironment(TypedDict, total=False):
    """Common fields; products may add their own explicitly documented fields."""

    nodes: dict[str, NodeEndpoint]
    node_groups: dict[str, Any]
    node_aliases: dict[str, str]
    users: dict[str, dict[str, str]]
    user: str
    product_id: str
    environment_id: str
    cluster: str
    history_root: str | Path


def validate_environment(value: dict[str, Any] | None) -> CaseEnvironment:
    if value is None:
        return cast(CaseEnvironment, {})
    if not isinstance(value, dict):
        raise ValueError("case environment must be a mapping")
    for field in ("nodes", "node_groups", "node_aliases", "users"):
        if (
            field in value
            and value[field] is not None
            and not isinstance(value[field], dict)
        ):
            raise ValueError(f"environment.{field} must be a mapping")
    return cast(CaseEnvironment, value)


def resolve_selector(
    environment: dict[str, Any], selector: str | None = "primary"
) -> str:
    """Resolve a product node selector to an injected endpoint key.

    Mirrors the legacy topology contract exactly: a direct node key wins;
    ``primary``/``writable`` prefer the streaming primary, then the first MMR
    member's primary in topology order, then the logical publisher, then the
    first declared node; ``standby`` resolves only from the streaming group;
    ``subscriber``/``logical_subscriber`` take the first logical subscriber;
    ``mmr:<member>[:primary|standby]`` resolves through the MMR member map.
    Products describe their groups via ``environment["node_groups"]``.
    """
    nodes = environment.get("nodes") or {}
    selector = str(selector or "primary")
    if selector in nodes:
        return selector
    groups = environment.get("node_groups") or {}
    streaming = groups.get("streaming") or {}
    logical = groups.get("logical") or {}
    members = (groups.get("mmr") or {}).get("members") or {}
    if selector in ("primary", "writable"):
        if streaming.get("primary") and streaming["primary"] in nodes:
            return streaming["primary"]
        if members:
            primary = next(iter(members.values()))["primary"]
            if primary in nodes:
                return primary
        if logical.get("publisher") and logical["publisher"] in nodes:
            return logical["publisher"]
        if nodes:
            return next(iter(nodes))
        raise Blocked("无法解析测试节点: %s" % selector)
    if selector == "standby" and streaming.get("standbys"):
        candidate = streaming["standbys"][0]
        if candidate in nodes:
            return candidate
    if selector == "publisher" and logical.get("publisher") in nodes:
        return logical["publisher"]
    if selector in ("subscriber", "logical_subscriber"):
        subscribers = logical.get("subscribers") or {}
        for candidate in subscribers:
            if candidate in nodes:
                return candidate
    parts = selector.split(":")
    if selector.startswith("mmr:"):
        relation = members.get(parts[1]) if len(parts) > 1 else None
        if relation:
            if len(parts) == 2 or parts[2] == "primary":
                candidate = relation["primary"]
                if candidate in nodes:
                    return candidate
            elif parts[2] == "standby" and relation.get("standbys"):
                candidate = relation["standbys"][0]
                if candidate in nodes:
                    return candidate
    # Compatibility: ``member:role`` resolves to an explicit ``member_role``
    # endpoint key when products inject flat names instead of groups.
    if len(parts) >= 2:
        key = parts[-2] if parts[-1] == "primary" else "%s_%s" % (parts[-2], parts[-1])
        if key in nodes:
            return key
    # Product convenience aliases (e.g. bare MMR member names) resolve last so
    # they can never shadow a real node key or a group-based selector.
    aliases = environment.get("node_aliases") or {}
    if aliases.get(selector) in nodes:
        return aliases[selector]
    raise Blocked("无法解析测试节点: %s" % selector)


class EnvironmentResolver:
    """Own environment operations for one case execution."""

    def __init__(self, context: CaseContext):
        self.context = context

    def expand(self, value: Any) -> Any:
        """Expand per-run placeholders in declarative fixtures and steps.

        ``{run_id}`` becomes this execution's unique id; ``{env.<key>}`` reads
        ``environment[key]``; ``{node.<name>.<field>}`` reads field ``<field>``
        of node ``<name>`` from ``environment["nodes"]`` (``host``/``port``/
        ``data_dir``...).  Strings holding a declared listener port are
        rewritten to the reserved port recorded in
        ``values["isolated_mmr_port_mapping"]`` (``port=<n>``, ``-p <n>`` and ``:<n>``
        forms), so product cases can keep their exported commands verbatim.
        """
        if isinstance(value, str):
            expanded = value.replace("{run_id}", str(self.context.values["run_id"]))
            if "{env." in expanded or "{node." in expanded:
                env = self.context.environment or {}
                nodes = env.get("nodes") or {}

                def _env_value(match):
                    key = match.group(1)
                    if "." in key:  # dotted path into nested dicts
                        current = env
                        for part in key.split("."):
                            if not isinstance(current, dict) or part not in current:
                                raise Blocked(f"用例环境缺少键: {key}")
                            current = current[part]
                        return str(current)
                    if key not in env or env[key] in (None, ""):
                        raise Blocked(f"用例环境缺少键: {key}")
                    return str(env[key])

                def _node_value(match):
                    name, field = match.group(1), match.group(2)
                    node = nodes.get(name) or (env.get("cluster_nodes") or {}).get(name)
                    if not isinstance(node, dict):
                        resolved = (env.get("node_aliases") or {}).get(name)
                        if resolved is None:
                            resolved = resolve_selector(env, name)
                        node = nodes.get(resolved) or (
                            env.get("cluster_nodes") or {}
                        ).get(resolved)
                    if not isinstance(node, dict) or node.get(field) in (None, ""):
                        raise Blocked(f"用例环境缺少节点字段: {name}.{field}")
                    return str(node[field])

                expanded = self.context._NODE_PLACEHOLDER.sub(_node_value, expanded)
                expanded = self.context._ENV_PLACEHOLDER.sub(_env_value, expanded)
            ports = self.context.values.get("isolated_mmr_port_mapping") or {}
            if expanded in ports:
                return ports[expanded]
            for declared, allocated in ports.items():
                escaped = re.escape(str(declared))
                expanded = re.sub(
                    r"(port\s*=\s*)%s\b" % escaped, r"\g<1>%s" % allocated, expanded
                )
                expanded = re.sub(
                    r"(\B-p\s+)%s\b" % escaped, r"\g<1>%s" % allocated, expanded
                )
                expanded = re.sub(
                    r"(:)%s\b" % escaped, r"\g<1>%s" % allocated, expanded
                )
            return expanded
        if isinstance(value, list):
            return [self.context.expand(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.context.expand(item) for item in value)
        if isinstance(value, dict):
            return {key: self.context.expand(item) for key, item in value.items()}
        return value

    def resolve_node(self, selector: str = "primary") -> str:
        """Map a product node selector to an injected endpoint key."""
        return resolve_selector(self.context.environment, selector)

    def node_endpoint(self, selector: str = "primary") -> NodeEndpoint:
        """Return the injected ``{host, port}`` endpoint for a selector."""
        key = self.context.resolve_node(selector)
        endpoint = (self.context.environment.get("nodes") or {}).get(key)
        if not isinstance(endpoint, dict):
            raise Blocked(f"测试节点连接信息无效: {key}")
        host, port = endpoint.get("host"), endpoint.get("port")
        if (
            not isinstance(host, str)
            or not host
            or not isinstance(port, int)
            or isinstance(port, bool)
            or not 1 <= port <= 65535
        ):
            raise Blocked(f"测试节点连接信息无效: {key}")
        return cast(NodeEndpoint, endpoint)
