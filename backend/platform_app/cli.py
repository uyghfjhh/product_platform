"""单机平台入口：一个命令启动 API 和本机任务 consumer。"""

import argparse
import sqlite3
import subprocess
import sys

import uvicorn

from .config import load_settings
from .filestore import FileStore, now


def migrate_sqlite(store: FileStore) -> int:
    """把旧 platform.sqlite3 显式迁入文件存储；已存在的记录一律跳过。"""
    marker = store.root / ".sqlite_imported"
    legacy = store.platform_dir / "platform.sqlite3"
    if marker.exists():
        print("已完成过 SQLite 导入（%s），无需重复迁移" % marker.read_text().strip())
        return 0
    if not legacy.is_file():
        print("未发现旧 SQLite 库: %s" % legacy)
        return 0
    try:
        with store._locked():
            skipped = store.import_legacy_sqlite_rows(legacy)
            marker.write_text(now(), encoding="utf-8")
    except (sqlite3.Error, OSError, KeyError, ValueError) as exc:
        print("迁移失败: %s（已导入的记录保留，重跑会继续补缺）" % exc)
        return 1
    print("迁移完成" + ("；跳过已存在记录 %d 条" % skipped if skipped else ""))
    return 0


def recover_unfinished(store: FileStore) -> list[str]:
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
    commands.add_parser("migrate-sqlite", help="将旧 platform.sqlite3 显式迁入文件存储")
    args = parser.parse_args()
    settings = load_settings()
    store = FileStore(settings.data_dir)

    if args.command == "migrate-sqlite":
        return migrate_sqlite(store)

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
