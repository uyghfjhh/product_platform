"""Typed configuration checks shared by product regression profiles."""


class ConfigurationError(ValueError):
    pass


def require_mapping(config, name):
    value = config.get(name)
    if not isinstance(value, dict):
        raise ConfigurationError("configuration section %r must be a mapping" % name)
    return value


def reject_unknown(mapping, allowed, location):
    unknown = sorted(set(mapping) - set(allowed))
    if unknown:
        raise ConfigurationError("unknown field(s) in %s: %s" % (location, ", ".join(unknown)))


def require_text(mapping, names, location):
    for name in names:
        value = mapping.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError("%s.%s must be a non-empty string" % (location, name))


def port_value(value, location):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ConfigurationError("%s must be an integer in range 1..65535" % location)
    return value


# Legacy aliases kept for products migrating off the vendored framework.
_require_mapping = require_mapping
_reject_unknown = reject_unknown
_require_text = require_text
_port = port_value
