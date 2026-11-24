"""Per-task jemalloc sampling and evidence for a dedicated fbasecman process."""

import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


def binary_digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


ARTIFACTS = {
    "rss.svg": ("RSS 趋势", "image/svg+xml"),
    "heap-diff.png": ("存活堆差分调用图", "image/png"),
    "heap-diff.txt": ("存活堆差分明细", "text/plain"),
    "rss.csv": ("RSS 原始采样", "text/csv"),
}


def tools_for(runtime_dir):
    prefix = runtime_dir / "jemalloc/install"
    library = os.environ.get("PRODUCT_PLATFORM_JEMALLOC_SO")
    if not library:
        library = next((str(path) for path in (
            prefix / "lib/libjemalloc.so.2", Path("/usr/lib64/libjemalloc.so.2"),
            Path("/usr/lib/x86_64-linux-gnu/libjemalloc.so.2"),
            Path("/usr/lib/aarch64-linux-gnu/libjemalloc.so.2"),
        ) if path.is_file()), None)
    local_jeprof = prefix / "bin/jeprof"
    jeprof = os.environ.get("PRODUCT_PLATFORM_JEPROF") or (
        str(local_jeprof) if local_jeprof.is_file() else shutil.which("jeprof")
    )
    dot = shutil.which("dot")
    if not library or not Path(library).is_file() or not jeprof or not shutil.which(jeprof) or not dot:
        raise ValueError("内存排查需要支持 profiling 的 jemalloc、jeprof 和 Graphviz dot")
    return Path(library).resolve(), jeprof, dot


