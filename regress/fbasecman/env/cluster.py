from functools import wraps

from framework.configuration import RegressionConfig
from framework.execution.locking import ExclusiveFileLock

from .cleanup import clean_environment
from .context import collect_test_context
from .health import check_environment
from .inventory import collect_inventory
from .jdbc import prepare_jdbc
from .license import check_license_dir
from .mmr import setup_mmr_topology
from .postgres import restart_environment_postgres, start_environment_postgres, stop_environment_postgres
from framework.execution.shell import LoggedShellRunner
from .state import EnvState, StateStore
from .ownership import authorize_environment, build_cleanup_plan
from framework.configuration import validate_profile_isolation


class Step:
    def __init__(self, name, method_name):
        self.name = name
        self.method_name = method_name


def exclusive_environment_operation(method):
    """Serialize destructive operations for one configured environment only."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        if kwargs.get("dry_run", False):
            return method(self, *args, **kwargs)
        lock = self.env.env_output_dir / "environment.lock"
        description = "%s environment %s" % (self.env.profile, method.__name__)
        with ExclusiveFileLock(lock, description):
            return method(self, *args, **kwargs)
    return wrapped


class ClusterManager:
    def __init__(self, env: RegressionConfig, verbose=True):
        self.env = env
        timeout = env.config.get("framework", {}).get("default_timeout", 60)
        self.runner = LoggedShellRunner(
            env.env_logs_dir, verbose=verbose, default_timeout=timeout,
        )
        self.store = StateStore(env)
        self.steps = [
            Step("preflight", "_preflight"),
            Step("clean_previous", "_clean_previous"),
            Step("setup_mmr_topology", "_setup_mmr_topology"),
            Step("health_check", "_health_check"),
            Step("collect_test_context", "_collect_test_context"),
            Step("prepare_jdbc", "_prepare_jdbc"),
        ]

    @exclusive_environment_operation
    def setup(self, adopt_existing=False):
        validate_profile_isolation(self.env)
        state = EnvState(status="created")
        self.store.save_state(state)
        try:
            for step in self.steps:
                print("[env] start %s" % step.name, flush=True)
                if step.name == "clean_previous":
                    authorize_environment(
                        self.env, self.runner, adopt_existing=adopt_existing,
                    )
                getattr(self, step.method_name)(state)
                if step.name == "clean_previous":
                    # cleanup removes the marker with the old PGDATA. Recreate it
                    # so the newly built environment can be cleaned later.
                    authorize_environment(self.env, self.runner)
                state.completed_steps.append(step.name)
                state.failed_step = None
                self.store.save_state(state)
                print("[env] done  %s" % step.name, flush=True)
            state.status = "ready"
            self.store.save_state(state)
        except Exception:
            state.status = "dirty"
            if state.failed_step is None and self.steps:
                next_step_index = len(state.completed_steps)
                if next_step_index < len(self.steps):
                    state.failed_step = self.steps[next_step_index].name
            self.store.save_state(state)
            print("[env] failed %s" % state.failed_step, flush=True)
            raise

    @exclusive_environment_operation
    def clean(self, dry_run=False, adopt_existing=False):
        if dry_run:
            return build_cleanup_plan(self.env)
        print("[env] start clean", flush=True)
        if adopt_existing:
            authorize_environment(self.env, self.runner, adopt_existing=True)
        plan = clean_environment(self.env, self.runner)
        self.store.save_state(EnvState(status="not_created"))
        print("[env] done  clean", flush=True)
        return plan

    def status(self):
        return {
            "inventory": collect_inventory(self.env, self.runner),
            "health": check_environment(self.env, self.runner),
        }

    @exclusive_environment_operation
    def start(self):
        print("[env] start start", flush=True)
        state = self.store.load_state()
        start_environment_postgres(self.env, self.runner)
        state.status = "started"
        state.failed_step = None
        self.store.save_state(state)
        print("[env] done  start", flush=True)

    @exclusive_environment_operation
    def restart(self):
        print("[env] start restart", flush=True)
        state = self.store.load_state()
        restart_environment_postgres(self.env, self.runner)
        state.status = "started"
        state.failed_step = None
        self.store.save_state(state)
        print("[env] done  restart", flush=True)

    @exclusive_environment_operation
    def stop(self):
        print("[env] start stop", flush=True)
        state = self.store.load_state()
        stop_environment_postgres(self.env, self.runner)
        state.status = "created"
        state.failed_step = None
        self.store.save_state(state)
        print("[env] done  stop", flush=True)

    @exclusive_environment_operation
    def heal(self):
        print("[env] start heal", flush=True)
        start_environment_postgres(self.env, self.runner)
        ha_result = {}
        try:
            from suites.high_availability.cluster_ops import NodeController
            node_ctrl = NodeController(self.env, self.env.env_logs_dir)
            ha_result = node_ctrl.heal_cluster(verify_replication=True)
            print("[env] HA cluster heal: %s" % ha_result, flush=True)
        except Exception as ha_err:
            print("[env] HA heal warning: %s" % ha_err, flush=True)
            ha_result = {"error": str(ha_err)}

        health = check_environment(self.env, self.runner)
        state = self.store.load_state()
        state.status = "started"
        state.failed_step = None
        self.store.save_state(state)
        print("[env] done  heal: health=%s" % health, flush=True)
        return {
            "ha_heal": ha_result,
            "health": health,
        }

    def _preflight(self, state):
        validate_profile_isolation(self.env)
        check_license_dir(self.env)
        state.status = "created"

    def _clean_previous(self, state):
        clean_environment(self.env, self.runner)

    def _setup_mmr_topology(self, state):
        state.status = "started"
        state.nodes.update(setup_mmr_topology(self.env, self.runner))

    def _health_check(self, state):
        checks = check_environment(self.env, self.runner)
        if checks["mmr_non_active"] != "0":
            raise RuntimeError(f"MMR non-active node count is not zero: {checks['mmr_non_active']}")
        if checks["testdb_node1"] != "ACTIVE":
            raise RuntimeError(f"testdb_node1 status mismatch: {checks['testdb_node1']}")
        if checks["testdb_node2"] != "JOIN_START":
            raise RuntimeError(f"testdb_node2 status mismatch: {checks['testdb_node2']}")
        expected_mmr_streaming = str(len(self.env.config["database"]["ports"].get(
            "mmr1_standbys", (
                self.env.config["database"]["ports"].get("mmr1_standby1"),
                self.env.config["database"]["ports"].get("mmr1_standby2"),
                self.env.config["database"]["ports"].get("mmr1_standby3"),
            )
        )))
        if checks.get("mmr_streaming") != expected_mmr_streaming:
            raise RuntimeError(f"MMR streaming count mismatch: {checks.get('mmr_streaming')}")

    def _collect_test_context(self, state):
        context = collect_test_context(self.env, self.runner)
        self.store.save_context(context)

    def _prepare_jdbc(self, state):
        prepare_jdbc(self.env)
