"""Disposable-cluster fixtures for FBase command cases.

These port the legacy fbase_regress ``isolated_*`` fixtures onto the platform
SDK with identical semantics: the same ``/tmp/fbase_regress_`` roots, the same
listener reservation and stale-instance reclaim protocol, the same pg_ctl
lifecycle and the same cleanup priorities. Only the host changed — the
platform ``CaseContext`` owns command execution, cleanup ordering and
evidence attachment. Anything the platform cannot express stays in the
legacy executor instead of being approximated here.
"""

import os
import re
import shutil
from pathlib import Path

from platform_regress.sdk import Blocked

TMP_PREFIX = os.environ.get("FBASE_REGRESS_TMP_PREFIX", "/tmp/fbase_regress_")


def _tmp_prefix(context):
    """Disposable-root prefix for this run.

    Prefers the injected ``environment["tmp_root"]`` so a configured root and
    the safety check stay consistent; falls back to ``FBASE_REGRESS_TMP_ROOT``
    and finally the module default.
    """
    root = str((context.environment or {}).get("tmp_root") or
               os.environ.get("FBASE_REGRESS_TMP_ROOT") or "")
    if not root:
        return TMP_PREFIX
    return root if root.endswith("_") else root + "_"


# Fixture names that only register root-directory cleanup.  The tuple carries
# the legacy directory suffix (appended to the run's tmp prefix) and cleanup
# title.
ROOT_FIXTURES = {
    "isolated_license": ("license", "删除 license 隔离集群"),
    "isolated_sm3_auth": ("sm3_auth", "删除 SM3 认证隔离集群"),
    "isolated_tlcp_transfer": ("tlcp_transfer",
                               "删除 TLCP 导出导入隔离集群"),
    "isolated_tlcp_session": ("tlcp_session",
                              "删除共享 TLCP 元数据隔离集群"),
    "isolated_tlcp_audit": ("tlcp_audit", "删除 TLCP 审计隔离集群"),
    "isolated_tlcp_handshake": ("tlcp_handshake",
                                "删除 TLCP 双向认证隔离集群"),
    "isolated_ssl": ("ssl", "删除 SSL 隔离集群"),
}

SUPPORTED_FIXTURES = frozenset(
    {"isolated_mmr_node_creation", "isolated_mmr_daemon", "isolated_tde",
     "isolated_password_log", "isolated_password_expiry"} | set(ROOT_FIXTURES)
)


def setup_fixture(context, definition, name, options):
    """Apply one isolated fixture exactly as the legacy registry did."""
    # Orderly exits restore everything via deferred cleanups; a force-killed
    # run cannot, so reclaim resources a dead engine owned before allocating.
    _sweep_dead_owners(context, definition)
    if name == "isolated_mmr_node_creation":
        _isolated_mmr_node_creation(context, definition, options)
    elif name == "isolated_mmr_daemon":
        _isolated_mmr_daemon(context, definition, options)
    elif name == "isolated_password_log":
        _isolated_password_log(context, definition, options)
    elif name == "isolated_password_expiry":
        _isolated_password_expiry(context, definition, options)
    elif name == "isolated_tde":
        _isolated_tde(context, definition, options)
    elif name in ROOT_FIXTURES:
        suffix, title = ROOT_FIXTURES[name]
        _isolated_cluster_root(context, definition, options, name,
                               _tmp_prefix(context) + suffix, title)
    else:
        raise ValueError("平台暂不支持隔离 fixture: %s" % name)


def _run(context, argv, timeout=30):
    """Run a fixture command like the legacy runner (merged output, no check)."""
    try:
        result = context.command([str(item) for item in argv],
                                 timeout_seconds=timeout, merge_stderr=True)
        return result.returncode, result.stdout
    except TimeoutError as exc:
        output = (getattr(exc, "partial_stdout", "") or "")
        return 124, output + f"\n命令执行超时（{timeout}s）"


def _binary(context, definition, name):
    return str(_bin_dir(context, definition) / name)


def _bin_dir(context, definition):
    """Locate the FBase binary directory used by the case commands."""
    cached = context.values.get("db_bin_dir")
    if cached:
        return Path(cached)
    override = context.environment.get("db_bin_dir")
    if override and Path(str(override)).is_dir():
        context.values["db_bin_dir"] = str(override)
        return Path(str(override))
    for command in _commands(context, definition):
        match = re.search(r"(/\S+/bin)/(?:initdb|pg_ctl|psql|pg_basebackup)\b", command)
        if match:
            context.values["db_bin_dir"] = match.group(1)
            return Path(match.group(1))
    raise Blocked("无法确定 FBase 二进制目录（未注入 db_bin_dir 且命令中无绝对路径）")


def _commands(context, definition, expand=True):
    """Return the case's shell commands, fully expanded or run_id-only."""
    commands = []
    for step in definition.get("steps") or []:
        if step.get("type") != "command":
            continue
        argv = step.get("argv") or []
        if not expand:
            argv = [str(item).replace("{run_id}", str(context.values.get("run_id", "{run_id}")))
                    for item in argv]
        commands.append(" ".join(
            str(item) for item in (context.expand(argv) if expand else argv)))
    return commands


