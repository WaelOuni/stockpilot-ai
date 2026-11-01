"""Offline image-contract failures; Docker is mocked here, never a registry write."""
import io
import json
from pathlib import Path
import tarfile

import pytest

from scripts.ci import check_keycloak_image as image

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {name: (image.THEME / name).read_bytes() for name in image.THEME_FILES}
CID = 'c' * 64
SHA = 'a' * 40
SOURCE = 'https://github.com/test/repo'


def archive(problem=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as tar:
        for index, (name, content) in enumerate(EXPECTED.items()):
            if problem == 'missing' and index == 0:
                continue
            if problem == 'changed' and index == 0:
                content = b'changed theme'
            item = tarfile.TarInfo('stockpilot/' + name)
            item.size = len(content)
            tar.addfile(item, io.BytesIO(content))
        if problem and problem not in ('missing', 'changed'):
            name = {'absolute': '/stockpilot/secret', 'traversal': 'stockpilot/../secret',
                    'extra': 'stockpilot/login/private.pem', 'empty': '.',
                    'duplicate': 'stockpilot/login/footer.ftl'}.get(problem, 'stockpilot/link')
            item = tarfile.TarInfo(name)
            if problem == 'symlink':
                item.type = tarfile.SYMTYPE
                item.linkname = '/private/secret'
            if problem == 'hardlink':
                item.type = tarfile.LNKTYPE
                item.linkname = '/private/secret'
            tar.addfile(item)
    return output.getvalue()


def test_exact_theme_archive_is_accepted():
    image.verify_theme(archive(), EXPECTED)


@pytest.mark.parametrize('problem', ['missing', 'changed', 'absolute', 'traversal', 'extra', 'duplicate', 'symlink', 'hardlink', 'empty'])
def test_unsafe_or_unexpected_image_content_is_refused(problem):
    with pytest.raises(image.ImageRefused):
        image.verify_theme(archive(problem), EXPECTED)


@pytest.mark.parametrize('problem', ['', 'user', 'entrypoint', 'platform', 'volume', 'revision', 'source', 'theme', 'version', 'exit'])
def test_image_check_retains_native_runtime_and_cleans_only_its_container(monkeypatch, problem):
    calls = []
    metadata = {'Os': 'linux', 'Architecture': 'amd64', 'Config': {
        'User': '1000', 'Entrypoint': ['/opt/keycloak/bin/kc.sh'], 'Volumes': None,
        'Labels': {'org.opencontainers.image.revision': SHA, 'org.opencontainers.image.source': SOURCE},
    }}
    if problem == 'user': metadata['Config']['User'] = '0'
    if problem == 'entrypoint': metadata['Config']['Entrypoint'] = ['/bin/sh']
    if problem == 'platform': metadata['Architecture'] = 'arm64'
    if problem == 'volume': metadata['Config']['Volumes'] = {'/data': {}}
    if problem == 'revision': metadata['Config']['Labels']['org.opencontainers.image.revision'] = 'b' * 40
    if problem == 'source': metadata['Config']['Labels']['org.opencontainers.image.source'] = 'https://foreign.example'

    def command(*args):
        calls.append(args)
        assert args[0] == 'docker'
        if args[1:3] == ('image', 'inspect'): return json.dumps([metadata]).encode()
        if args[1] == 'create':
            assert '--read-only' in args and ('--network', 'none') == args[2:4]
            assert args[-2:] == ('fixture:local', '--version')
            return CID.encode()
        if args[1] == 'cp': return archive('changed' if problem == 'theme' else None)
        if args[1] == 'start': return b'Keycloak 99.0.0' if problem == 'version' else b'Keycloak 26.8.1\nJVM: fixture\n'
        if args[1] == 'inspect': return json.dumps([{'State': {'Status': 'exited', 'ExitCode': 1 if problem == 'exit' else 0}}]).encode()
        if args[1] == 'rm':
            assert args == ('docker', 'rm', '--force', CID)
            return b''
        pytest.fail('Unexpected Docker boundary')

    monkeypatch.setattr(image, 'command', command)
    if problem:
        with pytest.raises(image.ImageRefused): image.check('fixture:local', SHA, SOURCE)
    else:
        image.check('fixture:local', SHA, SOURCE)
    if any(call[1] == 'create' for call in calls):
        assert calls[-1] == ('docker', 'rm', '--force', CID)
    else:
        assert len(calls) == 1


def test_keycloak_context_excludes_realms_secrets_and_preserves_pinned_official_runtime():
    dockerfile = (ROOT / 'docker/keycloak/Dockerfile').read_text()
    instructions = [line for line in dockerfile.splitlines() if line and not line.startswith('#')]
    reference = 'quay.io/keycloak/keycloak:26.8.1@sha256:79c623a22abacf3fe6c00a5fb30f5b7843d717b234033b80995891c06116d244'
    assert reference in (ROOT / 'docker/compose.b2b-demo.yml').read_text()
    assert instructions == ['FROM ' + reference, 'COPY --chown=1000:0 themes/stockpilot /opt/keycloak/themes/stockpilot']
    lines = (ROOT / 'docker/keycloak/.dockerignore').read_text().splitlines()
    assert lines[0] == '**'
    assert set(lines[1:]) == {
        '!Dockerfile', '!themes/', '!themes/stockpilot/', '!themes/stockpilot/login/',
        '!themes/stockpilot/login/messages/',
        *('!themes/stockpilot/' + name for name in image.THEME_FILES),
    }
    assert 'parent=keycloak.v2' in (image.THEME / 'login/theme.properties').read_text()
