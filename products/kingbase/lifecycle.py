"""Local native lifecycle, executed by the platform worker under resource locks."""

import json
import os
import pwd
import subprocess
import sys
from pathlib import Path

from platform_app.resources import canonical_host


def control_command(node):
    owner = pwd.getpwnam('kingbase')
    prefix = [] if os.getuid() == owner.pw_uid else ['sudo', '-n', '-u', owner.pw_name]
    installations = (Path('/opt/Kingbase/ES/V9/Server'), Path(owner.pw_dir) / 'ES/V9/Server')
    if canonical_host(node['host']) != 'local':
        raise ValueError('金仓原生启停当前仅支持本机实例')
    directory = Path(node['data_dir'])
    if directory == Path('/') or not directory.is_absolute():
        raise ValueError('无效数据目录')
    ownership = subprocess.run(prefix + ['stat', '-c', '%u', '--', str(directory)],
                               capture_output=True, text=True, check=False)
    if ownership.returncode or ownership.stdout.strip() != str(owner.pw_uid):
        raise ValueError('数据目录不属于 kingbase 用户')
    probe = subprocess.run(prefix + ['test', '-f', str(directory / 'kingbase.conf')], check=False)
    candidates = [home / 'bin/sys_ctl' for home in installations
                  if subprocess.run(prefix + ['test', '-x', str(home / 'bin/sys_ctl')]).returncode == 0]
    if probe.returncode or len(candidates) != 1:
        raise ValueError('无法唯一确定金仓安装或数据目录配置')
    return prefix + [str(candidates[0]), '-D', node['data_dir']]


def run(action, nodes):
    if action not in ('start', 'stop', 'restart', 'doctor'):
        raise ValueError('不支持的金仓操作')
    checked = [(node, control_command(node)) for node in nodes]
    if action == 'stop':
        checked.reverse()
    for node, base in checked:
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
