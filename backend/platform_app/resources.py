"""Resource identity and ordered locks shared by all execution entry points."""

import fcntl
import hashlib
import ipaddress
import json
import socket
from contextlib import ExitStack, contextmanager
from pathlib import Path, PurePosixPath

import yaml

from .filestore import ConflictError


def canonical_host(host):
    host = host.lower().rstrip('.')
    if host in {'localhost', '127.0.0.1', '::1', socket.gethostname().lower(), socket.gethostname().lower().split(".", 1)[0]}:
        return 'local'
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return host


def resource_keys(environment):
    """Conservatively own every instance in a registered deployment config.

    Connection-only environments still lock their endpoint. Remote directory
    identities are lexical; target-side canonical paths are checked by probes.
    """
    keys = set()
    host = canonical_host(environment['host'])
    keys.add(f"endpoint:{host}:{environment['port']}")
    config_path = environment.get('deployment_config')
    if config_path and Path(config_path).is_file():
        config = yaml.safe_load(Path(config_path).read_text())
        if not isinstance(config, dict):
            raise ValueError('部署配置不是对象')
        hosts = config.get('hosts') or {}
        for node in (config.get('instances') or {}).values():
            identity = canonical_host(hosts[node['host']]['address'])
            path = Path(node['data_dir']).resolve() if identity == 'local' else PurePosixPath(node['data_dir'])
            keys.add(f"endpoint:{identity}:{node['port']}")
            keys.add("directory:" + json.dumps([identity, str(path)]))
    return keys


def validate_registration(store, environment):
    if not environment.get('deployment_config'):
        return
    keys = resource_keys(environment)
    for existing in store.environments.list_environments():
        if existing['id'] == environment['id'] or not existing.get('deployment_config'):
            continue
        try:
            other = resource_keys(existing)
            shared = keys & other
            for key in keys:
                if not key.startswith("directory:"):
                    continue
                host, directory = json.loads(key.partition(":")[2])
                for candidate in other:
                    if not candidate.startswith("directory:"):
                        continue
                    other_host, other_directory = json.loads(candidate.partition(":")[2])
                    if host == other_host and (PurePosixPath(directory).is_relative_to(other_directory)
                                              or PurePosixPath(other_directory).is_relative_to(directory)):
                        shared.add(key)
        except (OSError, ValueError, KeyError):
            # A missing old config must not make its known endpoint unowned.
            shared = keys & {f"endpoint:{canonical_host(existing['host'])}:{existing['port']}"}
        if shared:
            raise ConflictError(f"实例资源已登记在环境 {existing['id']}：{sorted(shared)[0]}")


@contextmanager
def resource_lock(settings, environment):
    """Acquire in a deterministic order so partially overlapping jobs cannot race."""
    keys = resource_keys(environment) | {'environment:' + environment['id']}
    directory = settings.runtime_dir / 'locks' / 'resources'
    directory.mkdir(parents=True, exist_ok=True)
    modes = {key: fcntl.LOCK_EX for key in keys}
    for key in keys:
        if key.startswith("directory:"):
            host, path = json.loads(key.partition(":")[2])
            for parent in PurePosixPath(path).parents:
                modes.setdefault("directory:" + json.dumps([host, str(parent)]), fcntl.LOCK_SH)
    with ExitStack() as stack:
        for key in sorted(modes):
            path = directory / (hashlib.sha256(key.encode()).hexdigest() + '.lock')
            handle = stack.enter_context(path.open('a'))
            try:
                fcntl.flock(handle.fileno(), modes[key] | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ConflictError('实例资源已有操作正在执行') from exc
        yield
