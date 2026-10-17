"""Create/start/stop only the isolated V13.3 candidate; never touch existing installations."""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = Path('/home/waelko/deployments/stockpilot-candidate-v133')
PROJECT = 'stockpilot-v133-candidate'
DATABASE = 'stockpilot_candidate_v133'


def private_file(path, contents):
    path.write_text(contents)
    path.chmod(0o600)


def compose(root, *args, **kwargs):
    return subprocess.run(['docker', 'compose', '--env-file', str(root / 'compose.env'), '-f',
                           str(ROOT / 'docker/compose.candidate.yml'), '-p', PROJECT, *args],
                          check=True, **kwargs)


def verify(root):
    manifest = json.loads((root / 'candidate.json').read_text())
    if root.resolve() != DEFAULT or manifest['project'] != PROJECT or manifest['database'] != DATABASE:
        raise ValueError('Unexpected candidate identity; no action permitted')
    from scripts.candidate.guards import validate_config
    config = json.loads(compose(root, 'config', '--format', 'json', capture_output=True, text=True).stdout)
    validate_config(config, root, ROOT, manifest)
    ids = subprocess.check_output(['docker', 'ps', '-aq', '--filter', f'label=com.docker.compose.project={PROJECT}'], text=True).split()
    if ids:
        for container in json.loads(subprocess.check_output(['docker', 'inspect', *ids], text=True)):
            if container['Config']['Labels'].get('com.docker.compose.project') != PROJECT:
                raise ValueError('Unexpected container project')
            service = container['Config']['Labels'].get('com.docker.compose.service')
            if (service not in {'mongo','postgres','api','web','relay'}
                    or container['Config']['Labels'].get('com.docker.compose.project.config_files') != str(ROOT / 'docker/compose.candidate.yml')):
                raise ValueError('Unexpected candidate Compose provenance')
            if any(not name.startswith(PROJECT + '_') for name in container['NetworkSettings']['Networks']):
                raise ValueError('Candidate is attached to an unrelated network')
            for mount in container['Mounts']:
                if mount['Type'] == 'volume' and not mount['Name'].startswith(PROJECT + '_'):
                    raise ValueError('Unexpected candidate volume')
                source = mount.get('Source', '')
                if mount['Type'] == 'bind' and not (source.startswith(str(root) + '/') or source.startswith(str(ROOT / 'scripts/candidate') + '/')
                        or (source.startswith('/run/desktop/mnt/host/wsl/docker-desktop-bind-mounts/') and not mount['RW'])):
                    raise ValueError('Unexpected candidate mount')
    return manifest