def _instances(context, definition):
    """Map each isolated MMR listener port to the data directory it owns."""
    instances = dict(context.values.get("isolated_mmr_port_data_dirs") or {})
    for command in _commands(context, definition):
        for initialized in re.finditer(
                r"(?:initdb|pg_basebackup)\b.*?\s-D\s+(?P<data_dir>\S+).*?"
                r"port\s*=\s*(?P<port>\d+)", command):
            instances[initialized.group("port")] = initialized.group("data_dir")
    return instances


def _register_instance(context, port, data_dir):
    instances = context.values.setdefault("isolated_mmr_port_data_dirs", {})
    instances[str(port)] = str(data_dir)


def _track_cluster(context, data_dir, port=None):
    """Ledger-register a disposable cluster so a dead run can be reclaimed."""
    try:
        return context.ledger.register(
            "isolated_cluster", data_dir=str(data_dir), port=port)
    except Exception:
        return None


def _untrack_cluster(context, entry_id):
    try:
        context.ledger.release(entry_id)
    except Exception:
        pass


def _resources(context, definition):
    from platform_regress.environment.disposable import DisposablePostgresResources
    return DisposablePostgresResources(context, owned_prefix=_tmp_prefix(context),
        pg_ctl=lambda: _binary(context, definition, "pg_ctl"), run=_run)


def _sweep_dead_owners(context, definition):
    import pwd

    from platform_regress.ledger import sweep_orphaned_sysv_shm
    _resources(context, definition).sweep()
    sweep_orphaned_sysv_shm(owner=pwd.getpwuid(os.geteuid()).pw_name)


def _declared_ports(context, definition):
    """Return every temporary PostgreSQL listener declared before expansion."""
    ports = []
    for command in _commands(context, definition, expand=False):
        if not re.search(r"(?:initdb|pg_basebackup)\b", command):
            continue
        ports.extend(re.findall(r"port\s*=\s*(\d+)", command))
    return list(dict.fromkeys(ports))


def _allocate_listener(reserved):
    from platform_regress.environment.disposable import reserve_listener
    return reserve_listener(reserved, host=os.environ.get("FBASE_LOCAL_HOST", "127.0.0.1"))


def _reserve_ports(context, definition, declared_ports=None):
    """Allocate and reserve listener ports for the whole disposable topology."""
    reservations = {}
    try:
        mapping = dict(context.values.get("isolated_mmr_port_mapping") or {})
        for declared in (declared_ports or _declared_ports(context, definition)):
            if declared in mapping:
                continue
            allocated, listener = _allocate_listener(reservations)
            mapping[declared] = allocated
            reservations[allocated] = listener
        context.values["isolated_mmr_port_mapping"] = mapping
    except Exception:
        for listener in reservations.values():
            listener.close()
        raise

    context.values["isolated_mmr_port_reservations"] = reservations

    def cleanup():
        for listener in context.values.pop("isolated_mmr_port_reservations", {}).values():
            listener.close()

    # The cluster cleanup (priority 90) must stop every postmaster before the
    # last reservation is released.
    context.defer_cleanup(cleanup, priority=80)


def _release_port(context, port):
    reservations = context.values.get("isolated_mmr_port_reservations") or {}
    listener = reservations.pop(str(port), None)
    if listener is not None:
        listener.close()


def release_port_for_command(context, definition, argv):
    """Release exactly the reserved port owned by an imminent pg_ctl start."""
    reservations = context.values.get("isolated_mmr_port_reservations") or {}
    if not reservations:
        return
    command = " ".join(str(item) for item in argv)
    if not re.search(r"\bpg_ctl\s+-D\s+\S+.*?\bstart\b", command):
        return
    start_data_dirs = re.findall(r"\bpg_ctl\s+-D\s+(\S+)", command)
    instances = _instances(context, definition)
    matches = [(port, data_dir) for port, data_dir in instances.items()
               if data_dir in start_data_dirs]
    if not matches:
        raise Blocked("隔离实例启动命令未登记监听端口，拒绝使用固定端口: %s" % command)
    for port, unused_data_dir in matches:
        listener = reservations.pop(port, None)
        if listener is not None:
            listener.close()


def _cluster_pids(data_dir):
    from platform_regress.environment.disposable import postmaster_pids
    return postmaster_pids(data_dir)


def _postgres_data_dir(pid):
    from platform_regress.environment.disposable import postgres_data_dir
    return postgres_data_dir(pid)


def _listener_pids(context, port):
    from platform_regress.environment.disposable import DisposablePostgresResources
    return DisposablePostgresResources(context, owned_prefix=_tmp_prefix(context),
        pg_ctl=lambda: "pg_ctl", run=_run).listener_pids(port)


def _wait_ports_free(context, definition, timeout=10):
    return _resources(context, definition).wait_ports_free(_instances(context, definition), timeout=timeout)


def _reclaim_stale_listeners(context, definition):
    return _resources(context, definition).reclaim_listeners(_instances(context, definition))


def _remove_stale_cluster_root(context, definition, data_dir):
    return _resources(context, definition).remove(data_dir,
        registered=_instances(context, definition).values(),
        wait=lambda: _wait_ports_free(context, definition))


