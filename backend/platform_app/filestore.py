"""Composition root for domain repositories; no flat-method compatibility layer."""
from pathlib import Path

from .storage.backend import ConflictError, StorageBackend
from .storage.bindings import BindingsStore
from .storage.deployments import DeploymentsStore
from .storage.diagnoses import DiagnosesStore
from .storage.environments import EnvironmentsStore
from .storage.orchestration import OrchestrationStore
from .storage.results import ResultsStore
from .storage.tasks import TasksStore

__all__ = ["FileStore", "ConflictError"]


class FileStore:
    def __init__(self, data_dir: Path, *, runtime_dir=None, logs_dir=None):
        self.backend = StorageBackend(data_dir, runtime_dir or Path(data_dir).parent / "runtime",
                                      logs_dir or Path(data_dir).parent / "logs")
        self.root, self.runtime_dir, self.logs_dir = self.backend.root, self.backend.runtime_dir, self.backend.logs_dir
        for directory in ("environments", "tasks", "results", "diagnoses", "deployment-requests", "archived-tasks", "submissions"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self.environments = EnvironmentsStore(self.backend, self)
        self.bindings = BindingsStore(self.backend, self)
        self.deployments = DeploymentsStore(self.backend, self)
        self.results = ResultsStore(self.backend, self)
        self.diagnoses = DiagnosesStore(self.backend, self)
        self.tasks = TasksStore(self.backend, self)
        self.orchestration = OrchestrationStore(self.backend)
        self.tasks.recover_event_projections()
        self.tasks.recover_task_index()
        self.tasks.recover_notification_outbox()
