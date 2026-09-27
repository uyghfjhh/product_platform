"""Environment provider registration."""


_PROVIDERS = {}


def register_environment_provider(name, factory):
    if not name:
        raise ValueError("environment provider name must not be empty")
    if name in _PROVIDERS:
        raise ValueError("environment provider already registered: %s" % name)
    _PROVIDERS[name] = factory


def create_environment_provider(name, regress_env, verbose=True):
    try:
        factory = _PROVIDERS[name]
    except KeyError:
        raise ValueError("unknown environment provider: %s" % name)
    return factory(regress_env, verbose=verbose)
