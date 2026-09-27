"""Deadline-based polling without product-specific assumptions."""

import time


class PollTimeout(RuntimeError):
    def __init__(self, timeout, last_value):
        self.timeout = timeout
        self.last_value = last_value
        super().__init__("condition not met within %ss" % timeout)


def poll_until(probe, accept, timeout, interval=1.0):
    deadline = time.time() + timeout
    last_value = None
    while time.time() < deadline:
        last_value = probe()
        if accept(last_value):
            return last_value
        time.sleep(interval)
    raise PollTimeout(timeout, last_value)
