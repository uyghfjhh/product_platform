"""Resource ledger and graceful-termination contracts.

The engine restores the environment through deferred cleanups on every
orderly exit; a killed process cannot, so the ledger lets the next run
reclaim dead-owned resources.  These tests pin the reclaim semantics and
the cancellation-suppression contract that lets cleanup commands execute
after a run is cancelled.
"""

from __future__ import annotations

import signal
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from platform_regress.engine import Cancelled, CaseContext  # noqa: E402
from platform_regress.ledger import (  # noqa: E402
    ResourceLedger, _parse_ipcs_shm)


def _context(tmp_path, cancelled):
    return CaseContext("demo.case", tmp_path / "out", cancelled=cancelled)


def test_ledger_entries_track_owner_pid(tmp_path):
    ledger = ResourceLedger(tmp_path / "ledgers")
    entry_id = ledger.register("isolated_cluster",
                               data_dir="/tmp/fbase_regress_a")
    assert ledger.entries()[0]["owner_pid"] == __import__("os").getpid()
    assert ledger.sweep() == []  # own process is alive
    ledger.release(entry_id)
    assert ledger.entries() == []


def test_ledger_sweep_returns_dead_owners_only(tmp_path):
    dead = ResourceLedger(tmp_path / "ledgers", owner_pid=2147483000)
    dead.register("isolated_cluster", data_dir="/tmp/fbase_regress_dead")
    alive = ResourceLedger(tmp_path / "ledgers")
    alive.register("isolated_cluster", data_dir="/tmp/fbase_regress_live")
    swept = alive.sweep("isolated_cluster")
    assert [e["data_dir"] for e in swept] == ["/tmp/fbase_regress_dead"]
    alive.drop(swept[0])
    assert len(alive.entries()) == 1


def test_cleanup_runs_commands_while_cancelled(tmp_path):
    ran = []
    context = _context(tmp_path, cancelled=lambda: True)
    context.defer_cleanup(
        lambda: ran.append(context.command(["echo", "ok"],
                                           timeout_seconds=5).returncode))
    context.cleanup_fixtures()
    assert ran == [0]


def test_cancellation_still_interrupts_work_phase(tmp_path):
    context = _context(tmp_path, cancelled=lambda: True)
    with pytest.raises(Cancelled):
        context.check_cancel()
    # Suppression is scoped: after cleanup drain the flag is restored.
    with context.suppress_cancellation():
        context.check_cancel()  # no raise inside suppression
    with pytest.raises(Cancelled):
        context.check_cancel()


def test_context_ledger_is_per_environment(tmp_path):
    context = _context(tmp_path, cancelled=lambda: False)
    ledger = context.ledger
    assert ledger.root == (tmp_path / "out").resolve().parent / "ledgers"
    assert context.ledger is ledger


def test_parse_ipcs_shm_merges_segment_and_pid_tables():
    segments = """
------ Shared Memory Segments --------
key        shmid      owner      perms      bytes      nattch     status
0x0559f340 32768      postgres   600        56         21
0x0c1773c9 32775      postgres   600        56         0
"""
    pids = """
------ Shared Memory Creator/Last-op PIDs --------
shmid      owner      cpid       lpid
32768      postgres   183423     352512
32775      postgres   207645     207647
"""
    rows = _parse_ipcs_shm(segments, pids)
    assert len(rows) == 2
    by_id = {row["shmid"]: row for row in rows}
    assert by_id[32768]["cpid"] == 183423
    assert by_id[32775]["nattch"] == 0
