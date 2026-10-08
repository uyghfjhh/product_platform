import json

from fastapi.testclient import TestClient
from platform_app.api import create_app
from platform_app.license_defaults import defaults_path
from test_api import settings_for
from test_license import _decode_license_body, legacy_key_fixture


def client_for(tmp_path):
    settings = settings_for(tmp_path)
    return settings, TestClient(create_app(settings, enqueuer=lambda task_id: None))


def test_defaults_roundtrip_and_new_version_signing(tmp_path):
    settings, client = client_for(tmp_path)
    _, password = legacy_key_fixture(tmp_path)
    defaults = client.get('/api/v1/licenses/defaults').json()
    assert defaults['default_password'] == '123456'
    assert defaults['validity'] == {'years': 10, 'months': 0, 'days': 0}
    defaults.update(vendor='自定义厂商', purpose='生产环境', license_version='1.1', default_password='custom-default',
                    mac_addrs=['02:42:8e:0f:0b:1b'])
    defaults['products']['fbasecman'].update(default_version='2.0',
                                           selected=False, validity={'years': 2, 'months': 3, 'days': 4})
    assert client.put('/api/v1/licenses/defaults', json=defaults).status_code == 200
    assert defaults_path(settings) == tmp_path / 'config' / 'license' / 'defaults.json'
    assert defaults_path(settings).exists()
    assert not (tmp_path / 'data' / 'license' / 'defaults.json').exists()
    assert not defaults_path(settings).with_suffix('.lock').exists()
    assert (settings.runtime_dir / 'license-defaults.lock').exists()
    fresh = TestClient(create_app(settings, enqueuer=lambda task_id: None))
    assert fresh.get('/api/v1/licenses/defaults').json() == defaults
    options = fresh.get('/api/v1/licenses/options').json()
    assert next(p['version'] for p in options['products'] if p['name'] == 'fbasecman') == '2.0'
    response = fresh.post('/api/v1/licenses/generate', json={
        'license_version': '1.1', 'start_at': '2026-10-08', 'purpose': '本次覆盖',
        'products': [{'name': 'fbasecman', 'version': '2.0', 'expiration_at': '2036-10-08'}],
        'mac_addrs': ['02:42:8e:0f:0b:1b'], 'password': password})
    assert response.status_code == 200, response.text
    _, raw, footer = _decode_license_body(response.content)
    assert json.loads(raw)['products'][0]['version'] == '2.0'
    assert '厂商: 自定义厂商' in footer
    assert 'License用途: 本次覆盖' in footer
    request = {'license_version': '1.1', 'start_at': '2026-10-08',
               'products': [{'name': 'fbasecman', 'version': '2.1', 'expiration_at': '2036-10-08'}],
               'mac_addrs': ['02:42:8e:0f:0b:1b'], 'password': password}
    assert fresh.post('/api/v1/licenses/generate', json=request).status_code == 200
    request['products'][0]['version'] = '   '
    assert fresh.post('/api/v1/licenses/generate', json=request).status_code == 422
    request['products'][0].update(name='uninstalled', version='1.0')
    assert fresh.post('/api/v1/licenses/generate', json=request).status_code == 422


def test_invalid_defaults_leave_saved_configuration_unchanged(tmp_path):
    settings, client = client_for(tmp_path)
    defaults = client.get('/api/v1/licenses/defaults').json()
    assert client.put('/api/v1/licenses/defaults', json=defaults).status_code == 200
    before = defaults_path(settings).read_bytes()
    changes = [
        lambda d: d['products']['fbasecman'].update(default_version=' '),
        lambda d: d.update(license_version='1.99'),
        lambda d: d.update(mac_addrs=['not-a-mac']),
        lambda d: d.update(mac_addrs=['aa:bb:cc:dd:ee:ff', 'AA:BB:CC:DD:EE:FF']),
        lambda d: d.update(password='must-not-be-saved'),
        lambda d: d['validity'].update(years=-1),
        lambda d: d['products'].update(uninstalled=d['products']['fbasecman']),
    ]
    for change in changes:
        value = json.loads(json.dumps(defaults))
        change(value)
        assert client.put('/api/v1/licenses/defaults', json=value).status_code == 422
        assert defaults_path(settings).read_bytes() == before


