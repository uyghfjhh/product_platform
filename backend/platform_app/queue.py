"""Huey 只负责排队；业务状态以平台文件存储中的任务记录为准。"""

from huey import FileHuey

from .actions import run_task
from .config import load_settings
from .filestore import FileStore


settings = load_settings()
settings.platform_dir.mkdir(parents=True, exist_ok=True)
huey = FileHuey(
    "product-platform", path=str(settings.platform_dir / "queue"),
    results=False,
)


@huey.task()
def execute(task_id: str) -> None:
    run_task(FileStore(settings.data_dir), settings, task_id)
