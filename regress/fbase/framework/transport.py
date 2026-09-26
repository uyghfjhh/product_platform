import os
import shlex


class NodeTransport(object):
    """Run case commands locally or through the system ssh client."""

    def __init__(self, manager, command_runner, touch_node=None):
        self.manager = manager
        self.command_runner = command_runner
        self.touch_node = touch_node or (lambda unused: None)

    def run(self, node_name, argv, check=False, input_text=None, cwd=None, env=None,
            timeout=None):
        node = self.manager.node(node_name)
        self.touch_node(node_name)
        argv = [str(item) for item in argv]
        if self.manager.is_local(node["host"]):
            if cwd or env:
                command = self._shell_command(argv, cwd, env)
                return self.command_runner.run(
                    ["sh", "-lc", command], check=check, input_text=input_text,
                    timeout=timeout)
            return self.command_runner.run(
                argv, check=check, input_text=input_text, timeout=timeout)

        transport = self.manager.cluster.get("transport") or {}
        user = transport.get("ssh_user") or os.environ.get("USER", "postgres")
        port = transport.get("ssh_port", 22)
        remote = self._shell_command(argv, cwd, env)
        return self.command_runner.run([
            "ssh", "-p", str(port), "%s@%s" % (user, node["host"]), remote,
        ], check=check, input_text=input_text, timeout=timeout)

    @staticmethod
    def _shell_command(argv, cwd, env):
        parts = []
        if cwd:
            parts.extend(["cd", shlex.quote(str(cwd)), "&&"])
        if env:
            parts.append("env")
            parts.extend("%s=%s" % (key, shlex.quote(str(value)))
                         for key, value in sorted(env.items()))
        parts.extend(shlex.quote(value) for value in argv)
        return " ".join(parts)
