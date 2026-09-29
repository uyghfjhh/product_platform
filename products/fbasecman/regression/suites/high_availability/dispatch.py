"""high_availability executor registry — importable without the suite runner.

The platform engine path resolves executors from this module; ``suite.py``
re-exports ``EXECUTORS`` for the manual entry and vendored tests.
"""

from .executors.phase1_console import (
    run_core_19_set_node_atomicity,
    run_core_20_write_promoted_refresh_show,
    run_core_21_reload_parameters_and_structure,
    run_core_22_reload_failure_protection,
)
from .executors.phase2_failure import (
    run_core_13_monitor_confirm,
    run_core_14_rep_standby_failure,
    run_core_15_rep_primary_failure,
    run_core_18_balance_single_failure,
)
from .executors.phase3_failover import (
    run_core_16_rep_failover,
    run_core_17_mmr_write_center_failover,
)

EXECUTORS = {
    "core_13_monitor_confirm": run_core_13_monitor_confirm,
    "core_14_rep_standby_failure": run_core_14_rep_standby_failure,
    "core_15_rep_primary_failure": run_core_15_rep_primary_failure,
    "core_16_rep_failover": run_core_16_rep_failover,
    "core_17_mmr_write_center_failover": run_core_17_mmr_write_center_failover,
    "core_18_balance_single_failure": run_core_18_balance_single_failure,
    "core_19_set_node_atomicity": run_core_19_set_node_atomicity,
    "core_20_write_promoted_refresh_show": run_core_20_write_promoted_refresh_show,
    "core_21_reload_parameters_and_structure": run_core_21_reload_parameters_and_structure,
    "core_22_reload_failure_protection": run_core_22_reload_failure_protection,
}
