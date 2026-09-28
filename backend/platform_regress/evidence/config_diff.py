"""Generic configuration diffing and semantic block change assertions.

Provides helpers to parse block configurations (e.g. ``block_type "name" { ... }``),
derive mutation white-lists from commands, and perform semantic diffs that ignore
formatting or whitespace shifts while strictly checking expected fields.
"""

from __future__ import annotations

import re


def strip_inline_comment(value: str) -> str:
    """Strip trailing ``#`` comments that are not inside quotes or escaped."""
    quoted = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quoted:
            escaped = True
            continue
        if char == '"':
            quoted = not quoted
        elif char == "#" and not quoted:
            return value[:index].rstrip()
    return value.strip()


def parse_semantic_objects(text: str, pattern: re.Pattern | None = None) -> tuple[dict, list]:
    """Parse block configuration text into an ordered mapping of semantic objects.

    Returns:
        (objects_dict, order_list) where objects_dict maps (kind, name) -> {field_key: field_val}.
    """
    if pattern is None:
        pattern = re.compile(r'(?ms)^\s*(group|datasources)\s+"([^"]+)"\s*\{(.*?)\}')

    objects = {}
    order = []
    for match in pattern.finditer(text):
        kind, name, body = match.groups()
        identity = (kind, name)
        if identity in objects:
            raise ValueError("duplicate object %s %s" % identity)
        fields = {}
        token_text = " ".join(
            strip_inline_comment(raw).strip()
            for raw in body.splitlines()
            if strip_inline_comment(raw).strip()
        )
        field_pattern = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s+("(?:\\.|[^"\\])*"|[^\s]+)')
        for field_match in field_pattern.finditer(token_text):
            key, value = field_match.groups()
            if key in fields:
                raise ValueError("duplicate field %s.%s" % (name, key))
            fields[key] = value
        objects[identity] = fields
        order.append(identity)
    return objects, order


def command_mutation_scope(sql: str, before_objects: dict) -> dict:
    """Derive the dictionary of allowed field mutations from a command string.

    Returns mapping of (kind, name, field_name) -> expected_new_value_or_None.
    """
    upper = sql.upper()
    allowed = {}
    if " WRITE " in upper or " PROMOTED " in upper:
        groups = re.search(r'(?i)\bIN\s+GROUPS\s*\(([^)]*)\)', sql)
        group = re.search(r'(?i)\bIN\s+GROUP\s+([^\s;]+)', sql)
        if groups:
            names = [item.strip() for item in groups.group(1).split(",")]
        elif group:
            names = [group.group(1)]
        else:
            names = [name for kind, name in before_objects if kind == "group"]
        fields = {"promoted_cluster"}
        if " WRITE " in upper:
            fields.add("write_cluster")
        target = re.search(r'(?i)^SET\s+(?:NODE|CLUSTER)\s+(?:WRITE|PROMOTED)\s+([^\s]+)', sql)
        datasource = target.group(1).strip(";,()") if target else ""
        cluster = datasource
        if re.match(r'(?i)^SET\s+NODE\s+', sql):
            cluster = next(
                (v.get("cluster_name", "").strip('"')
                 for (k, n), v in before_objects.items()
                 if k == "datasources" and n == datasource),
                datasource,
            )
        for name in names:
            for field in fields:
                if field == "promoted_cluster" and " WRITE " in upper:
                    prior = before_objects.get(("group", name), {}).get("write_cluster")
                    allowed[("group", name, field)] = prior
                else:
                    allowed[("group", name, field)] = '"%s"' % cluster
    elif upper.startswith("SET CLUSTER "):
        match = re.search(r'(?i)^SET\s+CLUSTER\s+(?:ACTIVE|PARTED)\s+([^\s;]+)', sql)
        cluster = match.group(1) if match else ""
        value = '"active"' if " ACTIVE " in upper else '"parted"'
        allowed = {
            (kind, name, "status"): value for (kind, name), fields in before_objects.items()
            if kind == "datasources" and fields.get("cluster_name", "").strip('"') == cluster
        }
    else:
        field = "weight" if " WEIGHT " in upper else "status"
        targets = set(re.findall(r'\b[A-Za-z_][A-Za-z0-9_]*\b', sql))
        assignments = dict(re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\d+)', sql))
        allowed = {
            (kind, name, field): assignments.get(name) for kind, name in before_objects
            if kind == "datasources" and name in targets
        }
        for (kind, name), fields in before_objects.items():
            endpoint = "%s:%s" % (fields.get("host", "").strip('"'), fields.get("port", ""))
            if kind == "datasources" and endpoint in sql:
                value = assignments.get(name)
                allowed[(kind, name, field)] = value
    return allowed


def semantic_config_diff(before: str, after: str, sql: str) -> tuple[bool, str]:
    """Compare before and after config text, verifying only mutations within the scope of sql occurred."""
    try:
        old, old_order = parse_semantic_objects(before)
        new, new_order = parse_semantic_objects(after)
    except ValueError as exc:
        return False, "配置结构错误: %s" % exc
    lines = []
    valid = old_order == new_order and set(old) == set(new)
    if old_order != new_order:
        lines.append("对象顺序发生变化")
    allowed = command_mutation_scope(sql, old)
    for identity in sorted(set(old) | set(new)):
        old_fields, new_fields = old.get(identity, {}), new.get(identity, {})
        for field in sorted(set(old_fields) | set(new_fields)):
            old_value, new_value = old_fields.get(field), new_fields.get(field)
            if old_value == new_value:
                continue
            change = (identity[0], identity[1], field)
            expected = change in allowed and (allowed[change] is None or allowed[change] == new_value)
            lines.append("%s %s.%s: %s -> %s%s" % (
                identity[0], identity[1], field,
                old_value, new_value, "" if expected else " [非预期]",
            ))
            valid = valid and expected
    return valid, "\n".join(lines) or "<no semantic changes>"
