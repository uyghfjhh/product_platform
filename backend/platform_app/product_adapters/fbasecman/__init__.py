"""fbasecman product adapter."""
from .artifacts import (
    CaseProgressObserver,
    case_artifacts,
    case_log,
    export_source_report,
    recent_case_statuses,
    sync_current_results,
)
from .fixture import prepare as prepare_fbasecman_fixture, prepare
from .profile import build_profile, legacy_root, profile_paths, save_profile
from .observations import (
    ParsedObservation,
    parse_group_members,
    parse_group_routing,
    parse_groups,
    parse_monitor_config,
    parse_node_monitor,
    parse_node_status,
    parse_nodes,
    parse_replication,
)

__all__ = [
    "CaseProgressObserver",
    "ParsedObservation",
    "build_profile",
    "case_artifacts",
    "case_log",
    "export_source_report",
    "legacy_root",
    "parse_group_members",
    "parse_group_routing",
    "parse_groups",
    "parse_monitor_config",
    "parse_node_monitor",
    "parse_node_status",
    "parse_nodes",
    "parse_replication",
    "prepare_fbasecman_fixture",
    "profile_paths",
    "recent_case_statuses",
    "save_profile",
    "sync_current_results",
]
