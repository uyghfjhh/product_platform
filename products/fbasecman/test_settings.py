"""Named tested build selections shared by regression and soak runs."""
import hashlib
import os
import subprocess
from pathlib import Path
import yaml


def resolve(settings, environment, *, inspect=False):
    source = settings.product_regress_root('fbasecman') / 'regress.yaml'
    config = yaml.safe_load(source.read_text())
    for override in (source.with_name('regress.local.yaml'),
                     settings.profiles_dir / environment['id'] / 'regress.override.yaml'):
        if override.is_file():
            values = yaml.safe_load(override.read_text()) or {}
            for section in ('fbasecman', 'local'):
                config.setdefault(section, {}).update(values.get(section) or {})
    selection = environment.get('product_test_settings') or {}
    builds = selection.get('builds')
    if builds is not None:
        selected = next((item for item in builds if item['id'] == selection.get('active_build_id')), None)
        if selected is None:
            raise ValueError('请选择一个有效的当前被测版本')
    else:
        selected = selection
    binary = selected.get('fbasecman_bin') or os.environ.get('PRODUCT_PLATFORM_FBASECMAN_BIN') or config['fbasecman']['fbasecman_bin']
    license_dir = selected.get('license_dir') or os.environ.get('PRODUCT_PLATFORM_FBASECMAN_LICENSE_DIR') or config['fbasecman']['license_dir']
    result = {'fbasecman_bin': binary, 'license_dir': license_dir,
              'psql': str(Path(config['local']['postgres_dir']) / 'bin/psql'),
              'source': '执行环境配置' if selected.get('fbasecman_bin') else '服务器默认配置（尚未在页面保存）'}
    result.update(build_id=selected.get('id', 'legacy'), build_name=selected.get('name', '默认版本'))
    if inspect:
        path = Path(binary)
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError('被测 fbasecman 必须是控制机上可执行文件的绝对路径')
        if not Path(license_dir).is_absolute() or not Path(license_dir).is_dir():
            raise ValueError('License 必须是控制机上存在的目录绝对路径')
        digest = hashlib.sha256()
        with path.open('rb') as file:
            for chunk in iter(lambda: file.read(1048576), b''):
                digest.update(chunk)
        result.update(sha256=digest.hexdigest(), size_bytes=path.stat().st_size,
                      resolved_path=str(path.resolve()))
        # instance.c registers argp_program_version; --version exits before startup.
        try:
            version = subprocess.run([str(path), '--version'], capture_output=True, text=True, timeout=5)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('fbasecman --version 在 5 秒内未响应') from exc
        if version.returncode:
            raise ValueError('fbasecman --version 检查失败，请确认所选可执行文件')
        result['version'] = (version.stdout+version.stderr).strip()[:2000]
    return result


def regression_snapshot(settings, environment):
    from uuid import uuid4
    value = resolve(settings, environment, inspect=True)
    output = settings.artifact_dir('fbasecman', environment['id']) / 'tested-builds' / uuid4().hex
    output.mkdir(parents=True)
    import json
    (output / 'build.json').write_text(json.dumps(value, ensure_ascii=False))
    override = output / 'runtime.yaml'
    override.write_text(yaml.safe_dump({'fbasecman': {key: value[key] for key in ('fbasecman_bin', 'license_dir')}}))
    return {'regress_extra_configs': [str(override)], 'tested_build': value,
            'fbasecman_bin': value['fbasecman_bin'], 'license_dir': value['license_dir']}


def describe(settings, environment):
    """Keep legacy response fields while exposing the saved choices."""
    try:
        value = resolve(settings, environment, inspect=True)
    except (ValueError, OSError, TimeoutError) as exc:
        value = {**resolve(settings, environment), 'error': str(exc)}
    selection = environment.get('product_test_settings') or {}
    builds = selection.get('builds')
    if builds is None:
        builds = [{'id': 'legacy', 'name': '默认版本',
                   'fbasecman_bin': value['fbasecman_bin'], 'license_dir': value['license_dir']}]
    return {**value, 'builds': builds, 'active_build_id': selection.get('active_build_id', 'legacy')}
