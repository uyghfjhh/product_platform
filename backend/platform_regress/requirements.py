"""Requirement gate: declared case dependencies evaluated to ``Blocked``.

The platform registers the shared evaluators in the legacy gate's exact
evaluation order — the first blocker wins.  Products register evaluators
for product-specific keys via :func:`register_requirement`, optionally with
``before=`` to keep their position in the order.  Every evaluator receives
``(context, requirements)``, no-ops when its key is absent, and raises
``Blocked`` with the legacy reason text.
"""

import shutil

from platform_regress.engine import Blocked

_EVALUATORS = []  # ordered list of (key, evaluator)


def register_requirement(key, evaluator=None, before=None):
    """Register ``evaluator(context, requirements)`` for ``key``.

    With ``before`` the evaluator is inserted ahead of the named key so
    products can keep their dependency check in the original gate order.
    """

    def register(fn):
        entry = (key, fn)
        if before is not None:
            for index, (existing, _) in enumerate(_EVALUATORS):
                if existing == before:
                    _EVALUATORS.insert(index, entry)
                    return fn
            raise KeyError("unknown requirement key for 'before': %s" % before)
        _EVALUATORS.append(entry)
        return fn

    return register(evaluator) if evaluator is not None else register


def evaluate_requirements(context, requirements):
    """Run every registered evaluator against the declared requirements."""
    requirements = requirements or {}
    for _key, evaluator in list(_EVALUATORS):
        evaluator(context, requirements)


def _scalar(context, node, sql):
    rows = context.sql(node, sql).rows
    return str(rows[0][0]) if rows and rows[0] is not None else None


@register_requirement("clusters")
def _require_clusters(context, requirements):
    cluster = context.environment.get("cluster")
    allowed = requirements.get("clusters") or []
    if allowed and cluster not in allowed:
        raise Blocked("用例仅支持 cluster=%s；当前为 %s" % (",".join(allowed), cluster))


@register_requirement("commands")
def _require_commands(context, requirements):
    for command in requirements.get("commands") or []:
        if not isinstance(command, str) or not command:
            raise ValueError("requirements.commands 必须包含非空命令名: %s" % command)
        if not shutil.which(command):
            raise Blocked("缺少命令: %s；请安装 util-linux（提供 %s）" % (command, command))


@register_requirement("plugins")
def _require_plugins(context, requirements):
    cluster_name = context.environment.get("cluster_name") or \
        context.environment.get("cluster")
    missing = sorted(set(requirements.get("plugins") or []) -
                     set(context.environment.get("plugins") or []))
    if missing:
        raise Blocked("cluster %s 未启用插件: %s" % (cluster_name, ",".join(missing)))


@register_requirement("groups")
def _require_groups(context, requirements):
    cluster_name = context.environment.get("cluster_name") or \
        context.environment.get("cluster")
    for group in requirements.get("groups") or []:
        if group not in (context.environment.get("node_groups") or {}):
            raise Blocked("cluster %s 缺少关系组: %s" % (cluster_name, group))


@register_requirement("nodes")
def _require_nodes(context, requirements):
    for selector in requirements.get("nodes") or []:
        context.resolve_node(selector)


@register_requirement("node")
def _require_node(context, requirements):
    # The primary-selector probe runs unconditionally in the legacy gate:
    # resolving "primary" validates the declared topology even when the case
    # declares no explicit node requirement.
    context.resolve_node(requirements.get("node") or "primary")


@register_requirement("system_time_control")
def _require_system_time_control(context, requirements):
    if not requirements.get("system_time_control"):
        return
    result = context.command(["sudo", "-n", "true"], timeout_seconds=30,
                             merge_stderr=True)
    if result.returncode != 0:
        raise Blocked("密码周期用例需要免交互 sudo 调整并恢复系统时间；"
                      "请安装 sudo 并为当前测试用户配置 sudo -n true")


@register_requirement("roles")
def _require_roles(context, requirements):
    roles = requirements.get("roles") or []
    if not roles:
        return
    node = context.resolve_node(requirements.get("node") or "primary")
    for role in roles:
        try:
            exists = _scalar(
                context, node,
                "SELECT count(*) FROM pg_roles WHERE rolname = '%s'"
                % str(role).replace("'", "''"))
        except Blocked:
            raise
        except Exception as exc:
            raise Blocked("无法检查角色 %s: %s" % (role, exc))
        if exists != "1":
            raise Blocked("缺少数据库角色: %s" % role)


@register_requirement("extensions")
def _require_extensions(context, requirements):
    extensions = requirements.get("extensions") or []
    if not extensions:
        return
    node = context.resolve_node(requirements.get("node") or "primary")
    for extension in extensions:
        try:
            available = _scalar(
                context, node,
                "SELECT count(*) FROM pg_available_extensions WHERE name = '%s'"
                % str(extension).replace("'", "''"))
        except Blocked:
            raise
        except Exception as exc:
            raise Blocked("无法检查扩展 %s: %s" % (extension, exc))
        if available != "1":
            raise Blocked("缺少可用 PostgreSQL 扩展: %s" % extension)
