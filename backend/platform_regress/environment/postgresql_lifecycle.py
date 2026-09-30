"""PostgreSQL node actions shared by regression fixtures."""

from platform_regress import steps


class PostgresLifecycle:
    def __init__(self, context, *, run=None, operation_error=RuntimeError):
        self.context, self.operation_error = context, operation_error
        self.run = run or (
            lambda ctx, argv, **kwargs: ctx.command(argv, merge_stderr=True)
        )

    def node_order(self):
        return steps._managed_node_order(self.context)

    def node_action(self, node, action, *, check=True):
        endpoint = self.context.node_endpoint(node)
        argv = steps._pg_ctl_argv(self.context, endpoint, action)
        result = self.run(self.context, argv, check=False)
        if check and result.returncode:
            raise self.operation_error(
                "命令执行失败(%s): %s\n%s"
                % (result.returncode, " ".join(argv), result.stdout.rstrip())
            )
        return result

    def apply(self, action):
        order = self.node_order()
        if action == "reload":
            for node in order:
                self.node_action(node, "reload")
        elif action == "start":
            for node in order:
                if self.node_action(node, "status", check=False).returncode:
                    self.node_action(node, "start")
        elif action == "stop":
            for node in reversed(order):
                if not self.node_action(node, "status", check=False).returncode:
                    self.node_action(node, "stop")
        elif action == "restart":
            for node in order:
                status = self.node_action(node, "status", check=False).returncode
                self.node_action(node, "start" if status else "restart")
        else:
            raise ValueError(f"unsupported PostgreSQL action: {action}")
