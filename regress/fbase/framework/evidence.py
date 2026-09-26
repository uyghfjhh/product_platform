import os
import re
from pathlib import Path


class CoreCollector(object):
    def __init__(self, manager, output_dir, transport=None):
        self.manager = manager
        self.output_dir = Path(output_dir)
        self.before = {}
        self.transport = transport

    @staticmethod
    def _is_core_file(path):
        name = path.name.lower()
        return bool(re.match(r"^(core($|[._-])|.+\.core($|[._-]))", name))

    def _roots(self):
        roots = [self.output_dir]
        for node in self.manager.nodes.values():
            if self.manager.is_local(node["host"]):
                roots.append(Path(node["data_dir"]))
        return roots

    def _snapshot(self):
        snapshot = {}
        for root in self._roots():
            if not root.is_dir():
                continue
            for directory, _, files in os.walk(str(root)):
                for name in files:
                    path = Path(directory) / name
                    if not self._is_core_file(path):
                        continue
                    try:
                        stat = path.stat()
                        snapshot[str(path.resolve())] = (stat.st_mtime_ns, stat.st_size)
                    except OSError:
                        pass
        if self.transport:
            for node_name, node in self.manager.nodes.items():
                if self.manager.is_local(node["host"]):
                    continue
                process = self.transport.run(
                    node_name,
                    ["find", node["data_dir"], "-type", "f", "(",
                     "-name", "core", "-o", "-name", "core.*", "-o",
                     "-name", "*.core", "-o", "-name", "*.core.*", ")",
                     "-printf",
                     "%p\\t%T@\\t%s\\n"], check=False)
                if process.returncode != 0:
                    continue
                for line in process.stdout.splitlines():
                    try:
                        name, modified, size = line.rsplit("\t", 2)
                    except ValueError:
                        continue
                    path = Path(name)
                    if self._is_core_file(path):
                        snapshot["%s:%s" % (node_name, name)] = (modified, int(size))
        return snapshot

    def start(self):
        self.before = self._snapshot()

    def finish(self):
        after = self._snapshot()
        return sorted(path for path, signature in after.items()
                      if self.before.get(path) != signature)
