import unittest

from framework.execution.background import capture_command, start_background


class BackgroundTest(unittest.TestCase):
    def test_capture_command(self):
        self.assertEqual("ok", capture_command(["printf", "ok"]))

    def test_background_wait(self):
        process = start_background(["true"])
        self.assertEqual(0, process.wait())
