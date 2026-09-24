"""GUC reload scenarios built from shared config and JDBC sequence capabilities."""

from framework.configuration.reload import install_reload_config, record_config_transition
from suites.global_cache.domains.guc_sequences import run_guc_redeploy_phase


def run_guc_reload_toggle_case(rt):
    start_sync = rt.case.assertions.get("start_enable_guc_sync", "no")
    reload_sync = rt.case.assertions.get("reload_enable_guc_sync", "yes")
    scope = rt.case.assertions.get("guc_sync_scope", "global")
    if scope == "user":
        replacements = [
            ('storage_user "postgres"', 'storage_user "postgres"\n    enable_guc_sync %s' % start_sync)
        ]
    else:
        replacements = [('enable_guc_sync yes', 'enable_guc_sync %s' % start_sync)]
    start_conf = rt.render_runtime_conf(replacements, "guc_reload_live.conf")
    start_conf_text = start_conf.read_text(encoding="utf-8", errors="replace")
    rt.start_fbasecman(conf=start_conf)

    guc_name = rt.case.assertions.get("guc_name", "enable_seqscan")
    guc_value = rt.case.assertions.get("guc_value", "off")
    expected_before = rt.case.assertions.get("expected_before", "on")
    expected_after = rt.case.assertions.get("expected_after", "off")

    rt.record_step(
        "reload 前执行 GUC redeploy 验证",
        note="先按当前 enable_guc_sync 配置执行 SET + READ ONLY + prepared current_setting，记录切后端后的实际值。",
    )
    run_guc_redeploy_phase(rt, "before_reload", guc_name, guc_value, "GCGucReloadBefore")
    before_sequence = {
        "operations": list(rt.summary.get("jdbc_sequence", [])),
        "output": rt.summary.get("jdbc_sequence_output", ""),
    }
    before_reload_state = rt.capture_console_state("before_reload", include_server=False)

    if scope == "user":
        reload_replacements = [
            ('storage_user "postgres"', 'storage_user "postgres"\n    enable_guc_sync %s' % reload_sync)
        ]
    else:
        reload_replacements = [('enable_guc_sync yes', 'enable_guc_sync %s' % reload_sync)]
    reload_conf = rt.render_runtime_conf(reload_replacements, "guc_reload_next.conf")
    reload_conf_text = reload_conf.read_text(encoding="utf-8", errors="replace")
    record_config_transition(
        rt,
        "初始运行配置: enable_guc_sync %s" % start_sync,
        "修改运行中配置: enable_guc_sync %s -> %s" % (start_sync, reload_sync),
        start_conf,
        start_conf_text,
        reload_conf,
        reload_conf_text,
        [("enable_guc_sync", start_sync, reload_sync)],
    )
    install_reload_config(reload_conf, start_conf, [("enable_guc_sync", reload_sync)])
    rt.console_reload()
    reload_log = rt.logs_dir / "console_reload.log"
    if reload_log.exists():
        rt.summary["reload_result"] = (
            reload_log.read_text(encoding="utf-8", errors="replace").strip() or "<empty>"
        )

    rt.record_step(
        "reload 后再次执行 GUC redeploy 验证",
        note="reload 生效后重复同样的 SET + READ ONLY + prepared current_setting，观察新后端看到的值是否变化。",
    )
    run_guc_redeploy_phase(rt, "after_reload", guc_name, guc_value, "GCGucReloadAfter")
    after_sequence = {
        "operations": list(rt.summary.get("jdbc_sequence", [])),
        "output": rt.summary.get("jdbc_sequence_output", ""),
    }
    after_reload_state = rt.capture_console_state("after", include_server=False)

    rt.summary["reload_toggle"] = {
        "start": start_sync,
        "after": reload_sync,
        "guc_name": guc_name,
        "guc_value": guc_value,
        "expected_before": expected_before,
        "expected_after": expected_after,
        "scope": scope,
    }
    rt.summary["guc_reload_sequences"] = {
        "before": before_sequence,
        "after": after_sequence,
    }
    return before_reload_state, after_reload_state
