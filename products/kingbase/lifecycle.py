"""Local native lifecycle, executed by the platform worker under resource locks."""

import json
import os
import pwd
import subprocess
import sys
from pathlib import Path

from platform_app.resources import canonical_host


def run(action, nodes):
    if action not in ('start', 'stop', 'restart', 'doctor'):
        raise ValueError('不支持的金仓操作')
    checked = []
    for node in nodes:
        if canonical_host(node['host']) != 'local':
            raise ValueError('金仓原生启停当前仅支持本机实例')
        directory = Path(node['data_dir'])
        owner = pwd.getpwuid(directory.stat().st_uid)
        if directory == Path('/') or not directory.is_absolute():
            raise ValueError('无效数据目录')
        # Use the installation validated for this first native integration.
        ctl = Path('/opt/Kingbase/ES/V9/Server/bin/sys_ctl')
        prefix = [] if os.getuid() == owner.pw_uid else ['sudo', '-n', '-u', owner.pw_name]
        probe = subprocess.run(prefix + ['test', '-f', str(directory / 'kingbase.conf')], check=False)
        if probe.returncode or not ctl.is_file() or owner.pw_name != 'kingbase':
            raise ValueError('数据目录或金仓安装身份不匹配')
        checked.append((node, prefix, ctl))
    if action == 'stop':
        checked.reverse()
    for node, prefix, ctl in checked:
        base = prefix + [str(ctl), '-D', node['data_dir']]
        state = subprocess.run(base + ['status'], capture_output=True, text=True, check=False)
        if state.returncode not in (0, 3):
            raise RuntimeError(state.stderr or state.stdout)
        print(node['id'], state.stdout.strip(), flush=True)
        running = state.returncode == 0
        if action == 'doctor':
            if not running:
                raise RuntimeError('金仓实例未运行：' + node['id'])
            continue
        if action in ('stop', 'restart') and running:
            subprocess.run(base + ['-m', 'fast', '-w', '-t', '60', 'stop'], check=True)
        if action in ('start', 'restart') and (not running or action == 'restart'):
            subprocess.run(base + ['-l', str(Path(node['data_dir']) / 'platform-startup.log'), '-w', '-t', '60', 'start'], check=True)


if __name__ == '__main__':
    run(sys.argv[1], json.loads(sys.argv[2]))
