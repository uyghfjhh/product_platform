"""Read-only instance inventory executed on the database host (Python 3.6+)."""
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path


def command(argv, env=None):
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True, timeout=8, env=env, check=False)
    if result.returncode:
        raise ValueError(result.stderr.strip()[-600:] or 'command failed')
    return result.stdout.strip()


def connection(value):
    # Only expose endpoint identity; never return passwords or passfile paths.
    fields = dict(part.split('=', 1) for part in shlex.split(value) if '=' in part)
    return {'host': fields.get('host', ''), 'port': int(fields.get('port', 5432))}


def inventory(request):
    rows = []
    for directory in request['data_dirs']:
        path = Path(directory)
        if not (path / 'PG_VERSION').is_file():
            raise ValueError('不是数据库数据目录: ' + directory)
        home = request.get('home')
        if not home:
            opts = shlex.split((path / 'postmaster.opts').read_text())
            home = str(Path(opts[0]).parent.parent)
        binary = str(Path(home) / 'bin/postgres')
        def setting(key):
            return command([binary, '-D', directory, '-C', key])
        control = command([str(Path(home) / 'bin/pg_controldata'), directory],
                          dict(os.environ, LC_ALL='C'))
        match = re.search(r'Database system identifier:\s*(\d+)', control)
        if not match:
            raise ValueError('无法读取数据库系统标识: ' + directory)
        standby = (path / 'standby.signal').exists() or 'in recovery' in control
        port = int(setting('port'))
        node = {'data_dir': str(path.resolve()), 'home': home, 'port': port,
                'version': (path / 'PG_VERSION').read_text().strip(),
                'system_identifier': match.group(1), 'standby': standby,
                'running': False, 'mmr_nodes': [], 'mmr_groups': []}
        node['mmr_configured'] = 'fdd_mmr' in setting('shared_preload_libraries').split(',')
        if standby:
            node['upstream'] = connection(setting('primary_conninfo'))
        env = dict(os.environ, PGCONNECT_TIMEOUT='3',
                   PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=5000')
        def query(sql):
            return json.loads(command([str(Path(home) / 'bin/psql'), '-X', '-w', '-At',
                                       '-h', '127.0.0.1', '-p', str(port), '-U', request['database_user'],
                                       '-d', request['database_name'], '-v', 'ON_ERROR_STOP=1', '-c', sql], env))
        try:
            identity = query("SELECT json_build_object('port',inet_server_port(),'data_dir',current_setting('data_directory'),'standby',pg_is_in_recovery(),'mmr',to_regclass('fdd.mmr_node') IS NOT NULL)")
        except ValueError as exc:
            node['warning'] = '实例未运行或无法连接；仅采集离线配置: ' + str(exc)
        else:
            if identity['port'] != port or Path(identity['data_dir']).resolve() != path.resolve():
                raise ValueError('端口连接到另一个数据目录: ' + directory)
            node.update(running=True, standby=identity['standby'])
            if identity['mmr']:
                raw = query("SELECT coalesce(json_agg(t),'[]'::json) FROM (SELECT node_id,node_name,group_id,node_state,dbname,node_dsn FROM fdd.mmr_node ORDER BY node_id) t")
                for member in raw:
                    endpoint = connection(member.pop('node_dsn'))
                    node['mmr_nodes'].append(dict(member, endpoint=endpoint))
                node['mmr_groups'] = query("SELECT coalesce(json_agg(t),'[]'::json) FROM (SELECT group_id,group_name,group_uuid::text FROM fdd.mmr_group ORDER BY group_id) t")
        rows.append(node)
    return {'instances': rows}


if __name__ == '__main__':
    try:
        print(json.dumps(inventory(json.loads(sys.argv[1])), ensure_ascii=False))
    except (OSError, ValueError, KeyError, IndexError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        sys.exit(1)
