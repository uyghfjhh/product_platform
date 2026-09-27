"""Validated installation and recording of reload configuration."""

import os
import shutil
from pathlib import Path


class ReloadConfigError(RuntimeError):
    pass


def _config_key_and_value(line):
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None, None
    parts = stripped.split(None, 1)
    return parts[0], parts[1].strip() if len(parts) == 2 else ""


def config_lines_by_keys(conf_text, keys):
    wanted = set(keys)
    matched = []
    for line in conf_text.splitlines():
        key, _ = _config_key_and_value(line)
        if key in wanted:
            matched.append(line)
    return matched


def has_config_value(conf_text, key, value):
    expected = str(value).strip()
    for line in conf_text.splitlines():
        actual_key, actual_value = _config_key_and_value(line)
        if actual_key == key and actual_value == expected:
            return True
    return False


def record_config_transition(runtime, title_start, title_reload, start_conf,
                             start_conf_text, reload_conf, reload_conf_text, items):
    start_lines = []
    reload_lines = []
    for item in items:
        if len(item) == 2:
            label, value = item
            start_lines.append("%s=%s" % (label, value))
            reload_lines.append("%s=%s" % (label, value))
        else:
            label, before_value, after_value = item
            start_lines.append("%s=%s" % (label, before_value))
            reload_lines.append("%s=%s -> %s" % (label, before_value, after_value))
    keys = [item[0] for item in items]
    runtime.record_step(
        title_start,
        output="\n".join(
            start_lines
            + ["conf : %s" % start_conf]
            + config_lines_by_keys(start_conf_text, keys)
        ),
    )
    runtime.record_step(
        title_reload,
        output="\n".join(
            reload_lines
            + ["copy  : %s -> %s" % (reload_conf, start_conf)]
            + config_lines_by_keys(reload_conf_text, keys)
        ),
    )


def install_reload_config(source_conf, target_conf, required_items):
    """Validate then atomically install a reload configuration file."""
    source = Path(source_conf)
    target = Path(target_conf)
    source_text = source.read_text(encoding="utf-8", errors="replace")
    missing = [
        "%s=%s" % (key, value)
        for key, value in required_items
        if not has_config_value(source_text, key, value)
    ]
    if missing:
        raise ReloadConfigError(
            "reload source conf check failed, missing config items: %s"
            % ", ".join(missing)
        )

    temporary = target.with_name(target.name + ".reload.tmp")
    shutil.copyfile(str(source), str(temporary))
    os.replace(str(temporary), str(target))
    live_text = target.read_text(encoding="utf-8", errors="replace")
    missing = [
        "%s=%s" % (key, value)
        for key, value in required_items
        if not has_config_value(live_text, key, value)
    ]
    if missing:
        raise ReloadConfigError(
            "reload live conf write check failed, missing config items: %s"
            % ", ".join(missing)
        )
    return live_text
