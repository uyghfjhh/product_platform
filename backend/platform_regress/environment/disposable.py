"""Ownership-checked temporary PostgreSQL clusters and abandoned resources."""

import errno
import os
import shutil
import socket
import time
from pathlib import Path

from platform_regress.contracts import Blocked


def postmaster_pids(data_dir):
    try:
        lines = (Path(data_dir) / "postmaster.pid").read_text().splitlines()
        return [int(lines[0])] if lines and lines[0].isdigit() else []
    except (OSError, ValueError):
        return []


def postgres_data_dir(pid):
    try:
        argv = (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
    except OSError:
        return None
    for index, argument in enumerate(argv[:-1]):
        if argument == b"-D":
            return Path(argv[index + 1].decode("utf-8", "replace"))
    return None


def reserve_listener(reserved, *, host="127.0.0.1"):
    for port in (*range(20000, 65536), *range(1025, 20000)):
        if str(port) in reserved:
            continue
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((host, port))
        except OSError as exc:
            listener.close()
            if exc.errno == errno.EADDRINUSE:
                continue
            raise Blocked(f"无法探测隔离实例端口 {port}: {exc}") from exc
        return str(port), listener
    raise Blocked("没有可用的隔离实例监听端口")


class DisposablePostgresResources:
    def __init__(self, context, *, owned_prefix, pg_ctl, run):
        self.context = context
        self.owned_prefix = str(Path(owned_prefix).absolute())
        self.pg_ctl, self.run = pg_ctl, run

    def validate(self, path):
        path = Path(path)
        if not str(path.absolute()).startswith(self.owned_prefix) or not str(
            path.resolve()
        ).startswith(self.owned_prefix):
            raise Blocked(f"拒绝清理非框架隔离目录: {path}")
        return path

    def clusters(self, root, registered=()):
        root = self.validate(root)
        candidates = [root]
        candidates.extend(
            child for child in root.iterdir() if child.is_dir()
        ) if root.is_dir() else None
        candidates.extend(Path(path) for path in registered)
        return {
            self.validate(path)
            for path in candidates
            if (path / "PG_VERSION").is_file()
        }

    def stop(self, cluster):
        cluster = self.validate(cluster)
        self.run(
            self.context,
            [self.pg_ctl(), "-D", str(cluster), "stop", "-m", "immediate"],
            timeout=30,
        )
        # Only signal a PID whose actual command owns this exact data directory.
        for pid in postmaster_pids(cluster):
            actual = postgres_data_dir(pid)
            if actual and actual.resolve() == cluster.resolve():
                try:
                    os.kill(pid, 15)
                except ProcessLookupError:
                    pass

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            alive = [
                pid
                for pid in postmaster_pids(cluster)
                if (postgres_data_dir(pid) or Path("/")).resolve() == cluster.resolve()
            ]
            if not alive:
                return
            time.sleep(0.1)
        raise RuntimeError(f"postmaster still owns {cluster}; refusing deletion")

    def remove(self, root, *, registered=(), archive=False, wait=None):
        root = self.validate(root)
        clusters = self.clusters(root, registered)
        for cluster in clusters:
            if archive:
                for name in ("start.log", "postgresql.log", "postgresql.csv"):
                    path = cluster / name
                    if path.is_file():
                        self.context.attach_file(f"{cluster.name}.{name}", path)
            self.stop(cluster)
        if wait:
            wait()
        for cluster in clusters:
            shutil.rmtree(cluster)
        if root.exists():
            shutil.rmtree(root)

    def sweep(self):
        for entry in self.context.ledger.sweep("isolated_cluster"):
            try:
                self.remove(entry["data_dir"])
            except Blocked:
                continue
            self.context.ledger.drop(entry)

    def listener_pids(self, port):
        context = self.context
        returncode, output = self.run(
            context,
            ["lsof", "-nP", "-t", "-iTCP:%s" % port, "-sTCP:LISTEN"],
            timeout=10,
        )
        if returncode not in (0, 1) or (returncode == 1 and output.strip()):
            raise Blocked(f"无法探测监听端口 {port}: {output.strip() or f'lsof exit {returncode}'}")
        return [line.strip() for line in output.splitlines() if line.strip().isdigit()]

    def wait_ports_free(self, ports, timeout=10):
        """Wait for an immediate-stop listener to release a reused test port."""
        ports = tuple(ports)
        if not ports:
            return
        deadline = time.monotonic() + timeout
        escalated = False
        while True:
            occupied = {port: self.listener_pids(port) for port in ports}
            occupied = {port: pids for port, pids in occupied.items() if pids}
            if not occupied:
                return
            if time.monotonic() >= deadline:
                if not escalated:
                    # pg_ctl -m immediate can return before the postmaster lets go;
                    # SIGTERM framework-owned stragglers once, then re-wait.
                    for pids in occupied.values():
                        for pid in pids:
                            data_dir = postgres_data_dir(pid)
                            if data_dir is not None and str(
                                data_dir.resolve()
                            ).startswith(self.owned_prefix):
                                try:
                                    os.kill(int(pid), 15)
                                except OSError:
                                    pass
                    escalated = True
                    deadline = time.monotonic() + 5
                    continue
                details = ", ".join(
                    "%s(pid=%s)" % (port, ",".join(pids))
                    for port, pids in sorted(occupied.items())
                )
                raise RuntimeError(
                    "隔离实例端口在停止后 %ss 仍被监听: %s" % (timeout, details)
                )
            time.sleep(0.1)

    def reclaim_listeners(self, ports):
        """Stop only orphaned framework postmasters occupying this case's ports."""
        for port in ports:
            for pid in self.listener_pids(port):
                data_dir = postgres_data_dir(pid)
                if data_dir is None:
                    raise Blocked(
                        "端口 %s 被 PID %s 占用，但无法确认其 PostgreSQL 数据目录"
                        % (port, pid)
                    )
                if (
                    not str(data_dir.resolve()).startswith(self.owned_prefix)
                    or not (data_dir / "PG_VERSION").is_file()
                ):
                    raise Blocked(
                        "端口 %s 被非本框架隔离实例占用（PID %s，数据目录 %s），拒绝停止"
                        % (port, pid, data_dir)
                    )
                self.remove(data_dir)
        self.wait_ports_free(ports)
