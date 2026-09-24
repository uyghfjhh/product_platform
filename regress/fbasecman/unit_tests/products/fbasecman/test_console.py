import unittest

from products.fbasecman.console import ConsoleQueryError, parse_pipe_rows


class ConsoleParserTest(unittest.TestCase):
    def test_parses_pipe_rows(self):
        self.assertEqual([["a", "b"], ["1", "2"]], parse_pipe_rows("a|b\n1|2\n"))

    def test_rejects_psql_error(self):
        with self.assertRaises(ConsoleQueryError):
            parse_pipe_rows("psql: error: connection failed\n")
