import re

from framework.errors import ConfigError
from framework.document_coverage import validate_document_coverage
from framework.fixtures import FIXTURES
from framework.steps import validate_step
from suites import SUITES


_GUC_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_PLUGIN_SUITES = {
    "fbase_mac": "mac",
    "fdd_mmr": "mmr",
}
_LONG_TIME_PREFIX = "[LONG-TIME]"


def resolve_target(query, suite_hint=None):
    """Resolve a full target, group prefix, or unique abbreviated case name."""
    suites = SUITES
    if suite_hint in SUITES:
        suites = {suite_hint: SUITES[suite_hint]}

    targets = set()
    case_ids = []
    for suite_id, suite in suites.items():
        targets.add(suite_id)
        targets.update("%s.%s" % (suite_id, group) for group in suite["groups"])
        for case in suite["cases"]:
            targets.add(case["id"])
            case_ids.append(case["id"])

    if query in targets:
        return query

    exact_leaf = [case_id for case_id in case_ids
                  if case_id.rsplit(".", 1)[-1] == query]
    if len(exact_leaf) == 1:
        return exact_leaf[0]

    fuzzy = [case_id for case_id in case_ids
             if query in case_id.rsplit(".", 1)[-1]]
    if len(fuzzy) == 1:
        return fuzzy[0]

    matches = exact_leaf or fuzzy
    if matches:
        raise ConfigError("target 匹配到多个用例: %s（请使用完整名称: %s）" %
                          (query, ", ".join(sorted(matches))))
    raise ConfigError("未知 target: %s" % query)


def suite_for_target(target):
    suite_id = target.split(".", 1)[0]
    try:
        return SUITES[suite_id]
    except KeyError:
        raise ConfigError("未知 target: %s（可选 suite: %s）" %
                          (target, ", ".join(sorted(SUITES))))


def select_cases(target):
    suite = suite_for_target(target)
    selected = [case for case in suite["cases"]
                if case["id"] == target or case["id"].startswith(target + ".")]
    if selected:
        return selected
    valid_prefixes = {suite["id"]}
    valid_prefixes.update("%s.%s" % (suite["id"], group) for group in suite["groups"])
    if target in valid_prefixes:
        return []
    raise ConfigError("未知 target: %s" % target)


def select_cases_for_plugins(plugins, include_all=False):
    """Return cases supported by the cluster plugin set.

    The normal regression path excludes manually selected cases.  ``include_all``
    is the explicit opt-in used by ``run --all``.
    """
    enabled = set(plugins)
    missing = sorted({suite_id for plugin, suite_id in _PLUGIN_SUITES.items()
                      if plugin in enabled and suite_id not in SUITES})
    if missing:
        raise ConfigError("已启用插件对应的 suite 尚未注册: %s" %
                          ", ".join(missing))
    selected = []
    for suite_id in sorted(SUITES):
        suite = SUITES[suite_id]
        required = set(suite.get("required_plugins") or [])
        if required <= enabled:
            selected.extend(case for case in suite["cases"]
                            if include_all or case.get("default_enabled", True))
    return selected


def long_time_cases():
    """Return manually-run cases whose scenario has a deliberately long duration."""
    cases = []
    for suite_id in sorted(SUITES):
        cases.extend(case for case in SUITES[suite_id]["cases"]
                     if case["name"].startswith(_LONG_TIME_PREFIX))
    return cases


def long_time_cases_for_plugins(plugins):
    """Return only long-running cases supported by the cluster plugin set."""
    enabled = set(plugins)
    return [case for suite_id, suite in sorted(SUITES.items())
            if set(suite.get("required_plugins") or []) <= enabled
            for case in suite["cases"]
            if case["name"].startswith(_LONG_TIME_PREFIX)]


