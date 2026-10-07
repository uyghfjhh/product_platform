"""单机平台入口：一个命令启动 API 和本机任务 consumer。"""

import argparse
import signal
import subprocess
import sys
from pathlib import Path

import uvicorn

from .config import load_settings
from .filestore import FileStore


def recover_unfinished(store: FileStore) -> list[str]:
    """重启后只重投未领取任务；执行中任务须先核对外部进程。"""
    queued = []
    for task in store.tasks.unfinished_tasks():
        if task["status"] == "QUEUED":
            queued.append(task["id"])
        else:
            store.tasks.transition_task(task["id"], ("RUNNING", "CANCELLING"),
                                  "RECOVERY_REQUIRED", reason="平台重启；请核对外部操作状态")
    return queued


def main() -> int:
    parser = argparse.ArgumentParser(prog="product-platform")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="启动平台与后台任务进程")
    start.add_argument("--host", default="0.0.0.0")
    start.add_argument("--port", type=int, default=8080)
    commands.add_parser("check", help="检查本地存储和产品集成")
    key_import = commands.add_parser("import-license-keys", help="将旧版签发密钥迁入平台本地数据目录")
    key_import.add_argument("--source", required=True, type=Path)
    archive = commands.add_parser("archive", help="归档已结束任务，保留证据和幂等记录")
    archive.add_argument("--older-than-days", type=int, default=30)
    args = parser.parse_args()
    settings = load_settings()
    if args.command == "import-license-keys":
        from .license import import_legacy_keys

        versions = import_legacy_keys(settings, args.source)
        print("已导入 License 密钥版本: %s" % ", ".join(versions))
        return 0
    store = FileStore(settings.data_dir, runtime_dir=settings.runtime_dir, logs_dir=settings.logs_dir)

    if args.command == "archive":
        print("归档任务:", len(store.tasks.archive_tasks(args.older_than_days)))
        return 0

    if args.command == "check":
        from .product_catalog import discover_products

        print("数据目录: %s" % store.root)
        checks = [("pgcluster", settings.pgcluster_root, "pgcluster")]
        for product_id in sorted(discover_products(settings.products_root)):
            checks.append(
                ("%s regress" % product_id,
                 settings.product_regress_root(product_id), "regress.yaml")
            )
        for name, root, script in checks:
            print("%s: %s" % (name, "可用" if (root / script).is_file() else "未找到"))
        return 0

    queued = recover_unfinished(store)
    from .api import create_app
    from .queue import create_queue

    _huey, execute = create_queue(settings, store)
    for task_id in queued:
        execute(task_id)
    consumer = subprocess.Popen([sys.executable, "-m", "platform_app.worker"])
    # Uvicorn re-raises SIGTERM after its shutdown. The default handler would
    # terminate Python before our finally block, leaving a stale queue worker.
    previous_terminate = signal.getsignal(signal.SIGTERM)
    def terminate(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    try:
        app = create_app(settings, enqueuer=execute)
        uvicorn.run(app, host=args.host, port=args.port)
    except KeyboardInterrupt:
        pass
    finally:
        consumer.terminate()
        try:
            consumer.wait(timeout=5)
        except subprocess.TimeoutExpired:
            consumer.kill()
            consumer.wait()
        signal.signal(signal.SIGTERM, previous_terminate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
