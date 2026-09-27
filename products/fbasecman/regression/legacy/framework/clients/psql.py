"""Compatibility import; psql client helpers live in the platform SDK."""
from platform_regress.clients.psql import (  # noqa: F401
    assert_table_rows, build_psql_command, parse_psql_table,
)
