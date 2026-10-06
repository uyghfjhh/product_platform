import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest
import yaml

from platform_app.config import load_settings
from platform_app.workloads import WorkloadInput, runner


@pytest.mark.skipif(os.environ.get('PLATFORM_WORKLOAD_LIVE') != '1', reason='isolated PostgreSQL integration is opt-in')
def test_pgbench_executes_custom_query_in_owned_readonly_cluster(tmp_path, monkeypatch):
    profile = Path(os.environ.get('PLATFORM_WORKLOAD_DEPLOYMENT_CONFIG',
                                  load_settings().profile_dir('cman-lab') / 'pgcluster.yaml'))
    config = yaml.safe_load(profile.read_text())
    installation = next(iter(config['postgresql_installations'].values()))
    home = Path(installation['home'])
    initdb, pg_ctl, pgbench = (str(home / 'bin' / name) for name in ('initdb', 'pg_ctl', 'pgbench'))
    assert all(Path(binary).is_file() for binary in (initdb, pg_ctl, pgbench))
    monkeypatch.setenv('LD_LIBRARY_PATH', str(home / 'lib'))
    data = tmp_path / 'cluster'
    log = tmp_path / 'postgres.log'
    subprocess.run([initdb, '-D', str(data), '-A', 'trust', '-U', 'postgres', '--no-locale'],
                   check=True, capture_output=True, text=True)
    license_config = installation.get('license') or {}
    if license_config.get('source_file'):
        license_target = data / Path(license_config.get('data_file', 'license.dat')).name
        shutil.copyfile(license_config['source_file'], license_target)
        license_target.chmod(0o600)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    started = False
    try:
        startup = subprocess.run([pg_ctl, '-D', str(data), '-l', str(log), '-o',
                                  f'-h 127.0.0.1 -p {port} -k {tmp_path}', '-w', 'start'],
                                 capture_output=True, text=True)
        started = startup.returncode == 0
        assert started, startup.stdout + startup.stderr + (log.read_text() if log.exists() else '')
        jdbc_jar = load_settings().product_regress_root('fbasecman') / 'lib_jdbc' / 'postgresql-42.7.7.jar'
        assert jdbc_jar.is_file()
        for name, driver, threshold in [('execution', 'pgbench', 0), ('threshold', 'pgbench', 1000000000), ('jdbc', 'jdbc', 0)]:
            output = tmp_path / name
            output.mkdir()
            options = WorkloadInput(
                clients=2, jobs=2 if driver == 'pgbench' else None,
                duration_seconds=2, target_tps=20 if driver == 'pgbench' else 0,
                minimum_tps=threshold, jdbc_jar=str(jdbc_jar) if driver == 'jdbc' else '',
                script="SELECT 1 / CASE WHEN current_setting('transaction_read_only') = 'on' THEN 1 ELSE 0 END;",
            )
            path = output / 'request.json'
            path.write_text(json.dumps({
                'output': str(output), 'options': options.model_dump(), 'driver': driver,
                'environment': {'host': '127.0.0.1', 'port': port, 'database_user': 'postgres', 'database_name': 'postgres'},
                'pgbench': pgbench, 'execution_id': name,
            }))
            assert runner.main(path) == (1 if threshold else 0)
            result = json.loads((output / 'result.json').read_text())
            metrics = json.loads((output / 'metrics.json').read_text())
            assert result['summary']['tps'] > 0
            assert metrics['samples']
            assert result['performance_verdict'] == ('FAIL' if threshold else 'NOT_CONFIGURED')
            if driver == 'pgbench':
                assert 'BEGIN READ ONLY;' in (output / 'workload.sql').read_text()
            else:
                assert result['summary']['errors'] == 0
                assert result['summary']['elapsed'] > 0
                assert abs(result['summary']['tps'] * result['summary']['elapsed'] - result['summary']['transactions']) < 1
    finally:
        if started:
            subprocess.run([pg_ctl, '-D', str(data), '-m', 'fast', '-w', 'stop'], check=True, capture_output=True)
