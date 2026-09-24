import unittest

from framework.execution.polling import PollTimeout, poll_until


class PollingTest(unittest.TestCase):
    def test_returns_first_accepted_observation(self):
        values = iter([1, 2, 3])
        self.assertEqual(3, poll_until(lambda: next(values), lambda value: value == 3, 1, 0))

    def test_timeout_preserves_last_observation(self):
        with self.assertRaises(PollTimeout) as raised:
            poll_until(lambda: "latest", lambda value: False, 0.001, 0)
        self.assertEqual("latest", raised.exception.last_value)