def test_removed_products_are_hidden_but_saved_overrides_are_retained(tmp_path, monkeypatch):
    import platform_app.license_defaults as module
    settings, client = client_for(tmp_path)
    defaults = client.get('/api/v1/licenses/defaults').json()
    defaults['products']['fbasecman']['default_version'] = '1.7'
    assert client.put('/api/v1/licenses/defaults', json=defaults).status_code == 200
    original = module.descriptors
    monkeypatch.setattr(module, 'descriptors', lambda settings: {
        name: d for name, d in original(settings).items() if name != 'fbasecman'})
    visible = client.get('/api/v1/licenses/defaults').json()
    assert 'fbasecman' not in visible['products']
    assert all(p['name'] != 'fbasecman' for p in client.get('/api/v1/licenses/options').json()['products'])
    visible['purpose'] = 'updated'
    assert client.put('/api/v1/licenses/defaults', json=visible).status_code == 200
    assert json.loads(defaults_path(settings).read_text())['products']['fbasecman']['default_version'] == '1.7'


def test_revoked_default_key_cannot_be_saved_or_automatically_selected(tmp_path):
    settings, client = client_for(tmp_path)
    legacy_key_fixture(tmp_path)
    (settings.license_key_dir / "revoked.json").write_text(json.dumps({"1.1": "2026-10-08"}))
    assert '/api/v1/licenses/keys/{version}/revoke' not in client.get('/openapi.json').json()['paths']
    defaults = client.get('/api/v1/licenses/defaults').json()
    defaults['license_version'] = '1.1'
    assert client.put('/api/v1/licenses/defaults', json=defaults).status_code == 422
    options = client.get('/api/v1/licenses/options').json()
    assert options['key_versions'] == ['1.1']
    assert options['usable_key_versions'] == []


def test_generated_license_saved_bytes_match_download_and_replace_existing_file(tmp_path):
    settings, client = client_for(tmp_path)
    _, password = legacy_key_fixture(tmp_path)
    defaults = client.get('/api/v1/licenses/defaults').json()
    destination = tmp_path / '授权文件'
    defaults['output_directory'] = str(destination)
    assert client.put('/api/v1/licenses/defaults', json=defaults).status_code == 200
    request = {'license_version': '1.1', 'start_at': '2026-10-08',
               'products': [{'name': 'fbasecman', 'version': '2.0', 'expiration_at': '2036-10-08'}],
               'mac_addrs': ['02:42:8e:0f:0b:1b'], 'password': password}
    response = client.post('/api/v1/licenses/generate', json=request)
    assert response.status_code == 200
    assert not destination.exists()  # API saving requires explicit opt-in.
    request['save_to_directory'] = True
    response = client.post('/api/v1/licenses/generate', json=request)
    assert response.status_code == 200, response.text
    path = destination / 'license.dat'
    assert path.read_bytes() == response.content
    assert path.stat().st_mode & 0o777 == 0o600
    from urllib.parse import unquote
    assert unquote(response.headers['X-License-Saved-Path']) == str(path)
    path.write_bytes(b'old file')
    request['output_directory'] = str(destination)
    response = client.post('/api/v1/licenses/generate', json=request)
    assert response.status_code == 200
    assert path.read_bytes() == response.content
    blocked = tmp_path / 'not-a-directory'
    blocked.write_text('keep')
    request['output_directory'] = str(blocked)
    response = client.post('/api/v1/licenses/generate', json=request)
    assert response.status_code == 422
    assert '保存失败' in response.json()['detail']
    assert blocked.read_text() == 'keep'
    request['output_directory'] = 'relative/path'
    assert client.post('/api/v1/licenses/generate', json=request).status_code == 422


def test_set_default_key_preserves_other_defaults_and_rejects_invalid_keys(tmp_path):
    settings, client = client_for(tmp_path)
    legacy_key_fixture(tmp_path)
    defaults = client.get('/api/v1/licenses/defaults').json()
    defaults.update(vendor='保留厂商', purpose='保留用途', output_directory=str(tmp_path / 'lic'))
    assert client.put('/api/v1/licenses/defaults', json=defaults).status_code == 200
    result = client.put('/api/v1/licenses/default-key', json={'version': '1.1'})
    assert result.status_code == 200
    defaults['license_version'] = '1.1'
    assert client.get('/api/v1/licenses/defaults').json() == defaults
    for invalid in ['1.99', 'bad']:
        assert client.put('/api/v1/licenses/default-key', json={'version': invalid}).status_code == 422
    assert client.get('/api/v1/licenses/defaults').json() == defaults
