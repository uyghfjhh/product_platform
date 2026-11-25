"""Symbolized jemalloc snapshots and signed comparisons of complete stacks."""

import csv
import hashlib
import json
import re
import subprocess
import time

SAMPLE_NAME = re.compile(r"heap\.(\d+)\.(\d+)\.i\d+\.heap")
MIB = 1024**2


def write_json(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def collapsed_stacks(text):
    stacks = {}
    for line in text.splitlines():
        trace, delimiter, count = line.rpartition(" ")
        if not delimiter or not count.isdecimal() or not trace:
            continue
        frames = trace.split(";")
        identity = hashlib.sha256(trace.encode()).hexdigest()
        if identity in stacks:
            stacks[identity]["bytes"] += int(count)
        else:
            stacks[identity] = {"frames": frames, "bytes": int(count)}
    if text.strip() and not stacks:
        raise ValueError("jeprof 未返回可解析的完整分配栈")
    return stacks


def index(directory):
    path = directory / "timeline.json"
    if not path.is_file():
        return {"samples": [], "issue": None}
    return json.loads(path.read_text(encoding="utf-8"))


def measurement_start(directory):
    path = directory / "measurement.json"
    return json.loads(path.read_text(encoding="utf-8"))["start"] if path.is_file() else None


def collect(directory, snapshots, jeprof, binary, binary_sha256, started_epoch, finished=False):
    from products.fbasecman.longrun_memory import binary_digest

    history = index(directory)
    try:
        if binary_digest(binary) != binary_sha256:
            raise ValueError("被测二进制已变更，已暂停解析新快照")
        if not finished:
            # Do not open a dump while jemalloc is still writing it.
            snapshots = [path for path in snapshots if time.time() - path.stat().st_mtime >= 2]
        if not snapshots:
            return
        seen = {sample["id"] for sample in history["samples"]}
        boundary = measurement_start(directory)
        baseline = next((path for path in snapshots
                         if boundary is not None and path.stat().st_mtime - started_epoch >= boundary), None)
        for path in dict.fromkeys(path for path in (snapshots[0], baseline, snapshots[-1]) if path):
            match = SAMPLE_NAME.fullmatch(path.name)
            if not match or match[2] in seen:
                continue
            trace = subprocess.check_output(
                [jeprof, "--inuse_space", "--show_bytes", "--collapsed", binary, path],
                stderr=subprocess.PIPE, text=True, timeout=25,
            )
            stacks = collapsed_stacks(trace)
            write_json(directory / f"stacks-{match[2]}.json", stacks)
            elapsed = max(0, path.stat().st_mtime - started_epoch)
            history["samples"].append({"id": match[2], "elapsed": elapsed,
                                       "bytes": sum(item["bytes"] for item in stacks.values())})
        history["samples"].sort(key=lambda item: int(item["id"]))
        history["issue"] = None
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        history["issue"] = f"最近一次 heap 解析失败：{exc}"
    # Preserve the baseline, one checkpoint per hour, and recent detail.
    samples = history["samples"]
    if len(samples) > 120:
        checkpoints = {}
        for sample in samples[:-120]:
            checkpoints.setdefault(int(sample["elapsed"] // 3600), sample)
        boundary = measurement_start(directory)
        baseline = next((sample for sample in samples
                         if boundary is not None and sample["elapsed"] >= boundary), None)
        keep = {sample["id"]: sample for sample in [*checkpoints.values(), *samples[-120:]]}
        if baseline:
            keep[baseline["id"]] = baseline
        history["samples"] = sorted(keep.values(), key=lambda sample: int(sample["id"]))
    write_json(directory / "timeline.json", history)
    keep_ids = {sample["id"] for sample in history["samples"]}
    for path in directory.glob("stacks-*.json"):
        if path.stem.removeprefix("stacks-") not in keep_ids:
            path.unlink(missing_ok=True)


def rss_series(directory):
    source = directory / "rss.csv"
    if not source.is_file():
        return []
    with source.open(encoding="utf-8", newline="") as handle:
        rows = [(float(row["elapsed_seconds"]), int(row["rss_kib"]) / 1024)
                for row in csv.DictReader(handle) if row.get("elapsed_seconds") and row.get("rss_kib")]
    if len(rows) <= 1200:
        return rows
    step = (len(rows) + 599) // 600
    result = [rows[0]]
    for start in range(1, len(rows) - 1, step):
        bucket = rows[start:min(start + step, len(rows) - 1)]
        result.extend(sorted({min(bucket, key=lambda item: item[1]), max(bucket, key=lambda item: item[1])}))
    return [*result, rows[-1]]


def load_stacks(directory, sample_id):
    if not re.fullmatch(r"[0-9]+", sample_id):
        raise ValueError("heap 快照标识无效")
    try:
        return json.loads((directory / f"stacks-{sample_id}.json").read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("heap 快照已轮转，请重新选择对比区间") from exc


def display_frame(frames):
    for frame in reversed(frames):
        if not frame.startswith(("prof_", "je_", "malloc", "calloc", "realloc")):
            return frame
    return frames[-1]


def compare(directory, start=None, end=None, stack=None, query="", direction="growth", excluded=()):
    samples = index(directory)["samples"]
    if len(samples) < 2:
        return {"rows": [], "selected": None, "tree": [], "matched": 0}
    by_id = {sample["id"]: sample for sample in samples}
    if start is None:
        boundary = measurement_start(directory)
        eligible = [sample for sample in samples if boundary is None or sample["elapsed"] >= boundary]
        if len(eligible) < 2:
            return {"rows": [], "selected": None, "tree": [], "matched": 0}
        start = eligible[0]["id"]
    end = end or samples[-1]["id"]
    if start not in by_id or end not in by_id or int(start) >= int(end):
        raise ValueError("请选择两个按时间排列的有效 heap 快照")
    baseline, later = load_stacks(directory, start), load_stacks(directory, end)
    changes = []
    for identity in (baseline.keys() | later.keys()).difference(excluded):
        first = baseline.get(identity, {}).get("bytes", 0)
        last = later.get(identity, {}).get("bytes", 0)
        delta = last - first
        if (direction == "growth" and delta <= 0) or (direction == "reduction" and delta >= 0):
            continue
        frames = (later.get(identity) or baseline[identity])["frames"]
        if query and not any(query.casefold() in frame.casefold() for frame in frames):
            continue
        label = display_frame(frames)
        position = len(frames) - 1 - frames[::-1].index(label)
        changes.append({"id": identity, "label": label, "frames": frames,
                        "callers": frames[max(0, position - 2):position],
                        "before": first, "after": last, "delta": delta})
    changes.sort(key=lambda row: (-abs(row["delta"]), row["id"]))
    visible = changes[:50]
    selected = next((row for row in changes if row["id"] == stack), None) or (visible[0] if visible else None)
    if selected:
        series = []
        for sample in samples:
            try:
                current = load_stacks(directory, sample["id"])
            except ValueError:
                continue  # A live retention pass can rotate an old point.
            series.append((sample["elapsed"], current.get(selected["id"], {}).get("bytes", 0)))
        selected = {**selected, "series": series, "locations": source_locations(directory, selected["frames"])}
    return {"start": start, "end": end, "rows": [{key: value for key, value in row.items() if key != "frames"}
                                                 for row in visible],
            "selected": selected, "tree": build_tree(visible[:10]), "matched": len(changes)}


def build_tree(rows):
    root = {}
    for row in rows:
        children = root
        for frame in row["frames"]:
            node = children.setdefault(frame, {"frame": frame, "bytes": 0, "children": {}})
            node["bytes"] += abs(row["delta"])
            children = node["children"]
        node.setdefault("stack", row["id"])

    def ordered(children):
        return [{**node, "children": ordered(node["children"])}
                for node in sorted(children.values(), key=lambda item: (-item["bytes"], item["frame"]))]

    return ordered(root)


def source_locations(directory, frames):
    # The addresses emitted with collapsed symbols are function entry
    # addresses, so present these as definition locations, not call sites.
    path = directory / "binary.json"
    if not path.is_file() or not frames:
        return {}
    from products.fbasecman.longrun_memory import binary_digest

    try:
        binary = json.loads(path.read_text(encoding="utf-8"))
        if binary_digest(binary["path"]) != binary["sha256"]:
            return {}
        addresses = {frame: match[1] for frame in frames
                     if (match := re.search(r"<([0-9a-fA-F]+)>$", frame))}
        if not addresses:
            return {}
        locations = subprocess.check_output(
            ["addr2line", "-e", binary["path"], *("0x" + addr for addr in addresses.values())],
            text=True, stderr=subprocess.DEVNULL, timeout=3,
        ).splitlines()
        return {frame: location for frame, location in zip(addresses, locations, strict=True)
                if location and not location.startswith("??")}
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return {}
