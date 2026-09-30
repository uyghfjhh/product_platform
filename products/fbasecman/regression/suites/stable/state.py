"""JSON runtime state for foreground and background stable runs."""




DEFAULT_STATE = {
    "schema_version": 1, "revision": 0,
    "status": "stopped", "run_id": "", "run_dir": "", "started_at": 0,
    "fbasecman_pid": 0, "monitor_pid": 0, "supervisor_pid": 0,
    "workloads": {}, "commands": {},
}


from platform_regress.persistence.state import JsonStateStore


class StateStore(JsonStateStore):
    def __init__(self, path):
        super().__init__(path, defaults=DEFAULT_STATE, accepted_versions=(0, 1))
