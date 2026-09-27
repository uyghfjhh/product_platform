from framework.postgres import PostgresClient
from framework.process import CommandRunner
from framework.transport import NodeTransport
import re


class TestContext(object):
    def __init__(self, config, manager, case, output_dir, run_id=None):
        self.config = config
        self.manager = manager
        self.case = case
        self.output_dir = output_dir
        self.log_path = output_dir / "execution.log"
        self.command_runner = CommandRunner(self.log_path)
        self.touched_nodes = []
        self._cleanups = []
        self.values = {"run_id": run_id or output_dir.parent.name}
        self.postgres = PostgresClient(manager, self.command_runner, self.touch_node)
        self.transport = NodeTransport(manager, self.command_runner, self.touch_node)

    def resolve_node(self, selector=None):
        name = self.manager.resolve_node(selector or "primary")
        self.touch_node(name)
        return name

    def touch_node(self, node_name):
        if node_name not in self.touched_nodes:
            self.touched_nodes.append(node_name)

    def add_cleanup(self, title, callback, priority=0):
        self._cleanups.append((priority, title, callback))

    def expand(self, value):
        """Expand per-run placeholders in declarative fixtures and steps."""
        if isinstance(value, str):
            expanded = value.replace("{run_id}", self.values["run_id"])
            ports = self.values.get("isolated_mmr_port_mapping") or {}
            if expanded in ports:
                return ports[expanded]
            for declared, allocated in ports.items():
                escaped = re.escape(str(declared))
                expanded = re.sub(r"(port\s*=\s*)%s\b" % escaped,
                                  r"\g<1>%s" % allocated, expanded)
                expanded = re.sub(r"(\B-p\s+)%s\b" % escaped,
                                  r"\g<1>%s" % allocated, expanded)
                expanded = re.sub(r"(:)%s\b" % escaped,
                                  r"\g<1>%s" % allocated, expanded)
            return expanded
        if isinstance(value, list):
            return [self.expand(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.expand(item) for item in value)
        if isinstance(value, dict):
            return {key: self.expand(item) for key, item in value.items()}
        return value

    def cleanup(self):
        errors = []
        cleanups = sorted(self._cleanups, key=lambda item: item[0])
        self._cleanups = []
        while cleanups:
            unused_priority, title, callback = cleanups.pop()
            try:
                callback()
            except Exception as exc:
                errors.append("%s: %s" % (title, exc))
        return errors
