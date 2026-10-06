import json
import shlex
from types import SimpleNamespace

import pytest
from platform_app.deployment.probes import probe


def test_remote_probe_executes_agent_not_nested_ssh(monkeypatch):
    captured = {}

    def run(argv, **kwargs):
        captured['argv'] = argv
        return SimpleNamespace(returncode=0, stdout=json.dumps({'installations': []}), stderr='')

    monkeypatch.setattr('platform_app.deployment.probes.subprocess.run', run)
    request = {'operation': 'discover', 'home': '/opt/database with spaces'}
    assert probe('192.168.0.15', request, ssh={'user': 'postgres', 'port': 2222}) == {'installations': []}
    argv = captured['argv']
    assert argv[-3:-1] == ['postgres@192.168.0.15', '--']
    command = shlex.split(argv[-1])
    assert command[:2] == ['python3', '-c']
    assert 'def inspect_installation' in command[2]
    assert json.loads(command[3]) == request
    assert argv[argv.index('-p') + 1] == '2222'


def test_failed_ssh_reports_transport_error(monkeypatch):
    monkeypatch.setattr('platform_app.deployment.probes.subprocess.run',
                        lambda *a, **k: SimpleNamespace(returncode=255, stdout='', stderr='Permission denied (publickey).'))
    with pytest.raises(ValueError, match='Permission denied'):
        probe('192.168.0.15', {'operation': 'discover'})
