import unittest

from suites.global_cache.domains.capacity import (
    case_ps_limit,
    ps_limit_conf_keys,
    ps_limit_replacements,
    ps_limit_required_items,
)
from suites.global_cache.manifest import BACKEND_PS_LIMIT_KEY, GLOBAL_PS_LIMIT_KEY


class CapacityConfigTest(unittest.TestCase):
    def test_builds_global_and_backend_limit_configuration(self):
        self.assertEqual(
            [
                ("%s 10000" % GLOBAL_PS_LIMIT_KEY, "%s 5" % GLOBAL_PS_LIMIT_KEY),
                ("%s 10000" % BACKEND_PS_LIMIT_KEY, "%s 2" % BACKEND_PS_LIMIT_KEY),
            ],
            ps_limit_replacements(5, 2),
        )
        self.assertEqual(
            [(GLOBAL_PS_LIMIT_KEY, 5), (BACKEND_PS_LIMIT_KEY, 2)],
            ps_limit_required_items(5, 2),
        )
        self.assertEqual([GLOBAL_PS_LIMIT_KEY, BACKEND_PS_LIMIT_KEY], ps_limit_conf_keys())

    def test_case_limit_prefers_global_then_backend_then_default(self):
        self.assertEqual(7, case_ps_limit({GLOBAL_PS_LIMIT_KEY: 7, BACKEND_PS_LIMIT_KEY: 3}, 1))
        self.assertEqual(3, case_ps_limit({BACKEND_PS_LIMIT_KEY: 3}, 1))
        self.assertEqual(1, case_ps_limit({}, 1))
