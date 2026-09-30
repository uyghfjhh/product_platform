"""PostgreSQL fixture lifecycle; products inject SQL/evidence transports."""

import re
from dataclasses import dataclass
from typing import Any

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_GUC_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def _env(context, key, default):
    value = context.environment.get(key)
    return value if value not in (None, "") else default


def _identifier(value):
    if not _IDENTIFIER.fullmatch(value or ""):
        raise ValueError(f"invalid PostgreSQL identifier: {value}")
    return '"%s"' % value.replace('"', '""')


def _literal(value):
    return "'%s'" % str(value).replace("'", "''")


@dataclass(frozen=True)
class SqlExecution:
    returncode: int
    rows: tuple[tuple[Any, ...], ...] = ()
    output: str = ""


class PostgresFixtures:
    def __init__(
        self,
        context,
        *,
        checked=None,
        scalar=None,
        execute=None,
        apply_cluster=None,
        check_setting=None,
        defer=None,
        config_error=ValueError,
        operation_error=RuntimeError,
        empty_setting_token="__PLATFORM_EMPTY_SETTING__",
    ):
        self.context = context
        self.empty_setting_token = empty_setting_token
        self.checked = checked or self._checked
        self.scalar = scalar or self._scalar
        self.execute = execute or self._execute
        from .postgresql_lifecycle import PostgresLifecycle

        self.apply_cluster = apply_cluster or (
            lambda ctx, action: PostgresLifecycle(ctx).apply(action)
        )
        self.check_setting = check_setting or self._check_setting
        self.defer = defer or self._defer
        self.config_error, self.operation_error = config_error, operation_error

    @staticmethod
    def _checked(context, node, user, database, sql):
        return context.sql(node, sql, user=user, database=database)

    @staticmethod
    def _scalar(context, node, database, sql, user="postgres"):
        rows = context.sql(node, sql, user=user, database=database).rows
        return str(rows[0][0]) if rows else None

    @staticmethod
    def _execute(context, node, user, database, sql, structured=False):
        try:
            return SqlExecution(
                0, context.sql(node, sql, user=user, database=database).rows
            )
        except Exception as exc:
            from platform_regress.contracts import Blocked, Cancelled

            if isinstance(exc, (Blocked, Cancelled)):
                raise
            return SqlExecution(1, output=str(exc))

    @staticmethod
    def _check_setting(context, node, spec):
        rows = context.sql(node, "SHOW " + spec["name"]).rows
        actual = rows[0][0] if rows else None
        expected = spec["equals"]
        return {
            "name": spec["name"],
            "actual": actual,
            "requirement": "等于 " + str(expected),
            "matched": str(actual).lower() == str(expected).lower(),
        }

    @staticmethod
    def _defer(context, title, callback, priority=0):
        def restore():
            callback()
            context.step("fixture-cleanup", title)

        context.defer_cleanup(restore, priority=priority)

    def roles(self, options):
        context = self.context
        node = context.resolve_node(options.get("node", "primary"))
        database = options.get("database", "postgres")
        setup = options.get("setup", True)
        cleanup_priority = options.get("cleanup_priority", 0)
        for role in options.get("create", []):
            if isinstance(role, str):
                role = {"name": role}
            name = role["name"]
            attributes = role.get("attributes", "LOGIN")
            password = role.get("password")
            login_user = role.get("login", False)

            ownership = {"created": not setup}

            def cleanup(role_name=name, node_name=node, db=database, owned=ownership):
                if not owned["created"]:
                    return
                self.checked(
                    context,
                    node_name,
                    _env(context, "user", "postgres"),
                    db,
                    "DROP ROLE IF EXISTS %s" % _identifier(role_name),
                )

            self.defer(
                context, "删除角色 %s" % name, cleanup, priority=cleanup_priority
            )
            if setup:
                if login_user and not attributes and password is None:
                    sql = "CREATE USER %s" % _identifier(name)
                else:
                    sql = "CREATE ROLE %s %s" % (_identifier(name), attributes)
                if password is not None:
                    sql += " PASSWORD %s" % _literal(password)
                self.checked(
                    context, node, _env(context, "user", "postgres"), database, sql
                )
                ownership["created"] = True

    def database(self, options):
        context = self.context
        name = options.get("name")
        if not name:
            return
        node = context.resolve_node(options.get("node", "primary"))

        ownership = {"created": not options.get("setup", True)}

        def cleanup():
            if not ownership["created"]:
                return
            self.checked(
                context,
                node,
                _env(context, "user", "postgres"),
                "postgres",
                "DROP DATABASE IF EXISTS %s WITH (FORCE)" % _identifier(name),
            )

        self.defer(context, "删除数据库 %s" % name, cleanup)

        if options.get("setup", True):
            sql = "CREATE DATABASE %s" % _identifier(name)
            if options.get("template"):
                sql += " TEMPLATE %s" % _identifier(options["template"])
            if options.get("encoding"):
                sql += " ENCODING %s" % _literal(options["encoding"])
            if options.get("locale"):
                sql += " LOCALE %s" % _literal(options["locale"])
            self.checked(
                context, node, _env(context, "user", "postgres"), "postgres", sql
            )
            ownership["created"] = True

    def table(self, options):
        context = self.context
        """Create or only clean up a disposable table."""
        node = context.resolve_node(options.get("node", "primary"))
        database = options.get("database", "postgres")
        schema = options.get("schema", "public")
        name = options.get("name")
        if not name:
            raise self.config_error("table fixture 缺少 name")
        table_ref = "%s.%s" % (_identifier(schema), _identifier(name))

        ownership = {"created": not options.get("setup", True)}

        def cleanup():
            if not ownership["created"]:
                return
            self.checked(
                context,
                node,
                _env(context, "user", "postgres"),
                database,
                "DROP TABLE IF EXISTS %s" % table_ref,
            )

        self.defer(
            context,
            "删除测试表 %s" % name,
            cleanup,
            priority=options.get("cleanup_priority", 0),
        )

        if options.get("setup", True):
            columns = options.get("columns", "id integer PRIMARY KEY")
            self.checked(
                context,
                node,
                _env(context, "user", "postgres"),
                database,
                "CREATE TABLE %s (%s)" % (table_ref, columns),
            )
            ownership["created"] = True

    def sequence(self, options):
        context = self.context
        """Create or only clean up a disposable PostgreSQL sequence."""
        node = context.resolve_node(options.get("node", "primary"))
        database = options.get("database", "postgres")
        schema = options.get("schema", "public")
        name = options.get("name")
        if not name:
            raise self.config_error("sequence fixture 缺少 name")
        sequence_ref = "%s.%s" % (_identifier(schema), _identifier(name))

        ownership = {"created": not options.get("setup", True)}

        def cleanup():
            if not ownership["created"]:
                return
            self.checked(
                context,
                node,
                _env(context, "user", "postgres"),
                database,
                "DROP SEQUENCE IF EXISTS %s" % sequence_ref,
            )

        self.defer(
            context,
            "删除测试序列 %s" % name,
            cleanup,
            priority=options.get("cleanup_priority", 0),
        )

        if options.get("setup", True):
            self.checked(
                context,
                node,
                _env(context, "user", "postgres"),
                database,
                "CREATE SEQUENCE %s" % sequence_ref,
            )
            ownership["created"] = True

    def table_grants(self, options):
        context = self.context
        """Grant disposable table privileges required as a prior test state."""
        node = context.resolve_node(options.get("node", "primary"))
        database = options.get("database", "postgres")
        schema = options.get("schema", "public")
        table = options.get("table")
        if not table:
            raise self.config_error("table_grants fixture 缺少 table")
        table_ref = "%s.%s" % (_identifier(schema), _identifier(table))
        for role, privileges in (options.get("grants") or {}).items():
            self.checked(
                context,
                node,
                _env(context, "user", "postgres"),
                database,
                "GRANT %s ON %s TO %s" % (privileges, table_ref, _identifier(role)),
            )

    def settings(self, options):
        context = self.context
        selectors = options.get("nodes") or [options.get("node", "primary")]
        values = options.get("values") or {}
        action = options.get("apply", "reload")
        user = options.get("user") or _env(context, "user", "postgres")
        setup = options.get("setup", True)
        restore_runtime_value = options.get("restore_runtime_value", False)
        if action not in ("reload", "restart"):
            raise self.config_error("settings fixture apply 只支持 reload 或 restart")
        saved = []

        def cleanup():
            for node, name, old, auto_value in saved:
                sql = (
                    "ALTER SYSTEM RESET %s" % name
                    if auto_value is None
                    else "ALTER SYSTEM SET %s = %s" % (name, _literal(auto_value))
                )
                self.checked(context, node, user, "postgres", sql)
            if saved:
                self.apply_cluster(context, action)

        cleanup_registered = False
        for selector in selectors:
            node = context.resolve_node(selector)
            for name, value in values.items():
                if not _GUC_NAME.match(name):
                    raise self.config_error("非法 PostgreSQL 配置名: %s" % name)
                old = self.scalar(
                    context,
                    node,
                    "postgres",
                    "SELECT CASE WHEN current_setting(%s) = '' THEN "
                    "%s ELSE current_setting(%s) END"
                    % (
                        _literal(name),
                        _literal(self.empty_setting_token),
                        _literal(name),
                    ),
                    user=user,
                )
                if old == self.empty_setting_token:
                    old = ""
                auto_result = self.execute(
                    context,
                    node,
                    _env(context, "user", "postgres"),
                    "postgres",
                    "SELECT setting FROM pg_file_settings "
                    "WHERE name = %s AND sourcefile LIKE '%%/postgresql.auto.conf' "
                    "ORDER BY seqno DESC LIMIT 1" % _literal(name),
                    structured=True,
                )
                if auto_result.returncode != 0:
                    raise self.operation_error(
                        auto_result.output.strip() or "无法读取 ALTER SYSTEM 原配置"
                    )
                auto_value = auto_result.rows[0][0] if auto_result.rows else None
                if restore_runtime_value:
                    auto_value = old
                saved.append((node, name, old, auto_value))
                if not cleanup_registered:
                    self.defer(context, "恢复 PostgreSQL 配置", cleanup, priority=100)
                    cleanup_registered = True
                if setup:
                    self.checked(
                        context,
                        node,
                        user,
                        "postgres",
                        "ALTER SYSTEM SET %s = %s" % (name, _literal(value)),
                    )
        if saved and setup:
            self.apply_cluster(context, action)
            details = context.values.setdefault("postgresql_settings", [])
            purpose = options.get("purpose", "用例临时 PostgreSQL 配置")
            for node, name, old, _ in saved:
                if setup:
                    detail = self.check_setting(
                        context,
                        node,
                        {
                            "source": "fixture",
                            "name": name,
                            "equals": str(values[name]),
                            "purpose": purpose,
                        },
                    )
                    detail["previous"] = old
                    details.append(detail)
                    if not detail["matched"]:
                        raise self.operation_error(
                            "PostgreSQL 参数 %s 未生效（实际=%s，要求=%s）"
                            % (name, detail["actual"], detail["requirement"])
                        )
