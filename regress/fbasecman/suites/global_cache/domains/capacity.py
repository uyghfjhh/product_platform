"""Capacity configuration and seed actions."""

from suites.global_cache.drivers import run_prepared_sequence
from suites.global_cache.manifest import BACKEND_PS_LIMIT_KEY, GLOBAL_PS_LIMIT_KEY


def ps_limit_replacements(global_limit, backend_limit=None):
    backend_limit = global_limit if backend_limit is None else backend_limit
    return [
        (
            "%s 10000" % GLOBAL_PS_LIMIT_KEY,
            "%s %s" % (GLOBAL_PS_LIMIT_KEY, global_limit),
        ),
        (
            "%s 10000" % BACKEND_PS_LIMIT_KEY,
            "%s %s" % (BACKEND_PS_LIMIT_KEY, backend_limit),
        ),
    ]


def ps_limit_required_items(global_limit, backend_limit=None):
    backend_limit = global_limit if backend_limit is None else backend_limit
    return [
        (GLOBAL_PS_LIMIT_KEY, global_limit),
        (BACKEND_PS_LIMIT_KEY, backend_limit),
    ]


def ps_limit_conf_keys():
    return [GLOBAL_PS_LIMIT_KEY, BACKEND_PS_LIMIT_KEY]


def case_ps_limit(case_config, default):
    if GLOBAL_PS_LIMIT_KEY in case_config:
        return int(case_config[GLOBAL_PS_LIMIT_KEY])
    if BACKEND_PS_LIMIT_KEY in case_config:
        return int(case_config[BACKEND_PS_LIMIT_KEY])
    return int(default)


def seed_capacity_entries(rt, count, prefix):
    operations = []
    statements = []
    for index in range(1, count + 1):
        tag = "%s_%02d" % (prefix, index)
        sql = "select name from test where id = ? /* %s */" % tag
        operations.append(("seed_%02d" % index, "query_int:%d" % index, sql))
        statements.append(sql)
    run_prepared_sequence(rt, operations, log_stem="GC_%s" % prefix)
    rt.summary["seed_sql_tags"] = statements