class MemoryProfile:
    def __init__(self, runtime_dir, output, binary, interval):
        self.library, self.jeprof, self.dot = tools_for(runtime_dir)
        self.output = output
        self.binary = binary
        self.binary_sha256 = binary_digest(binary)
        self.interval = interval
        self.output.mkdir(mode=0o700)
        self.env = {
            **os.environ,
            "LD_PRELOAD": " ".join(filter(None, (str(self.library), os.environ.get("LD_PRELOAD")))),
            "MALLOC_CONF": (f"prof:true,lg_prof_sample:19,lg_prof_interval:{interval},"
                            f"prof_prefix:{self.output / 'heap'}"),
        }
        with tempfile.TemporaryDirectory(dir=self.output) as directory:
            probe = Path(directory)
            env = {**self.env, "MALLOC_CONF": f"prof:true,prof_final:true,prof_prefix:{probe / 'check'}"}
            subprocess.run(["/bin/true"], env=env, capture_output=True, check=True, timeout=5)
            if not list(probe.glob("check.*.f.heap")):
                raise ValueError("当前 jemalloc 无法生成 heap 快照，请使用启用了 --enable-prof 的构建")
        self.pid = None
        self.rows = []
        self.next_sample = 0
        self.started = time.monotonic()

    def attach(self, pid_file):
        self.pid = int(pid_file.read_text().strip())
        maps = Path(f"/proc/{self.pid}/maps").read_text()
        if str(self.library) not in maps:
            raise RuntimeError("被测代理未加载指定的 jemalloc")
        self.started = time.monotonic()
        self.sample(force=True)
        print(f"jemalloc 已加载，代理 PID {self.pid}，平均采样间隔 512 KiB", flush=True)

    def snapshots(self):
        pattern = re.compile(rf"heap\.{self.pid}\.(\d+)\.i\d+\.heap")
        matches = []
        for path in self.output.glob(f"heap.{self.pid}.*.heap"):
            match = pattern.fullmatch(path.name)
            if match and path.stat().st_size:
                matches.append((int(match[1]), path))
        return [path for _, path in sorted(matches)]

    def sample(self, force=False):
        if not self.pid:
            return
        now = time.monotonic()
        if not force and now < self.next_sample:
            return
        self.next_sample = now + 5
        try:
            status = Path(f"/proc/{self.pid}/status").read_text()
        except FileNotFoundError:
            return
        rss = re.search(r"(?m)^VmRSS:\s+(\d+)\s+kB$", status)
        if rss:
            self.rows.append((round(now - self.started, 3), int(rss[1])))
            with (self.output / "rss.csv").open("a", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                if len(self.rows) == 1:
                    writer.writerow(("elapsed_seconds", "rss_kib"))
                writer.writerow(self.rows[-1])
        # Keep the baseline and most recent snapshots. An intermediate
        # snapshot is only pruned after later dumps have been observed.
        for path in self.snapshots()[1:-12]:
            path.unlink(missing_ok=True)

    def finish(self):
        snapshots = self.snapshots()
        result = {
            "pid": self.pid, "samples": len(self.rows), "snapshots": len(snapshots),
            "sampling_bytes": 2**19, "dump_interval_bytes": 2**self.interval,
            "baseline": None, "later": None, "rss_change_mib": None, "issue": None,
            "baseline_to_later_seconds": None,
            "binary_sha256": self.binary_sha256,
        }
        if self.rows:
            (self.output / "rss.svg").write_text(rss_svg(self.rows), encoding="utf-8")
            result["rss_change_mib"] = round((self.rows[-1][1] - self.rows[0][1]) / 1024, 3)
        if len(snapshots) < 2:
            result["issue"] = "同一代理进程运行期间的 heap 快照少于两份，无法生成差分。可延长压测或降低导出间隔。"
        else:
            baseline, later = snapshots[0], snapshots[-1]
            result.update(baseline=baseline.name, later=later.name,
                          baseline_to_later_seconds=round(later.stat().st_mtime - baseline.stat().st_mtime, 3))
            try:
                if binary_digest(self.binary) != self.binary_sha256:
                    raise ValueError("采集期间被测二进制发生变更，无法可靠解析符号")
                self.diff(baseline, later)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                result["issue"] = f"堆差分生成失败：{exc}"
        temporary = self.output / "summary.tmp"
        temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.output / "summary.json")
        if result["issue"]:
            print(result["issue"], flush=True)
        print(f"内存诊断证据：{self.output}", flush=True)

    def diff(self, baseline, later):
        args = [self.jeprof, "--inuse_space", "--show_bytes", f"--base={baseline}"]
        evidence = self.output / "jeprof.log"
        with evidence.open("w", encoding="utf-8") as error:
            summary = subprocess.check_output(
                [*args, "--text", self.binary, later], stderr=error, timeout=45,
            )
            (self.output / "heap-diff.txt").write_bytes(summary)
            graph = subprocess.check_output(
                [*args, "--dot", self.binary, later], stderr=error, timeout=45,
            )
            png = subprocess.check_output([self.dot, "-Tpng"], input=graph, stderr=error, timeout=45)
            (self.output / "heap-diff.png").write_bytes(png)


def rss_svg(rows):
    low, high = min(v for _, v in rows), max(v for _, v in rows)
    elapsed = max(1, rows[-1][0])
    span = max(1024, high - low)
    points = " ".join(f"{70 + t / elapsed * 795:.2f},{284 - (v - low) / span * 222:.2f}"
                      for t, v in rows)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 920 338">
<rect width="920" height="338" fill="#fff"/>
<g font-family="sans-serif" fill="#273548">
<text x="70" y="30" font-size="18">fbasecman RSS (MiB)</text>
<text x="63" y="68" text-anchor="end" font-size="12">{(low + span) / 1024:.2f}</text>
<text x="63" y="288" text-anchor="end" font-size="12">{low / 1024:.2f}</text>
<text x="70" y="310" font-size="12">0 s</text>
<text x="865" y="310" text-anchor="end" font-size="12">{rows[-1][0]:.1f} s</text>
</g>
<path d="M70 62V284H865" fill="none" stroke="#9ba9b9"/>
<polyline points="{points}" fill="none" stroke="#087f8c" stroke-width="2"/>
</svg>'''
