"""Synchronous CLI execution through the same submission service and worker."""

import signal
import threading

from .actions import run_task
from .operations import OperationService


def run_local_operation(settings, store, request):
    def enqueue(task_id):
        previous = None
        if threading.current_thread() is threading.main_thread():
            previous = signal.signal(signal.SIGINT, lambda *_: store.tasks.request_cancel(task_id))
        try:
            run_task(store, settings, task_id)
        finally:
            if previous is not None:
                signal.signal(signal.SIGINT, previous)
    return OperationService(settings, store, enqueue).submit(request)