def validate_catalog():
    seen = set()
    required = {
        "id", "name", "document", "section", "group", "fixtures",
        "requirements", "prerequisites", "steps", "teardown",
    }
    for suite_id, suite in SUITES.items():
        if suite["id"] != suite_id:
            raise ConfigError("suite 注册名与 id 不一致: %s" % suite_id)
        for plugin in suite.get("required_plugins") or []:
            if not isinstance(plugin, str) or not plugin:
                raise ConfigError("suite %s 包含非法插件要求: %s" %
                                  (suite_id, plugin))
        for case in suite["cases"]:
            missing = required - set(case)
            if missing:
                raise ConfigError("case %s 缺少字段: %s" %
                                  (case.get("id", "<unknown>"), ", ".join(sorted(missing))))
            if case["id"] in seen:
                raise ConfigError("case id 重复: %s" % case["id"])
            if not case["id"].startswith(suite_id + "."):
                raise ConfigError("case id 不属于 suite %s: %s" % (suite_id, case["id"]))
            if case["group"] not in suite["groups"]:
                raise ConfigError("case 分组未注册: %s" % case["group"])
            if not case["steps"]:
                raise ConfigError("case 没有步骤: %s" % case["id"])
            known_issue = case.get("known_issue")
            if known_issue is not None and not re.match(r"^D-[0-9]{3}$", known_issue):
                raise ConfigError("case %s 的 known_issue 格式必须为 D-XXX: %s" %
                                  (case["id"], known_issue))
            if ("default_enabled" in case and
                    not isinstance(case["default_enabled"], bool)):
                raise ConfigError("case %s 的 default_enabled 必须为布尔值" % case["id"])
            if (case["name"].startswith(_LONG_TIME_PREFIX) and
                    case.get("default_enabled", True)):
                raise ConfigError("[LONG-TIME] 用例不得进入默认回归: %s" % case["id"])
            session = case.get("session")
            if session is not None:
                if (not isinstance(session, dict) or
                        not isinstance(session.get("key"), str) or
                        not session["key"] or
                        not isinstance(session.get("fixtures"), list)):
                    raise ConfigError(
                        "case %s 的 session 必须包含非空 key 和 fixtures 列表" %
                        case["id"])
                order = session.get("order")
                if order is not None and (isinstance(order, bool) or
                                          not isinstance(order, int)):
                    raise ConfigError(
                        "case %s 的 session.order 必须为整数" % case["id"])
                for fixture in session["fixtures"]:
                    fixture_name = (fixture if isinstance(fixture, str)
                                    else fixture.get("type"))
                    if fixture_name not in FIXTURES.names:
                        raise ConfigError(
                            "case %s 的 session 使用未知 fixture: %s" %
                            (case["id"], fixture_name))
            for setting in (case.get("requirements") or {}).get("settings", []):
                setting_required = {"name", "purpose"}
                missing_setting = setting_required - set(setting)
                comparisons = {"equals", "contains"} & set(setting)
                if (missing_setting or len(comparisons) != 1 or
                        not _GUC_NAME.match(setting.get("name", ""))):
                    raise ConfigError(
                        "case %s 的 setting 要求必须包含 name、purpose，且只使用 "
                        "equals/contains 之一: %s" % (case["id"], setting))
            for fixture in case["fixtures"]:
                fixture_name = fixture if isinstance(fixture, str) else fixture.get("type")
                if fixture_name not in FIXTURES.names:
                    raise ConfigError("case %s 使用未知 fixture: %s" %
                                      (case["id"], fixture_name))
            for step in case["steps"]:
                validate_step(step)
            seen.add(case["id"])
        validate_document_coverage(suite)


def render_tree():
    lines = []
    for suite_id in sorted(SUITES):
        suite = SUITES[suite_id]
        case_groups = {}
        for case in suite["cases"]:
            case_groups.setdefault(case["group"], []).append(case["id"].split(".")[-1])
        lines.append(suite_id)
        for group in suite["groups"]:
            lines.append("  %s" % group)
            for case_name in case_groups.get(group, []):
                lines.append("    %s" % case_name)
    return "\n".join(lines)


def render_case_details(target):
    cases = select_cases(target)
    if not cases:
        return "target: %s\ncases: 0（该分组尚未迁移用例）" % target
    lines = ["target: %s" % target, "cases: %s" % len(cases)]
    for case in cases:
        lines.extend([
            "",
            "case: %s" % case["id"],
            "名称: %s" % case["name"],
            "来源: %s / %s" % (case["document"], case["section"]),
            "Fixture: %s" % ",".join(
                item if isinstance(item, str) else item.get("type", "<unknown>")
                for item in case["fixtures"]),
            "前置条件:",
        ])
        lines.extend("  - %s" % item for item in case["prerequisites"])
        lines.append("步骤:")
        for index, step in enumerate(case["steps"], 1):
            lines.append("  %s. %s" % (index, step["title"]))
            lines.append("     类型: %s" % step.get("type", "sql"))
            if step.get("node"):
                lines.append("     节点: %s" % step["node"])
            if step.get("user"):
                lines.append("     用户: %s" % step["user"])
            if step.get("sql"):
                lines.append("     SQL: %s" % step["sql"])
            if step.get("argv"):
                lines.append("     命令: %s" % " ".join(str(v) for v in step["argv"]))
            lines.append("     预期: %s" % step["expected"])
    return "\n".join(lines)


def render_long_time_cases():
    cases = long_time_cases()
    lines = ["longtime cases: %s" % len(cases)]
    for case in cases:
        lines.extend([
            "",
            "case: %s" % case["id"],
            "名称: %s" % case["name"],
            "来源: %s / %s" % (case["document"], case["section"]),
            "执行: ./run.sh run %s %s" %
            (case["id"].split(".", 1)[0], case["id"]),
        ])
    return "\n".join(lines)


validate_catalog()
