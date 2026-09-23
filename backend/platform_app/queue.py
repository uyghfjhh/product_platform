"""Huey 只负责排队；业务状态以平台 SQLite 中的任务记录为准。"""

from huey import SqliteHuey

from .actions import run_task
from .config import load_settings
from .storage import Store


settings = load_settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
huey = SqliteHuey(
    "product-platform", filename=str(settings.queue_database), results=False
)


@huey.task()
def execute(task_id: str) -> None:
    run_task(Store(settings.database), settings, task_id)
