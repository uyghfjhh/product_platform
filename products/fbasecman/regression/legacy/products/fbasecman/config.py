"""fbasecman configuration inspection and editing."""

import re


def extract_config_lines(conf_text, prefixes):
    if not conf_text:
        return ""
    keep = []
    for raw_line in conf_text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if any(stripped.startswith(prefix) for prefix in prefixes):
            keep.append(line)
    return "\n".join(keep)


def set_or_append_config_line(rendered, key, line):
    pattern = re.compile(r"(?m)^[ \t]*%s\b.*$" % re.escape(key))
    if pattern.search(rendered):
        return pattern.sub(line, rendered, count=1)
    if not rendered.endswith("\n"):
        rendered += "\n"
    return rendered + line + "\n"


def set_config_block_line(rendered, block_kind, block_name, key, line):
    block_pattern = re.compile(
        r'(%s\s+"%s"\s*\{)(.*?)(^\})'
        % (re.escape(block_kind), re.escape(block_name)),
        re.MULTILINE | re.DOTALL,
    )
    match = block_pattern.search(rendered)
    if not match:
        return set_or_append_config_line(rendered, key, line)
    block_head, block_body, block_tail = match.group(1), match.group(2), match.group(3)
    key_pattern = re.compile(r"(?m)^[ \t]*%s\b.*$" % re.escape(key))
    if key_pattern.search(block_body):
        new_body = key_pattern.sub(line, block_body, count=1)
    else:
        if block_body and not block_body.endswith("\n"):
            block_body += "\n"
        new_body = block_body + "    %s\n" % line.strip()
    return rendered[:match.start()] + block_head + new_body + block_tail + rendered[match.end():]


def remove_config_block_line(rendered, block_kind, block_name, key):
    """Remove one configuration key from a named block without touching others."""
    block_pattern = re.compile(
        r'(%s\s+"%s"\s*\{)(.*?)(^\})'
        % (re.escape(block_kind), re.escape(block_name)),
        re.MULTILINE | re.DOTALL,
    )
    match = block_pattern.search(rendered)
    if not match:
        return rendered
    block_head, block_body, block_tail = match.group(1), match.group(2), match.group(3)
    key_pattern = re.compile(r"(?m)^[ \t]*%s\b.*(?:\n|$)" % re.escape(key))
    new_body = key_pattern.sub("", block_body, count=1)
    return rendered[:match.start()] + block_head + new_body + block_tail + rendered[match.end():]


def apply_datasource_runtime(rendered, topology, config):
    database = config["database"]
    ports = database["ports"]
    if topology == "mmr":
        datasource_map = {
            "pg_220": (database["mmr_host"], ports["mmr1"]),
            "pg_230": (database["mmr_host"], ports["mmr2"]),
            "pg_240": (database["mmr_host"], ports["mmr1_standby1"]),
        }
    else:
        datasource_map = {
            "pg_220": (database["mmr_host"], ports["mmr1"]),
            "pg_230": (database["mmr_host"], ports["mmr1_standby1"]),
            "pg_240": (database["mmr_host"], ports["mmr1_standby2"]),
        }
    for datasource_name, (host, port) in datasource_map.items():
        rendered = set_config_block_line(
            rendered, "datasources", datasource_name, "host", 'host "%s"' % host
        )
        rendered = set_config_block_line(
            rendered, "datasources", datasource_name, "port", "port %s" % port
        )
    return rendered