def create(root, upstream, port):
    if port == 5191 or not 1024 <= port <= 65535:
        raise ValueError('Candidate requires a distinct nonprivileged port')
    if root.exists():
        raise ValueError('Candidate directory already exists; refusing overwrite')
    for name in ('network', 'volume'):
        existing = subprocess.check_output(['docker', name, 'ls', '--format', '{{.Name}}'], text=True)
        if any(value.startswith(PROJECT + '_') for value in existing.splitlines()):
            raise ValueError('Candidate resources already exist; refusing reuse')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', port))
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    root.mkdir(mode=0o700)
    (root / 'tls').mkdir(mode=0o700)
    (root / 'control').mkdir(mode=0o755)
    (root / 'control/mode').write_text('normal\n')
    identity = str(uuid4())
    password, mongo, signing, backoffice = [secrets.token_hex(32) for _ in range(4)]
    admin_password = secrets.token_urlsafe(24)
    from stockpilot.presentation.api.admin_auth import password_verifier
    manifest = {'project': PROJECT, 'database': DATABASE, 'instance_id': identity,
                'source': str(ROOT), 'revision': revision, 'url': f'https://localhost:{port}',
                'ollama_upstream': upstream, 'port': port}
    private_file(root / 'candidate.json', json.dumps(manifest, indent=2))
    private_file(root / 'credentials.json', json.dumps({'admin_username': 'candidate-admin',
        'admin_password': admin_password, 'customer_jwt_secret': signing, 'customer_id': 'candidate-v133-a'}))
    private_file(root / 'mongo.env', f'MONGO_INITDB_ROOT_USERNAME=candidate\nMONGO_INITDB_ROOT_PASSWORD={mongo}\n')
    private_file(root / 'postgres.env', f'POSTGRES_USER=candidate\nPOSTGRES_PASSWORD={password}\nPOSTGRES_DB={DATABASE}\n')
    private_file(root / 'mongo-key', base64.b64encode(secrets.token_bytes(512)).decode())
    # Individual file mounts permit read access inside the container; parent directory stays 0700.
    (root / 'mongo-key').chmod(0o444)
    env = {'DEPLOYMENT_ENVIRONMENT': 'local_demo', 'MONGO_URI': f'mongodb://candidate:{mongo}@mongo:27017/?authSource=admin&replicaSet=rs0&directConnection=true',
        'MONGO_DATABASE': DATABASE, 'POSTGRES_URL': f'postgresql://candidate:{password}@postgres:5432/{DATABASE}',
        'LANGGRAPH_CHECKPOINT_DB_URI': f'postgresql://candidate:{password}@postgres:5432/{DATABASE}',
        'CUSTOMER_JWT_SECRET': signing, 'BACKOFFICE_API_TOKEN': backoffice,
        'ADMIN_USERNAME': 'candidate-admin', 'ADMIN_PASSWORD_VERIFIER': password_verifier(admin_password),
        'ADMIN_SESSION_STORE': 'mongo', 'ADMIN_TRUSTED_ORIGIN': manifest['url'], 'ADMIN_ALLOW_LOCAL_HTTP': 'false',
        'HTTP_TRUSTED_HOSTS': '["localhost","127.0.0.1"]', 'OLLAMA_BASE_URL': 'http://relay:8080',
        'OLLAMA_MODEL': 'qwen2.5:7b', 'OLLAMA_EMBEDDING_MODEL': 'nomic-embed-text',
        'OTEL_ENABLED': 'false', 'LOG_FILE': '', 'OLLAMA_TIMEOUT_SECONDS': '120'}
    private_file(root / 'api.env', '\n'.join(f'{k}={v}' for k, v in env.items()) + '\n')
    private_file(root / 'compose.env', f'CANDIDATE_ROOT={root}\nCANDIDATE_SOURCE={ROOT}\nCANDIDATE_REVISION={revision[:12]}\nCANDIDATE_PORT={port}\nOLLAMA_UPSTREAM={upstream}\n')
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '90',
        '-keyout', str(root / 'tls/server.key'), '-out', str(root / 'tls/server.crt'),
        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1'],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (root / 'tls/server.key').chmod(0o444)
    config = (ROOT / 'docker/web.conf.template').read_text().replace('server {',
        f'map $http_origin $candidate_origin_denied {{ default 1; "" 0; "https://localhost:{port}" 0; }}\nserver {{', 1)
    config = config.replace('listen 8080;', 'listen 8443 ssl;\n  ssl_certificate /candidate/server.crt;\n  ssl_certificate_key /candidate/server.key;\n  if ($host !~ ^(localhost|127\\.0\\.0\\.1)$) { return 400; }\n  if ($candidate_origin_denied) { return 403; }\n  proxy_read_timeout 300s;')
    config = config.replace('proxy_set_header Host $host;', 'proxy_set_header Host $http_host;').replace('${API_UPSTREAM}', 'http://api:8000')
    edge_headers = ('  add_header X-Content-Type-Options "nosniff" always;\n'
                    '  add_header Referrer-Policy "no-referrer" always;\n'
                    '  add_header Cache-Control "no-store" always;\n'
                    '  proxy_hide_header X-Content-Type-Options;\n'
                    '  proxy_hide_header Referrer-Policy;\n'
                    '  proxy_hide_header Cache-Control;\n')
    config = config.replace('  proxy_read_timeout 300s;', '  proxy_read_timeout 300s;\n' + edge_headers)
    (root / 'web.conf').write_text(config)
    access_files(root)
    print(f'Created isolated candidate configuration: {root}; no secret values displayed.')


def access_files(root):
    values=json.loads((root/'credentials.json').read_text())
    private_file(root/'admin-login.json', json.dumps({key:values[key] for key in ('admin_username','admin_password')},indent=2))
    for action in ('start','stop','status'):
        path=root/(action+'.sh')
        path.write_text('#!/usr/bin/env bash\nset -euo pipefail\ncd -- '+__import__('shlex').quote(str(ROOT))+'\nexec .venv/bin/python -m scripts.candidate.manage '+action+'\n')
        path.chmod(0o700)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create', 'build', 'stores', 'start', 'stop', 'status'])
    parser.add_argument('--root', type=Path, default=DEFAULT)
    parser.add_argument('--upstream', default='http://host.docker.internal:11434')
    parser.add_argument('--port', type=int, default=5193)
    args = parser.parse_args()
    if args.root.resolve() != DEFAULT:
        raise ValueError('This launcher only manages the named V13.3 candidate')
    if args.action == 'create':
        create(args.root, args.upstream, args.port)
        return
    manifest = verify(args.root)
    if args.action == 'build':
        # Build the manifest's immutable source, even after this tooling worktree advances.
        archive = subprocess.check_output(['git', 'archive', '--format=tar', manifest['revision']], cwd=ROOT)
        for dockerfile, image in [('Dockerfile', 'api'), ('frontend/Dockerfile', 'web')]:
            subprocess.run(['docker', 'build', '-f', dockerfile, '-t', f'stockpilot-candidate-{image}:{manifest["revision"][:12]}',
                            '--label', f'org.opencontainers.image.revision={manifest["revision"]}', '-'], input=archive, check=True)
    elif args.action == 'stores':
        compose(args.root, 'up', '-d', '--wait', 'mongo', 'postgres')
    elif args.action == 'start':
        compose(args.root, 'up', '-d', '--wait')
        # The existing saver owns one long-lived PG connection. If PG restarted
        # later than the API, reopen that connection without changing checkpoints.
        starts = {}
        for service in ('postgres','api'):
            starts[service] = subprocess.check_output(['docker','inspect', PROJECT+'-'+service+'-1',
                '--format','{{.State.StartedAt}}'], text=True).strip()
        if starts['postgres'] > starts['api']:
            compose(args.root, 'restart', 'api')
            compose(args.root, 'up', '-d', '--wait', 'api')
    elif args.action == 'stop':
        compose(args.root, 'stop')
    else:
        compose(args.root, 'ps')


if __name__ == '__main__':
    main()
