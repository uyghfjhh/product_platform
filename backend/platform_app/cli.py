"""单机平台入口：一个命令启动 API 和本机任务 consumer。"""

import argparse
import subprocess
import sys

import uvicorn

from .config import load_settings
from .storage import Store


def recover_unfinished(store: Store) -> list[str]:
    """重启后只重投未领取任务；执行中任务须先核对外部进程。"""
    queued = []
    for task in store.unfinished_tasks():
        if task["status"] == "QUEUED":
            queued.append(task["id"])
        else:
            store.transition_task(task["id"], ("RUNNING", "CANCELLING"),
                                  "RECOVERY_REQUIRED", reason="平台重启；请核对外部操作状态")
    return queued


def main() -> int:
    parser = argparse.ArgumentParser(prog="product-platform")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="启动平台与后台任务进程")
    start.add_argument("--host", default="0.0.0.0")
    start.add_argument("--port", type=int, default=8080)
    commands.add_parser("check", help="检查本地存储和产品集成")
    args = parser.parse_args()
    settings = load_settings()
    store = Store(settings.database)

    if args.command == "check":
        print("SQLite: %s" % store.path)
        for name, root, script in (
            ("pgcluster", settings.pgcluster_root, "pgcluster"),
            ("fbasecman regress", settings.fbasecman_regress_root, "run.sh"),
            ("FBase regress", settings.fbase_regress_root, "run.sh"),
        ):
            print("%s: %s" % (name, "可用" if (root / script).is_file() else "未找到"))
        return 0

    queued = recover_unfinished(store)
    consumer = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "huey.bin.huey_consumer",
            "platform_app.queue.huey",
            "-w",
            "2",
            "-k",
            "thread",
            "-m",
            "0.5",
        ]
    )
    if queued:
        from .queue import execute
        for task_id in queued:
            execute(task_id)
    try:
        uvicorn.run("platform_app.api:app", host=args.host, port=args.port)
    finally:
        consumer.terminate()
        try:
            consumer.wait(timeout=5)
        except subprocess.TimeoutExpired:
            consumer.kill()
            consumer.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
