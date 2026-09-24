"""Assertions for declarative GUC JDBC sequence cases."""

from framework.evidence.assertions import matching_rows, pipe_rows, require_markers
from suites.global_cache.errors import GlobalCacheFailure


def _read_log(path):
    return path.read_text(encoding="utf-8", errors="replace")


def assert_guc_reload_toggle(rt, before_state, after_state):
    cfg = rt.summary.get("reload_toggle", {})
    expected_before = cfg.get("expected_before", "on")
    expected_after = cfg.get("expected_after", "off")
    guc_name = cfg.get("guc_name", "enable_seqscan")
    start_sync = cfg.get("start", "no")
    reload_sync = cfg.get("after", "yes")
    before_marker = "before_reload_current_setting=%s" % expected_before
    after_marker = "after_reload_current_setting=%s" % expected_after
    require_markers(
        _read_log(rt.logs_dir / "GCGucReloadBefore.java.log"),
        [before_marker],
        "guc reload before phase",
        GlobalCacheFailure,
    )
    require_markers(
        _read_log(rt.logs_dir / "GCGucReloadAfter.java.log"),
        [after_marker],
        "guc reload after phase",
        GlobalCacheFailure,
    )
    matched = pipe_rows(
        matching_rows(
            after_state["global"],
            1,
            contains="current_setting('%s')" % guc_name,
            minimum_columns=5,
        )
    )
    if not matched:
        raise GlobalCacheFailure("guc reload toggle case expects prepared current_setting entry in global cache")
    rt.summary["matched_global"] = matched
    rt.summary["verification_checks"] = [
        {
            "title": "reload 前切后端后的 current_setting 值符合初始 enable_guc_sync 行为",
            "expected": "enable_guc_sync=%s 时，JDBC 日志出现 %s" % (start_sync, before_marker),
            "actual": "JDBC 日志已捕获 %s" % before_marker,
            "result": "PASS",
        },
        {
            "title": "reload 后切后端后的 current_setting 值切换到新 enable_guc_sync 行为",
            "expected": "reload 后 enable_guc_sync=%s 时，JDBC 日志出现 %s" % (reload_sync, after_marker),
            "actual": "JDBC 日志已捕获 %s" % after_marker,
            "result": "PASS",
        },
        {
            "title": "reload 前后复用的 prepared current_setting 仍可在 global cache 中观测",
            "expected": "console 中存在 current_setting('%s') 对应 entry" % guc_name,
            "actual": matched[0],
            "result": "PASS",
        },
    ]
    rt.summary["business_summary_lines"] = [
        "先按初始 enable_guc_sync 配置执行 SET + READ ONLY + prepared current_setting，记录切后端后的实际值。",
        "随后仅通过 console reload 修改 enable_guc_sync，再重复同样流程。",
        "验证 reload 会改变后续新后端部署是否同步前端 GUC 的行为。",
    ]
    rt.summary["key_evidence_lines"] = [
        "Before JDBC: %s" % before_marker,
        "Reload result: %s" % rt.summary.get("reload_result", "<missing>"),
        "After JDBC: %s" % after_marker,
        "Console(global): %s" % matched[0],
    ]
