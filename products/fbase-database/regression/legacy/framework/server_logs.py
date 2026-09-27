from pathlib import Path


class ServerLogCollector:
    DESTINATIONS = {
        "stderr": "postgresql.log",
        "csvlog": "postgresql.csv",
    }

    def __init__(self, manager, node_name, case_dir, label=None, transport=None):
        self.manager = manager
        self.node_name = node_name
        self.case_dir = Path(case_dir)
        self.data_dir = Path(manager.nodes[node_name]["data_dir"])
        self.before = {}
        self.errors = []
        self.label = label
        self.transport = transport

    def _output_name(self, output_name):
        if not self.label:
            return output_name
        path = Path(output_name)
        return "%s.%s%s" % (path.stem, self.label, path.suffix)

    def start(self):
        for output_name in self.DESTINATIONS.values():
            path = self.case_dir / self._output_name(output_name)
            if path.exists():
                path.unlink()
        self.before = self._snapshot()

    def finish(self, allow_missing_before=False):
        after = self._snapshot(ignore_errors=allow_missing_before)
        evidence = []
        for destination, output_name in self.DESTINATIONS.items():
            output_name = self._output_name(output_name)
            segments = []
            before_path, before_size = self.before.get(destination, (None, 0))
            after_path, unused_size = after.get(destination, (None, 0))
            if before_path:
                segments.append((before_path, before_size))
            if after_path and after_path != before_path:
                segments.append((after_path, 0))
            content = b""
            for path, offset in segments:
                try:
                    if self.manager.is_local(self.manager.nodes[self.node_name]["host"]):
                        with path.open("rb") as stream:
                            stream.seek(offset)
                            content += stream.read()
                    elif self.transport:
                        process = self.transport.run(
                            self.node_name,
                            ["tail", "-c", "+%s" % (offset + 1), str(path)],
                            check=False)
                        if process.returncode != 0:
                            raise OSError(process.stdout.strip() or "远端日志读取失败")
                        content += process.stdout.encode("utf-8")
                except OSError as exc:
                    if not (allow_missing_before and path == before_path):
                        self.errors.append("%s: %s" % (path, exc))
            if content:
                (self.case_dir / output_name).write_bytes(content)
                evidence.append(output_name)
        return evidence

    def _snapshot(self, ignore_errors=False):
        result = {}
        for destination in self.DESTINATIONS:
            try:
                relative = self.manager.query_value(
                    self.node_name, "postgres",
                    "SELECT coalesce(pg_current_logfile('%s'), '')" % destination,
                )
                if not relative:
                    continue
                path = Path(relative)
                if not path.is_absolute():
                    path = self.data_dir / path
                if self.manager.is_local(self.manager.nodes[self.node_name]["host"]):
                    size = path.stat().st_size
                elif self.transport:
                    process = self.transport.run(
                        self.node_name, ["stat", "-c", "%s", str(path)], check=False)
                    if process.returncode != 0:
                        raise OSError(process.stdout.strip() or "远端日志 stat 失败")
                    size = int(process.stdout.strip())
                else:
                    continue
                result[destination] = (path, size)
            except Exception as exc:
                if not ignore_errors:
                    self.errors.append("%s: %s" % (destination, exc))
        return result
