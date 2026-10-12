"""Configuration and opt-in HTTP checks; never start or connect to a real database."""
import json
import os
import re
from pathlib import Path
import subprocess
import uuid

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_database_bindings_preserve_storage_and_replica_set():
    compose = yaml.safe_load((ROOT / 'docker-compose.yml').read_text())
    mongo, postgres = (compose['services'][name] for name in ('mongodb', 'postgres'))
    assert mongo['ports'] == ['127.0.0.1:27017:27017']
    assert postgres['ports'] == ['127.0.0.1:5432:5432']
    assert mongo['command'] == ['mongod', '--replSet', 'rs0', '--bind_ip_all']
    assert 'mongodb_data:/data/db' in mongo['volumes']
    assert postgres['volumes'] == ['postgres_data:/var/lib/postgresql/data']


def test_gateway_has_no_database_or_dev_configuration():
    gateway = yaml.safe_load((ROOT / 'docker/compose.demo-gateway.yml').read_text())['services']['gateway']
    assert gateway['profiles'] == ['local-preparation']
    assert gateway['network_mode'] == 'host'
    assert 'ports' not in gateway and 'environment' not in gateway
    assert gateway['read_only'] and gateway['cap_drop'] == ['ALL']
    assert gateway['volumes'] == ['./demo-gateway.conf:/etc/nginx/conf.d/default.conf:ro']
    config = (ROOT / 'docker/demo-gateway.conf').read_text()
    assert 'listen 127.0.0.1:8086;' in config
    assert 'proxy_pass http://127.0.0.1:8003/health;' in config
    assert 'set $args "";' in config
    assert 'proxy_pass_request_headers off;' in config
    assert 'access_log off;' in config and 'error_log /dev/null;' in config


@pytest.fixture(scope='module')
def gateway(tmp_path_factory):
    if os.environ.get('DEMO_GATEWAY_HTTP_TESTS') != '1':
        pytest.skip('Opt-in Docker test: DEMO_GATEWAY_HTTP_TESTS=1; no published ports or databases')
    temporary = tmp_path_factory.mktemp('demo-gateway')
    config = (ROOT / 'docker/demo-gateway.conf').read_text()
    config += '''\nserver {
      listen 127.0.0.1:8003;
      location = /health {
        add_header Set-Cookie "must-not-escape=1";
        return 200 "$request_method|$request_uri|$http_authorization|$http_cookie|$http_forwarded|$http_x_forwarded_for|$http_cf_access_jwt_assertion|$http_x_stockpilot_dev_login";
      }
    }\n'''
    (temporary / 'default.conf').write_text(config)
    name = 'stockpilot-gateway-test-' + uuid.uuid4().hex[:12]
    image = os.environ.get('DEMO_GATEWAY_TEST_IMAGE', 'stockpilot-web:v11-local-audit')
    def docker(*args, **kwargs):
        return subprocess.run(['docker', *args], check=True, capture_output=True, **kwargs)
    docker('run', '-d', '--name', name, '--network', 'none', '--read-only', '--tmpfs', '/tmp',
           '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
           '-v', f'{temporary}/default.conf:/etc/nginx/conf.d/default.conf:ro',
           '--entrypoint', 'nginx', image, '-g', 'daemon off;')
    try:
        docker('exec', name, 'nginx', '-t')
        inspect = json.loads(docker('inspect', name).stdout)[0]
        assert inspect['HostConfig']['NetworkMode'] == 'none'
        assert not inspect['HostConfig']['PortBindings']
        def request(method, path, headers=''):
            raw = f'{method} {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n{headers}\r\n'
            response = docker('exec', '-i', name, 'nc', '-w', '1', '127.0.0.1', '8086', input=raw.encode()).stdout.decode()
            status = int(response.split(' ', 2)[1])
            return status, response
        yield request
        result = docker('logs', name)
        logs = (result.stdout + result.stderr).decode()
        assert 'fixture-private-' not in logs
    finally:
        docker('rm', '-f', name)  # Only the uniquely named network-none test container, no volumes.


@pytest.mark.parametrize('path', ['/', '/assistant', '/products', '/products/BOSCH-X100', '/orders', '/orders/test-123', '/healthz'])
def test_local_frontend_allowlist(gateway, path):
    assert gateway('GET', path)[0] == 200


def test_health_proxy_drops_credentials_identity_and_cookies(gateway):
    status, response = gateway('GET', '/api/health?token=fixture-private-query',
        'Authorization: Bearer fixture-private-token\r\nCookie: fixture-private-cookie\r\n'
        'Forwarded: for=fixture-private-peer\r\nX-Forwarded-For: fixture-private-peer\r\n'
        'Cf-Access-Jwt-Assertion: fixture-private-identity\r\nX-StockPilot-Dev-Login: fixture-private-key\r\n')
    assert status == 200 and 'GET|/health||||||' in response
    assert 'fixture-private-' not in response and 'Set-Cookie:' not in response


def test_compiled_assets_have_no_dev_plugin_and_safe_headers(gateway):
    status, html = gateway('GET', '/')
    assert status == 200 and 'Cache-Control: no-store' in html
    assert 'X-Content-Type-Options: nosniff' in html
    assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', html)
    assert assets
    for path in assets:
        status, compiled = gateway('GET', path)
        assert status == 200
        for marker in ('__stockpilot_dev_login', 'stockpilot-dev-login', 'X-StockPilot-Dev-Login'):
            assert marker not in compiled
    assert gateway('HEAD', '/products')[0] == 200


