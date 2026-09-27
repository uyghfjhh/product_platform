import unittest

import framework.environment.registry as registry


class EnvironmentRegistryTest(unittest.TestCase):
    def setUp(self):
        self.original = dict(registry._PROVIDERS)
        registry._PROVIDERS.clear()

    def tearDown(self):
        registry._PROVIDERS.clear()
        registry._PROVIDERS.update(self.original)

    def test_creates_registered_provider(self):
        registry.register_environment_provider(
            "sample", lambda env, verbose=True: (env, verbose)
        )
        self.assertEqual(
            ("env", False),
            registry.create_environment_provider("sample", "env", verbose=False),
        )

    def test_rejects_duplicate_and_unknown_providers(self):
        registry.register_environment_provider("sample", object)
        with self.assertRaises(ValueError):
            registry.register_environment_provider("sample", object)
        with self.assertRaises(ValueError):
            registry.create_environment_provider("missing", "env")
