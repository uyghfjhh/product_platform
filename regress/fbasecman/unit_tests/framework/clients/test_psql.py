import unittest

from framework.clients.psql import build_psql_command


class PsqlClientTest(unittest.TestCase):
    def test_builds_connection_and_sql_arguments(self):
        self.assertEqual(
            [
                "/opt/pgsql/bin/psql",
                "-h", "db.example",
                "-p", "6432",
                "-U", "postgres",
                "-d", "postgres",
                "-c", "select 1;",
            ],
            build_psql_command(
                "/opt/pgsql", "db.example", 6432, "postgres", "postgres", "select 1;"
            ),
        )

    def test_builds_structured_output_options(self):
        self.assertEqual(
            [
                "/opt/pgsql/bin/psql",
                "-h", "localhost",
                "-p", "6432",
                "-U", "admin",
                "-d", "console",
                "-P", "footer=off",
                "-P", "format=unaligned",
                "-F", "|",
                "-t",
                "-c", "show stats;",
            ],
            build_psql_command(
                "/opt/pgsql",
                "localhost",
                6432,
                "admin",
                "console",
                "show stats;",
                footer=False,
                output_format="unaligned",
                field_separator="|",
                tuples_only=True,
            ),
        )
