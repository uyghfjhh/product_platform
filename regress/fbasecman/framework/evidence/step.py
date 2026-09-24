"""Action-scoped evidence and crash-safe step persistence."""

import json
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from framework.execution.polling import PollTimeout, poll_until
from framework.persistence.atomic import atomic_write_text


class EvidenceStepError(RuntimeError):
    pass


class StepJournal(object):
    """Persist completed and in-progress business steps after every change."""

    def __init__(self, path, target):
        self.path = Path(path)
        self.target = target
        self.steps = []

    def save(self):
        payload = {
            "target": self.target,
            "updated_at": datetime.now().isoformat(),
            "steps": self.steps,
        }
        atomic_write_text(self.path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def append(self, record):
        self.steps.append(record)
        self.save()
        return record


class EvidenceStep(object):
    """One business action and the evidence collected while it is executed.

    ``open_window`` and ``collect_window`` are suite adapters.  They let the
    framework keep a common step lifecycle while a suite decides which local
    and remote logs are relevant to its topology.
    """

    def __init__(self, title, journal, open_window=None, collect_window=None,
                 critical=True, expected=None, on_change=None, metadata=None):
        self.title = title
        self.journal = journal
        self.open_window = open_window
        self.collect_window = collect_window
        self.on_change = on_change
        self.record = {
            "title": title,
            "critical": bool(critical),
            "status": "RUNNING",
            "started_at": datetime.now().isoformat(),
            "execution": [],
            "intermediate": [],
            "evidence": [],
            "expected": expected or "",
            "actual": "",
            "result": "",
        }
        if metadata:
            self.record.update(dict(metadata))
        self._window = None
        self._journal_index = None

    def __enter__(self):
        if self.open_window:
            self._window = self.open_window()
        self._journal_index = len(self.journal.steps)
        self.journal.append(self.record)
        return self

    def _save(self):
        if self._journal_index is None:
            raise EvidenceStepError("evidence step has not started")
        self.journal.steps[self._journal_index] = self.record
        self.journal.save()
        if self.on_change:
            self.on_change()

    def actual_execution(self, command, output, label="实际执行"):
        text = str(command).rstrip()
        rendered_output = str(output).rstrip()
        if rendered_output:
            text = "%s\n\n%s" % (text, rendered_output)
        self.record["execution"].append({"label": label, "text": text})
        self._save()

    def intermediate_state(self, command, output, label="中间状态"):
        text = str(command).rstrip()
        rendered_output = str(output).rstrip()
        if rendered_output:
            text = "%s\n\n%s" % (text, rendered_output)
        self.record["intermediate"].append({"label": label, "text": text})
        self._save()

    def add_evidence(self, label, text):
        rendered = str(text).rstrip()
        if rendered:
            self.record["evidence"].append({"label": label, "text": rendered})
            self._save()

    def mark_observation_only(self):
        """Mark this step as a snapshot whose checks decide PASS or FAIL."""
        self.record["observation_only"] = True
        self._save()

    def expect(self, expected, actual, passed):
        return self.assess(expected, actual, passed, raise_on_fail=True)

    def assess(self, expected, actual, passed, raise_on_fail=False):
        self.record["expected"] = str(expected).strip()
        self.record["actual"] = str(actual).strip()
        self.record["result"] = "PASS" if passed else "FAIL"
        self._save()
        if not passed and raise_on_fail:
            raise EvidenceStepError("%s: expected %s, actual %s" % (
                self.title, expected, actual))
        return bool(passed)

    def wait_until(self, probe, accept, description, timeout=5.0, interval=0.1):
        started = time.time()
        try:
            value = poll_until(probe, accept, timeout=timeout, interval=interval)
        except PollTimeout as exc:
            actual = "等待 %.3fs 后仍未满足；最后状态: %s" % (timeout, exc.last_value)
            self.intermediate_state("等待条件: %s" % description, actual)
            self.expect(description, actual, False)
            raise
        elapsed = time.time() - started
        self.intermediate_state(
            "等待条件: %s" % description,
            "已满足，收敛耗时 %.3fs\n%s" % (elapsed, value),
        )
        return value

    def _collect(self):
        if not self.collect_window or self._window is None:
            return
        for label, text in self.collect_window(self._window) or ():
            self.add_evidence(label, text)

    def __exit__(self, exc_type, exc_value, traceback):
        self._collect()
        self.record["finished_at"] = datetime.now().isoformat()
        if exc_type is None:
            if not self.record["result"]:
                self.record["result"] = "PASS"
            self.record["status"] = "COMPLETED"
        else:
            self.record["status"] = "FAILED"
            self.record["result"] = "FAIL"
            if not self.record["actual"]:
                self.record["actual"] = str(exc_value)
            self.record["error"] = str(exc_value)
        self._save()
        return False


@contextmanager
def evidence_step(*args, **kwargs):
    step = EvidenceStep(*args, **kwargs)
    with step:
        yield step
