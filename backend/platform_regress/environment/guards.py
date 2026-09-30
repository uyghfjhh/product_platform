"""Restoration guards register recovery before any attempted mutation."""

from pathlib import Path


class FileRestoreGuard:
    def __init__(
        self, context, path, *, after_restore=None, priority=90, title=None, defer=None
    ):
        self.path = Path(path)
        self.before = self.path.read_bytes()
        self.after_restore = after_restore
        callback = self.restore
        if defer:
            defer(
                context,
                title or f"Restore {self.path.name}",
                callback,
                priority=priority,
            )
        else:
            context.defer_cleanup(callback, priority=priority)

    def restore(self):
        self.path.write_bytes(self.before)
        if self.after_restore:
            self.after_restore()

    def write_text(self, text):
        self.path.write_text(text, encoding="utf-8")


def system_clock_guard(context, *, run, defer, key="system_clock"):
    now = run(context, ["date", "+%s"]).stdout.strip()
    ntp = run(
        context, ["timedatectl", "show", "-p", "NTP", "--value"], check=False
    ).stdout.strip()
    if not now.isdigit():
        raise RuntimeError("无法读取当前系统时间")
    state = {"epoch": now, "ntp": ntp, "changed": False}
    context.values[key] = state

    def restore():
        if state["changed"]:
            run(context, ["sudo", "-n", "timedatectl", "set-ntp", "false"])
            run(context, ["sudo", "-n", "date", "-s", "@" + state["epoch"]])
            if state["ntp"] in ("yes", "true", "1"):
                run(context, ["sudo", "-n", "timedatectl", "set-ntp", "true"])

    defer(context, "恢复系统时间和 NTP 状态", restore, priority=200)
    return state