def _isolated_cluster_root(context, definition, options, fixture_name,
                           default_data_dir, cleanup_name):
    """Register cleanup for a root containing one or more disposable clusters."""
    data_dir = Path(options.get("data_dir", default_data_dir))
    if not str(data_dir).startswith(_tmp_prefix(context)):
        raise ValueError("%s data_dir 必须位于 %s 下" % (fixture_name, _tmp_prefix(context)))
    _remove_stale_cluster_root(context, definition, data_dir)
    ledger_entries = [_track_cluster(context, data_dir)]

    def cleanup():
        _resources(context, definition).remove(data_dir,
            registered=_instances(context, definition).values(), archive=True,
            wait=lambda: _wait_ports_free(context, definition))
        for entry_id in ledger_entries:
            _untrack_cluster(context, entry_id)

    context.defer_cleanup(cleanup, priority=90)


def _isolated_mmr_node_creation(context, definition, options):
    """Remove disposable instances and expose their real test topology."""
    _isolated_cluster_root(context, definition, options,
                           "isolated_mmr_node_creation",
                           _tmp_prefix(context) + "mmr_node_creation",
                           "删除多活节点创建隔离实例")
    _reclaim_stale_listeners(context, definition)
    _reserve_ports(context, definition)


def _isolated_mmr_daemon(context, definition, options):
    """Stop and delete the disposable instance used for MMR worker lifecycle."""
    data_dir = Path(options.get("data_dir", _tmp_prefix(context) + "mmr_daemon"))
    declared_port = str(options.get("port", "15445"))
    if not str(data_dir).startswith(_tmp_prefix(context)):
        raise ValueError("isolated_mmr_daemon data_dir 必须位于 %s 下" % _tmp_prefix(context))
    _remove_stale_cluster_root(context, definition, data_dir)
    _reserve_ports(context, definition, [declared_port])
    allocated_port = context.values["isolated_mmr_port_mapping"][declared_port]
    _register_instance(context, allocated_port, data_dir)
    entry_id = _track_cluster(context, data_dir, allocated_port)

    def cleanup():
        _run(context, [_binary(context, definition, "pg_ctl"), "-D", str(data_dir),
                       "stop", "-m", "immediate"], timeout=30)
        shutil.rmtree(str(data_dir), ignore_errors=True)
        _untrack_cluster(context, entry_id)

    context.defer_cleanup(cleanup, priority=300)


def _isolated_password_log(context, definition, options):
    """Preserve server stderr captures from the password-masking case."""
    data_dir = Path(options.get("data_dir", _tmp_prefix(context) + "password_log"))
    if not str(data_dir).startswith(_tmp_prefix(context)):
        raise ValueError("isolated_password_log data_dir 必须位于 %s 下" % _tmp_prefix(context))

    entry_id = _track_cluster(context, data_dir)

    def cleanup():
        _run(context, [_binary(context, definition, "pg_ctl"), "-D", str(data_dir),
                       "stop", "-m", "immediate"], timeout=30)
        for name in ("console_capture.log", "file_capture.log"):
            source = data_dir / name
            if source.is_file():
                context.attach_file(name, source)
        shutil.rmtree(str(data_dir), ignore_errors=True)
        _untrack_cluster(context, entry_id)

    context.defer_cleanup(cleanup, priority=90)


def _isolated_password_expiry(context, definition, options):
    """Remove the disposable server used by the password-cycle case."""
    data_dir = Path(options.get("data_dir", _tmp_prefix(context) + "password_expiry"))
    if not str(data_dir).startswith(_tmp_prefix(context)):
        raise ValueError("isolated_password_expiry data_dir 必须位于 %s 下" % _tmp_prefix(context))

    entry_id = _track_cluster(context, data_dir)

    def cleanup():
        _run(context, [_binary(context, definition, "pg_ctl"), "-D", str(data_dir),
                       "stop", "-m", "immediate"], timeout=30)
        shutil.rmtree(str(data_dir), ignore_errors=True)
        _untrack_cluster(context, entry_id)

    # Stop the server before system_clock restores host time.
    context.defer_cleanup(cleanup, priority=300)


def _isolated_tde(context, definition, options):
    """Register cleanup for a TDE cluster created by reportable case steps."""
    data_dir = Path(options.get("data_dir", _tmp_prefix(context) + "tde"))
    key_file = Path(options.get("key_file", _tmp_prefix(context) + "tde.txt"))
    if not str(data_dir).startswith(_tmp_prefix(context)):
        raise ValueError("isolated_tde data_dir 必须位于 %s 下" % _tmp_prefix(context))

    entry_id = _track_cluster(context, data_dir)

    def cleanup():
        _run(context, [_binary(context, definition, "pg_ctl"), "-D", str(data_dir),
                       "stop", "-m", "immediate"], timeout=30)
        shutil.rmtree(str(data_dir), ignore_errors=True)
        _untrack_cluster(context, entry_id)
        try:
            key_file.unlink()
        except OSError:
            pass

    context.defer_cleanup(cleanup, priority=90)
