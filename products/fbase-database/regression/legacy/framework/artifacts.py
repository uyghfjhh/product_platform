import datetime
import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path
from xml.etree import ElementTree

import fcntl

from framework.errors import OperationError


def create_run_id():
    return "run_%s_%s" % (
        datetime.datetime.now().strftime("%Y%m%d_%H%M%S"), uuid.uuid4().hex[:6])


def run_directory(root, cluster, run_id):
    return Path(root) / "output" / "runs" / cluster / run_id


class ClusterRunLock(object):
    """Prevent concurrent runs from changing the same managed cluster."""

    def __init__(self, root, cluster, identity=None):
        if identity:
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
            self.path = Path(tempfile.gettempdir()) / "fbase_regress_locks" / (
                "cluster_%s.lock" % digest)
        else:
            self.path = Path(root) / "output" / "envs" / cluster / "run.lock"
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            self.stream.seek(0)
            owner = self.stream.read().strip() or "未知进程"
            self.stream.close()
            self.stream = None
            raise OperationError("cluster 正在执行测试: %s" % owner)
        self.stream.seek(0)
        self.stream.truncate()
        self.stream.write("pid=%s started_at=%s\n" % (
            os.getpid(), datetime.datetime.now().isoformat(timespec="seconds")))
        self.stream.flush()
        return self

    def __exit__(self, unused_type, unused_value, unused_traceback):
        if self.stream:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()
            self.stream = None


def _write_text(path, content):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(content)
    os.replace(str(temporary), str(path))


def _status_counts(records):
    counts = {"success": 0, "failed": 0, "blocked": 0}
    for record in records:
        if record["status"] == "SUCCESS":
            counts["success"] += 1
        elif record["status"] == "BLOCKED":
            counts["blocked"] += 1
        else:
            counts["failed"] += 1
    return counts


def _run_status(records):
    if any(record["status"] == "FAILED" for record in records):
        return "FAILED"
    if any(record["status"] == "BLOCKED" for record in records):
        return "BLOCKED"
    return "SUCCESS"


def write_run_summary(run_dir, run_id, cluster, target, state, plugins,
                      records, started, finished, duration_seconds=None):
    """Write machine-readable results without changing per-case reports."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "run_id": run_id,
        "cluster": cluster,
        "target": target or "<auto>",
        "environment": {
            "env_id": state.get("env_id", "-"),
            "plugins": sorted(plugins),
        },
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round(
            (finished - started).total_seconds()
            if duration_seconds is None else duration_seconds, 3),
        "status": _run_status(records),
        "counts": _status_counts(records),
        "cases": records,
    }
    _write_text(run_dir / "summary.json",
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    _write_junit(run_dir / "junit.xml", payload)
    return payload


def _write_junit(path, payload):
    records = payload["cases"]
    suite = ElementTree.Element("testsuite", {
        "name": "%s:%s" % (payload["cluster"], payload["target"]),
        "tests": str(len(records)),
        "failures": str(sum(record["status"] == "FAILED" for record in records)),
        "skipped": str(sum(record["status"] == "BLOCKED" for record in records)),
        "time": "%.3f" % payload["duration_seconds"],
    })
    properties = ElementTree.SubElement(suite, "properties")
    for name, value in (("run_id", payload["run_id"]),
                        ("env_id", payload["environment"]["env_id"])):
        ElementTree.SubElement(properties, "property", {"name": name, "value": value})
    for record in records:
        parts = record["id"].split(".")
        testcase = ElementTree.SubElement(suite, "testcase", {
            "classname": ".".join(parts[:-1]),
            "name": parts[-1],
            "time": "%.3f" % record["duration_seconds"],
        })
        message = record.get("reason", "")
        if record["status"] == "FAILED":
            failure = ElementTree.SubElement(testcase, "failure", {
                "message": message or "case failed",
                "type": "FAILED",
            })
            failure.text = "report: %s" % record["report"]
        elif record["status"] == "BLOCKED":
            skipped = ElementTree.SubElement(testcase, "skipped", {
                "message": message or "case blocked",
            })
            skipped.text = "report: %s" % record["report"]
    tree = ElementTree.ElementTree(suite)
    temporary = path.with_suffix(path.suffix + ".tmp")
    tree.write(str(temporary), encoding="utf-8", xml_declaration=True)
    os.replace(str(temporary), str(path))