@pytest.mark.parametrize('path', [
    '/admin', '/admin/login', '/backoffice/session/login', '/api/backoffice/session/login',
    '/docs', '/redoc', '/openapi.json', '/api/docs', '/api/openapi.json',
    '/chat', '/classify', '/api/chat', '/api/classify', '/api/assistant', '/api/products',
    '/api/orders', '/api/orders/test/status', '/api/orders/test/cancel', '/api/customer/orders/test/cancel',
    '/__stockpilot_dev_login', '/@vite/client', '/src/main.ts', '/.env',
    '/ADMIN/login', '/API/health', '//api/health', '/api//health', '/api/%68ealth',
    '/%61dmin/login', '/api/../api/health', '/api/%252e%252e/health', '/api/health/',
    '/assets/../index.html', '/assets/test.js.map', '/api\\health', '/unknown',
])
def test_closed_routes_and_noncanonical_paths(gateway, path):
    assert gateway('GET', path)[0] in (400, 403)


@pytest.mark.parametrize('method', ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE'])
@pytest.mark.parametrize('path', ['/', '/products', '/api/health', '/api/orders/test/cancel'])
def test_unlisted_methods_never_reach_api(gateway, method, path):
    assert gateway(method, path)[0] in (400, 403, 405)


def test_snapshot_refuses_repository_and_does_not_contact_docker(monkeypatch):
    from scripts import demo_network_snapshot as snapshot
    monkeypatch.setattr('sys.argv', ['snapshot', str(ROOT / 'private-snapshot')])
    monkeypatch.setattr(snapshot, 'collect', lambda: pytest.fail('Must refuse before Docker'))
    assert snapshot.main() == 1


def test_snapshot_files_private_and_loopback_plan_preserves_rollback(tmp_path, monkeypatch, capsys):
    from scripts import demo_network_snapshot as snapshot
    destination = tmp_path / 'private'
    state = {'fingerprint': {'identity': 'test-only'}}
    compose = {'services': {
        name: {'ports': [{'host_ip': '0.0.0.0'}], 'image': 'sha256:test',
               'environment': {'PRIVATE_TEST_ONLY': 'fixture-private-secret'},
               'volumes': ['test-volume:/data']} for name in ('mongodb', 'postgres')
    }}
    monkeypatch.setattr('sys.argv', ['snapshot', str(destination)])
    monkeypatch.setattr(snapshot, 'collect', lambda: (state, compose))
    assert snapshot.main() == 0
    assert destination.stat().st_mode & 0o777 == 0o700
    for file in destination.iterdir():
        assert file.stat().st_mode & 0o777 == 0o600
    rollback = json.loads((destination / 'rollback.compose.json').read_text())
    plan = json.loads((destination / 'loopback.compose.json').read_text())
    assert rollback == compose
    for name in ('mongodb', 'postgres'):
        assert plan['services'][name]['ports'][0]['host_ip'] == '127.0.0.1'
        for key in ('image', 'environment', 'volumes'):
            assert plan['services'][name][key] == rollback['services'][name][key]
    assert 'fixture-private-secret' not in capsys.readouterr().out


def test_snapshot_mismatch_creates_no_files(tmp_path, monkeypatch):
    from scripts import demo_network_snapshot as snapshot
    baseline = tmp_path / 'before.json'
    baseline.write_text(json.dumps({'fingerprint': {'identity': 'before'}}))
    destination = tmp_path / 'after'
    monkeypatch.setattr('sys.argv', ['snapshot', str(destination), '--compare', str(baseline)])
    monkeypatch.setattr(snapshot, 'collect', lambda: ({'fingerprint': {'identity': 'other'}}, {}))
    assert snapshot.main() == 1
    assert not destination.exists()


def test_snapshot_uses_canonical_wsl_bind_not_desktop_translation():
    from scripts import demo_network_snapshot as snapshot
    mount = {'Type': 'bind', 'Destination': '/opt/stockpilot/init-replica-set.js',
             'Source': '/run/desktop/internal-unusable-source', 'RW': False}
    configured = [{'type': 'bind', 'target': mount['Destination'],
                   'source': str(ROOT / 'docker/init-replica-set.js')}]
    assert snapshot.bind_source(mount, configured) == str(ROOT / 'docker/init-replica-set.js')


@pytest.mark.parametrize('unsafe', ['missing', 'different-target', 'writable'])
def test_snapshot_refuses_ambiguous_or_unsafe_bind(unsafe):
    from scripts import demo_network_snapshot as snapshot
    mount = {'Destination': '/opt/stockpilot/init-replica-set.js', 'RW': unsafe == 'writable'}
    configured = [{'type': 'bind', 'target': mount['Destination'],
                   'source': str(ROOT / 'docker/init-replica-set.js')}]
    if unsafe == 'missing':
        configured = []
    if unsafe == 'different-target':
        mount['Destination'] = '/unexpected'
    with pytest.raises(ValueError):
        snapshot.bind_source(mount, configured)
