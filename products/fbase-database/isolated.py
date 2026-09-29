"""Disposable-cluster fixtures for FBase command cases.

These port the legacy fbase_regress ``isolated_*`` fixtures onto the platform
SDK with identical semantics: the same ``/tmp/fbase_regress_`` roots, the same
listener reservation and stale-instance reclaim protocol, the same pg_ctl
lifecycle and the same cleanup priorities. Only the host changed — the
platform ``CaseContext`` owns command execution, cleanup ordering and
evidence attachment. Anything the platform cannot express stays in the
legacy executor instead of being approximated here.
"""

import errno
import os
import re
import shutil
import socket
import time
from pathlib import Path

from platform_regress import Blocked


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
        return 124, output + "\n命令执行超时（%ss）" % timeout


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


def _sweep_dead_owners(context, definition):
    """Reclaim framework clusters whose owning engine process is gone.

    A run killed with SIGKILL leaves postmasters listening and directories
    behind.  Any later isolated fixture on this environment sweeps the
    ledger: stop the postmaster under the framework tmp prefix only, remove
    the directory, then drop the entry.  Orphaned SysV segments left by dead
    postmasters are released the same way.
    """
    from platform_regress.ledger import sweep_orphaned_sysv_shm
    try:
        entries = context.ledger.sweep("isolated_cluster")
    except Exception:
        entries = []
    for entry in entries:
        data_dir = Path(str(entry.get("data_dir") or ""))
        if not str(data_dir).startswith(_tmp_prefix(context)):
            continue
        clusters = []
        if (data_dir / "PG_VERSION").is_file():
            clusters.append(data_dir)
        for child in data_dir.iterdir() if data_dir.is_dir() else []:
            if child.is_dir() and (child / "PG_VERSION").is_file():
                clusters.append(child)
        if clusters:
            try:
                pg_ctl = _binary(context, definition, "pg_ctl")
            except Exception:
                pg_ctl = None
            for cluster in clusters:
                if pg_ctl:
                    _run(context, [pg_ctl, "-D", str(cluster),
                                   "stop", "-m", "immediate"], timeout=30)
                # A postmaster that ignores pg_ctl still holds its port;
                # SIGTERM by pid is the bounded fallback for dead-owner dirs.
                for pid in _cluster_pids(cluster):
                    try:
                        os.kill(pid, 15)
                    except OSError:
                        pass
        shutil.rmtree(str(data_dir), ignore_errors=True)
        try:
            context.ledger.drop(entry)
        except Exception:
            pass
    try:
        import pwd
        sweep_orphaned_sysv_shm(owner=pwd.getpwuid(os.geteuid()).pw_name)
    except Exception:
        pass


def _declared_ports(context, definition):
    """Return every temporary PostgreSQL listener declared before expansion."""
    ports = []
    for command in _commands(context, definition, expand=False):
        if not re.search(r"(?:initdb|pg_basebackup)\b", command):
            continue
        ports.extend(re.findall(r"port\s*=\s*(\d+)", command))
    return list(dict.fromkeys(ports))


def _allocate_listener(reserved):
    """Bind one available loopback port and retain the socket until pg_ctl starts."""
    for port in list(range(20000, 65536)) + list(range(1025, 20000)):
        if str(port) in reserved:
            continue
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((os.environ.get("FBASE_LOCAL_HOST", "127.0.0.1"), port))
        except OSError as exc:
            listener.close()
            if exc.errno == errno.EADDRINUSE:
                continue
            raise Blocked("无法探测隔离实例端口 %s: %s" % (port, exc))
        return str(port), listener
    raise Blocked("没有可用的隔离实例监听端口")


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
    """Postmaster pid from postmaster.pid, if the file survives."""
    try:
        lines = (Path(data_dir) / "postmaster.pid").read_text().splitlines()
        return [int(lines[0].strip())] if lines and lines[0].strip().isdigit() else []
    except (OSError, ValueError):
        return []


