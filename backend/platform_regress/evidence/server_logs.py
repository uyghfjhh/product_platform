"""Node log snapshots and rotation-aware incremental evidence collection."""

import os
from pathlib import Path

from platform_regress.execution.remote import RemoteExecutor, RemoteTarget


class ServerLogCollector:
    def __init__(
        self,
        context,
        node,
        case_dir,
        *,
        current_log,
        destinations,
        label=None,
        remote_target=None,
    ):
        self.context, self.node = context, node
        self.case_dir = Path(case_dir)
        endpoint = context.node_endpoint(node)
        self.data_dir = Path(str(endpoint.get("data_dir") or ""))
        self.host = str(endpoint.get("host", ""))
        self.current_log, self.destinations = current_log, dict(destinations)
        self.label, self.before, self.errors, self.finished = label, {}, [], False
        self._handles = {}
        if not context.is_local(self.host):
            target = remote_target or RemoteTarget(
                self.host,
                context.environment.get("ssh_user")
                or os.environ.get("USER")
                or "postgres",
                context.environment.get("ssh_port") or 22,
            )
            self.remote = RemoteExecutor(context, target)
        else:
            self.remote = None

    def _output_name(self, name):
        path = Path(name)
        return f"{path.stem}.{self.label}{path.suffix}" if self.label else name

    def _snapshot(self, ignore_errors=False):
        result = {}
        for destination in self.destinations:
            try:
                relative = self.current_log(self.context, self.node, destination)
                if not relative:
                    continue
                path = Path(relative)
                if not path.is_absolute():
                    path = self.data_dir / path
                if self.remote:
                    device, inode, size = self.remote.stat(path)
                else:
                    stat = path.stat()
                    device, inode, size = stat.st_dev, stat.st_ino, stat.st_size
                result[destination] = (path, size, (device, inode))
            except Exception as exc:
                if not ignore_errors:
                    self.errors.append(f"{destination}: {exc}")
        return result

    def start(self):
        self.before = self._snapshot()
        if not self.remote:
            for destination, (path, size, identity) in self.before.items():
                try:
                    handle = path.open("rb")
                    stat = os.fstat(handle.fileno())
                    self.before[destination] = (
                        path,
                        stat.st_size,
                        (stat.st_dev, stat.st_ino),
                    )
                    self._handles[destination] = handle
                except OSError as exc:
                    self.errors.append(f"{destination}: {exc}")
            self.context.defer_cleanup(self.close)

    def close(self):
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()

    def finish(self, allow_missing_before=False):
        if self.finished:
            return []
        self.finished = True
        try:
            return self._collect(allow_missing_before)
        finally:
            self.close()

    def _collect(self, allow_missing_before):
        after = self._snapshot(ignore_errors=allow_missing_before)
        evidence = []
        for destination, name in self.destinations.items():
            before_path, before_size, before_identity = self.before.get(
                destination, (None, 0, None)
            )
            after_path, after_size, after_identity = after.get(
                destination, (None, 0, None)
            )
            content = bytearray()
            same_file = before_identity == after_identity
            try:
                if before_path:
                    handle = self._handles.get(destination)
                    if handle is not None:
                        size = os.fstat(handle.fileno()).st_size
                        handle.seek(before_size if size >= before_size else 0)
                        content.extend(handle.read())
                    elif self.remote:
                        if after_path == before_path and not same_file:
                            content.extend(
                                self.remote.tail_identity(
                                    before_path, before_size, before_identity
                                )
                            )
                        else:
                            offset = (
                                before_size
                                if after_path != before_path
                                or after_size >= before_size
                                else 0
                            )
                            content.extend(self.remote.tail(before_path, offset))
            except OSError as exc:
                if not allow_missing_before:
                    self.errors.append(f"{before_path}: {exc}")
            if after_path and (not before_path or not same_file):
                try:
                    if self.remote:
                        content.extend(self.remote.tail(after_path, 0))
                    else:
                        content.extend(after_path.read_bytes())
                except OSError as exc:
                    self.errors.append(f"{after_path}: {exc}")
            if content:
                evidence.append(
                    self.context.attach_bytes(self._output_name(name), bytes(content))
                )
        return evidence
