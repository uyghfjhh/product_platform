from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import CaseContext


class FixtureManager:
    """Own fixtures operations for one case execution."""

    def __init__(self, context: CaseContext):
        self.context = context
        self._cleanup_actions = []
        self._cleanup_sequence = 0

    def defer_cleanup(self, action: Callable[[], None], *, priority: int = 0) -> None:
        """Register an idempotent product fixture cleanup action.

        Cleanup drains highest priority first; actions with the same priority
        run in reverse registration order. Products use priorities to order
        resource teardown (e.g. stop postmasters before releasing ports).
        """
        self._cleanup_actions.append((priority, self._cleanup_sequence, action))
        self._cleanup_sequence += 1

    def cleanup_fixtures(self) -> None:
        """Run registered fixture cleanup in reverse order."""
        errors = []
        pending = sorted(self._cleanup_actions, key=lambda item: item[:2])
        self._cleanup_actions.clear()
        while pending:
            _, _, action = pending.pop()
            try:
                with self.context.suppress_cancellation():
                    action()
            except Exception as exc:
                errors.append(str(exc))
        errors.extend(self.context._commands.archive_logs())
        if errors:
            raise RuntimeError("; ".join(errors))

    def reload(self, node: str, *, user: str | None = None) -> None:
        """Reload PostgreSQL configuration through the declared node."""
        kwargs = {"user": user} if user else {}
        self.context.sql(node, "SELECT pg_reload_conf()", **kwargs)
        self.context.step("fixture-reload", "重载数据库配置")

    def set_setting(
        self, node: str, name: str, value: str, *, user: str | None = None
    ) -> None:
        """Set a runtime setting and restore its previous value afterwards."""
        if not name.replace("_", "").replace(".", "").isalnum():
            raise ValueError("配置参数名无效")
        kwargs = {"user": user} if user else {}
        self.context.sql(
            node, f"SELECT current_setting('{name}', true)", **kwargs
        ).rows
        auto = self.context.sql(node,
            "SELECT setting FROM pg_file_settings WHERE name = '%s' "
            "AND sourcefile LIKE '%%/postgresql.auto.conf' ORDER BY seqno DESC LIMIT 1" % name,
            **kwargs).rows
        auto_value = auto[0][0] if auto else None
        restore = (f"RESET {name}" if auto_value is None else
                   f"SET {name} = '{str(auto_value).replace(chr(39), chr(39) * 2)}'")
        self.context.defer_cleanup(lambda: (
            self.context.sql(node, f"ALTER SYSTEM {restore}", **kwargs),
            self.context.reload(node, user=user)))
        escaped = value.replace("'", "''")
        self.context.sql(node, f"ALTER SYSTEM SET {name} = '{escaped}'", **kwargs)
        self.context.reload(node, user=user)

    def create_role(self, node: str, name: str, attributes: str = "") -> None:
        """Create a temporary role and guarantee cleanup after the case."""
        if not name.replace("_", "").isalnum():
            raise ValueError("角色名无效")
        suffix = (" " + attributes.strip()) if attributes.strip() else ""
        self.context.sql(node, f"CREATE ROLE {name}{suffix}")
        self.context.defer_cleanup(
            lambda: self.context.sql(node, f"DROP ROLE IF EXISTS {name}")
        )

    def defer_drop_table(self, node: str, name: str) -> None:
        """Register cleanup for a table declared by a catalog fixture."""
        if not name.replace("_", "").isalnum():
            raise ValueError("表名无效")
        self.context.defer_cleanup(
            lambda: self.context.sql(node, f"DROP TABLE IF EXISTS {name}")
        )
