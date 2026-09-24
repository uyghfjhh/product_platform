try:
    import fcntl
except ImportError:
    fcntl = None
import os
from pathlib import Path


class ExclusiveFileLock(object):
    def __init__(self, path, description):
        self.path = Path(path)
        self.description = description
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        if fcntl is not None:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.handle.close()
                self.handle = None
                raise RuntimeError("%s is already running (lock: %s)" % (self.description, self.path))
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write("%s\n" % os.getpid())
        self.handle.flush()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.handle is not None:
            if fcntl is not None:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None
