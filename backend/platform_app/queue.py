"""Huey 只负责排队；业务状态以平台文件存储中的任务记录为准。"""

import fcntl
import os
import threading

from huey import FileHuey
from huey.storage import FileStorage

from .actions import run_task
from .config import load_settings
from .filestore import FileStore


class _FileLock:
    """基于 flock 的互斥锁，每次 acquire 独立 fd、线程内持有。

    huey.storage.FileLock 把所有 acquire 的 fd 写在共享的 ``self.fd``
    上：并发 worker 线程互相覆盖，导致先取得的锁永远不被释放（死锁）。
    其 ``__init__`` 还会 unlink 锁文件，在两个进程先后初始化 storage
    时把互斥分裂到不同 inode 上。这里 fd 存 ``threading.local``，
    acquire/release 严格配对，且不在初始化时删除锁文件。
    """

    def __init__(self, filename):
        self.filename = filename
        self._local = threading.local()
        dirname = os.path.dirname(filename)
        if not os.path.exists(dirname):
            os.makedirs(dirname)

    def acquire(self):
        fd = os.open(self.filename,
                     os.O_CREAT | os.O_TRUNC | os.O_RDWR, 0o777)
        fcntl.flock(fd, fcntl.LOCK_EX)
        self._local.fd = fd
        return self

    def release(self):
        fd = getattr(self._local, "fd", None)
        self._local.fd = None
        if fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


class _FileStorage(FileStorage):
    """FileStorage with a lock that is safe under thread workers."""

    def __init__(self, name, path, **kwargs):
        # use_thread_lock=True 让父类不构造 FileLock（其 __init__ 会
        # unlink 现有锁文件导致多进程 inode 分裂），随后换成本模块的
        # 每线程独立 fd 实现，进程间仍由 flock 互斥。
        kwargs["use_thread_lock"] = True
        super().__init__(name, path, **kwargs)
        self.lock = _FileLock(os.path.join(self.path, ".lock"))


settings = load_settings()
settings.platform_dir.mkdir(parents=True, exist_ok=True)
huey = FileHuey(
    "product-platform", path=str(settings.platform_dir / "queue"),
    results=False, storage_class=_FileStorage,
)


@huey.task()
def execute(task_id: str) -> None:
    run_task(FileStore(settings.data_dir), settings, task_id)