def _postgres_data_dir(pid):
    """Read the postmaster data directory without trusting lsof process names."""
    try:
        argv = (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
    except OSError:
        return None
    for index, argument in enumerate(argv[:-1]):
        if argument == b"-D":
            return Path(argv[index + 1].decode("utf-8", "replace"))
    return None


def _listener_pids(context, port):
    returncode, output = _run(
        context, ["lsof", "-nP", "-t", "-iTCP:%s" % port, "-sTCP:LISTEN"], timeout=10)
    return [line.strip() for line in output.splitlines() if line.strip().isdigit()]


def _wait_ports_free(context, definition, timeout=10):
    """Wait for an immediate-stop listener to release a reused test port."""
    ports = tuple(_instances(context, definition))
    if not ports:
        return
    deadline = time.monotonic() + timeout
    escalated = False
    while True:
        occupied = {port: _listener_pids(context, port) for port in ports}
        occupied = {port: pids for port, pids in occupied.items() if pids}
        if not occupied:
            return
        if time.monotonic() >= deadline:
            if not escalated:
                # pg_ctl -m immediate can return before the postmaster lets go;
                # SIGTERM framework-owned stragglers once, then re-wait.
                for pids in occupied.values():
                    for pid in pids:
                        data_dir = _postgres_data_dir(pid)
                        if (data_dir is not None and
                                str(data_dir).startswith(_tmp_prefix(context))):
                            try:
                                os.kill(int(pid), 15)
                            except OSError:
                                pass
                escalated = True
                deadline = time.monotonic() + 5
                continue
            details = ", ".join("%s(pid=%s)" % (port, ",".join(pids))
                                for port, pids in sorted(occupied.items()))
            raise RuntimeError("隔离实例端口在停止后 %ss 仍被监听: %s" % (timeout, details))
        time.sleep(0.1)


def _reclaim_stale_listeners(context, definition):
    """Stop only orphaned framework postmasters occupying this case's ports."""
    for port in _instances(context, definition):
        for pid in _listener_pids(context, port):
            data_dir = _postgres_data_dir(pid)
            if data_dir is None:
                raise Blocked("端口 %s 被 PID %s 占用，但无法确认其 PostgreSQL 数据目录"
                              % (port, pid))
            if (not str(data_dir).startswith(_tmp_prefix(context)) or
                    not (data_dir / "PG_VERSION").is_file()):
                raise Blocked(
                    "端口 %s 被非本框架隔离实例占用（PID %s，数据目录 %s），拒绝停止"
                    % (port, pid, data_dir))
            _run(context, [_binary(context, definition, "pg_ctl"), "-D", str(data_dir),
                           "stop", "-m", "immediate"], timeout=30)
            shutil.rmtree(str(data_dir), ignore_errors=True)
    _wait_ports_free(context, definition)


def _remove_stale_cluster_root(context, definition, data_dir):
    """Stop and remove a prior interrupted run under a framework-owned root."""
    data_dir = Path(data_dir)
    command_clusters = [Path(cluster)
                        for cluster in _instances(context, definition).values()]
    if not data_dir.exists() and not any(cluster.exists()
                                         for cluster in command_clusters):
        return
    if not str(data_dir).startswith(_tmp_prefix(context)):
        raise Blocked("拒绝清理非框架隔离目录: %s" % data_dir)
    clusters = []
    if (data_dir / "PG_VERSION").is_file():
        clusters.append(data_dir)
    for child in data_dir.iterdir() if data_dir.is_dir() else []:
        if child.is_dir() and (child / "PG_VERSION").is_file():
            clusters.append(child)
    # The case command may expand {run_id} after fixture registration.  Use
    # the parsed instance paths as well so an interrupted run cannot leave a
    # nonempty data directory that makes the next initdb fail.
    for cluster in command_clusters:
        if (cluster / "PG_VERSION").is_file() and cluster not in clusters:
            clusters.append(cluster)
    for cluster in clusters:
        _run(context, [_binary(context, definition, "pg_ctl"), "-D", str(cluster),
                       "stop", "-m", "immediate"], timeout=30)
        shutil.rmtree(str(cluster), ignore_errors=True)
    shutil.rmtree(str(data_dir), ignore_errors=True)


def _isolated_cluster_root(context, definition, options, fixture_name,
                           default_data_dir, cleanup_name):
    """Register cleanup for a root containing one or more disposable clusters."""
    data_dir = Path(options.get("data_dir", default_data_dir))
    if not str(data_dir).startswith(_tmp_prefix(context)):
        raise ValueError("%s data_dir 必须位于 %s 下" % (fixture_name, _tmp_prefix(context)))
    _remove_stale_cluster_root(context, definition, data_dir)
    ledger_entries = [_track_cluster(context, data_dir)]

    def cleanup():
        clusters = set()
        if data_dir.is_dir() and (data_dir / "PG_VERSION").is_file():
            clusters.add(data_dir)
        for child in data_dir.iterdir() if data_dir.is_dir() else []:
            if child.is_dir() and (child / "PG_VERSION").is_file():
                clusters.add(child)
        # Commands may have expanded the fixture's data_dir placeholder before
        # startup.  The registered topology is therefore authoritative even
        # when filesystem enumeration misses a still-running postmaster.
        for cluster in _instances(context, definition).values():
            cluster = Path(cluster)
            if (cluster / "PG_VERSION").is_file():
                clusters.add(cluster)
        for cluster in clusters:
            for source_name in ("start.log", "postgresql.log", "postgresql.csv"):
                source = cluster / source_name
                if source.is_file():
                    context.attach_file("%s.%s" % (cluster.name, source_name), source)
            _run(context, [_binary(context, definition, "pg_ctl"), "-D", str(cluster),
                           "stop", "-m", "immediate"], timeout=30)
        # pg_ctl can return while the postmaster still owns its listener.  The
        # next isolated MMR case reuses fixed ports, so do not return until
        # the kernel has released every port declared by this case.
        _wait_ports_free(context, definition)
        for cluster in clusters:
            shutil.rmtree(str(cluster), ignore_errors=True)
        shutil.rmtree(str(data_dir), ignore_errors=True)
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
