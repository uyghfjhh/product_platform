"""Declarative JDBC sequences for GUC scenarios."""

from suites.global_cache.drivers import run_prepared_sequence


def _sequence_value(output, key):
    prefix = key + "="
    for line in output.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return "<未返回>"


def run_guc_set_report_case(rt):
    guc_sql = rt.case.assertions.get("guc_sql", "SET application_name = 'gc_guc_report'")
    _, output = run_prepared_sequence(rt, [
        ("guc_set_execute", "execute", guc_sql),
        ("application_name", "query", "SHOW application_name"),
    ], log_stem="GCGucSetReport")
    rt.step_records[-1].update({
        "expected": "prepared SET 执行完成，随后可查询到新的 application_name",
        "actual": "SET 已完成；SHOW application_name 返回 %s" % _sequence_value(
            output, "application_name"
        ),
        "result": "PASS",
    })


def run_guc_reset_all_bypass_case(rt):
    report_guc_sql = rt.case.assertions.get("report_guc_sql", "SET application_name = 'gc_reset_all_case'")
    noreport_guc_sql = rt.case.assertions.get("noreport_guc_sql", "SET extra_float_digits = 2")
    reset_all_sql = rt.case.assertions.get("reset_all_sql", "RESET ALL")
    verify_sql = rt.case.assertions.get("verify_sql", "SHOW extra_float_digits")
    _, output = run_prepared_sequence(rt, [
        ("guc_reset_all_set_report_execute", "execute", report_guc_sql),
        ("guc_reset_all_set_noreport_execute", "execute", noreport_guc_sql),
        ("guc_reset_all_execute", "execute", reset_all_sql),
        ("guc_reset_all_verify_value", "query", verify_sql),
    ], log_stem="GCGucResetAllBypass")
    rt.step_records[-1].update({
        "expected": "依次执行两个 SET 和 RESET ALL，随后连接仍可查询 GUC",
        "actual": "SET、RESET ALL 均已完成；SHOW extra_float_digits 返回 %s" % _sequence_value(
            output, "guc_reset_all_verify_value"
        ),
        "result": "PASS",
    })


def run_guc_redeploy_phase(rt, label_prefix, guc_name, guc_value, log_stem):
    run_prepared_sequence(rt, [
        ("set_guc", "statement", "SET %s = %s" % (guc_name, guc_value)),
        ("set_read_only", "statement", "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"),
        (
            "%s_current_setting" % label_prefix,
            "query",
            "SELECT current_setting('%s')" % guc_name,
        ),
    ], log_stem=log_stem)
