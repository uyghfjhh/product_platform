from platform_regress.sdk import CaseContext
from platform_regress.execution.forensics import CoreSnapshot, CORE_PATTERN


def test_core_pattern_matching():
    assert CORE_PATTERN.match("core")
    assert CORE_PATTERN.match("core.12345")
    assert CORE_PATTERN.match("core-123")
    assert CORE_PATTERN.match("core_123")
    assert CORE_PATTERN.match("fbasecman.core")
    assert CORE_PATTERN.match("fbasecman.core.123")
    assert not CORE_PATTERN.match("score.txt")


def test_core_snapshot_lifecycle(tmp_path):
    root1 = tmp_path / "node1"
    root2 = tmp_path / "node2"
    root1.mkdir()
    root2.mkdir()

    # Pre-existing core file and source code before test
    old_core = root1 / "core.old"
    old_core.write_text("old core dump")
    script = root1 / "core_helper.py"
    script.write_text("print('test')")

    snapshot = CoreSnapshot([root1, root2])
    assert not snapshot.detect_new_cores()

    # Create new core file during test
    new_core = root2 / "core.9999"
    new_core.write_text("new crash dump")

    detected = snapshot.detect_new_cores()
    assert len(detected) == 1
    assert detected[0].name == "core.9999"

    # Context attachment
    context = CaseContext("test.crash_case", tmp_path / "output", environment={})
    attached = snapshot.attach_evidence(context, "core-files.txt")
    assert len(attached) == 1
    assert any("core-files.txt" in ev for ev in context.evidence)
    artifact_path = context.output_dir / "artifacts" / context.execution_id / "core-files.txt"
    assert artifact_path.read_text().strip() == str(new_core)
