"""Exercise the release gate shell with mocked Git/REST boundaries; no real tags."""
import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SHA = 'a' * 40


@pytest.mark.parametrize('overrides,accepted', [
    ({}, True),
    ({'REF_NAME': 'v0.2.1'}, True),
    ({'REF_NAME': 'v01.2.3'}, False),
    ({'REF_NAME': 'v0.1.0-rc1'}, False),
    ({'REF_NAME': 'v0.1'}, False),
    ({'REF_NAME': '0.1.0'}, False),
    ({'PUBLICATION_ENABLED': ''}, False),
    ({'ANCESTOR_OK': 'no'}, False),
    ({'TAG_TYPE': 'commit'}, False),
    ({'TAG_SHA': 'b' * 40}, False),
    ({'ENVIRONMENT_OK': 'no'}, False),
    ({'ENVIRONMENT_EXISTS': 'no'}, False),
    ({'EVENT': 'workflow_dispatch', 'PUBLICATION_ENABLED': '',
      'ENVIRONMENT_EXISTS': 'no'}, True),
])
def test_release_gate_rejects_unsafe_inputs(tmp_path, overrides, accepted):
    config = yaml.load((ROOT / '.github/workflows/release.yml').read_text(),
                       Loader=yaml.BaseLoader)
    gate = config['jobs']['gate']['steps'][-1]['run']
    # Test the actual workflow shell. External commands are controlled boundaries;
    # jq's result models GitHub environment policy, not a remote environment.
    stubs = r'''
    git() {
      case "$1 $2" in
        'rev-parse HEAD') printf '%s\n' "$HEAD_SHA" ;;
        'merge-base --is-ancestor') test "$ANCESTOR_OK" = yes ;;
        'cat-file -t') printf '%s\n' "$TAG_TYPE" ;;
        rev-parse*) printf '%s\n' "$TAG_SHA" ;;
        *) return 99 ;;
      esac
    }
    gh() { test "$ENVIRONMENT_EXISTS" = yes; }
    jq() { test "$ENVIRONMENT_OK" = yes; }
    '''
    output = tmp_path / 'outputs'
    environment = dict(os.environ, REF_NAME='v0.1.0', EVENT='push',
                       PUBLICATION_ENABLED='true', HEAD_SHA=SHA, TAG_SHA=SHA,
                       TAG_TYPE='tag', ANCESTOR_OK='yes', ENVIRONMENT_OK='yes',
                       ENVIRONMENT_EXISTS='yes', GITHUB_OUTPUT=str(output),
                       RUNNER_TEMP=str(tmp_path), GITHUB_REPOSITORY='test/repo')
    environment.update(overrides)
    result = subprocess.run(['bash', '-c', stubs + gate], env=environment,
                            capture_output=True, text=True)
    assert (result.returncode == 0) is accepted
    if accepted:
        assert f'sha={SHA}' in output.read_text()
        if environment['EVENT'] == 'workflow_dispatch':
            assert 'version=dry-run' in output.read_text()


def test_release_does_not_export_private_images_as_public_artifacts():
    config = yaml.load((ROOT / '.github/workflows/release.yml').read_text(),
                       Loader=yaml.BaseLoader)
    assert set(config['on']) == {'push', 'workflow_dispatch'}
    assert config['permissions'] == {'contents': 'read'}
    for name, job in config['jobs'].items():
        if name != 'publish':
            assert job.get('permissions', config['permissions']) == {'contents': 'read'}
        for step in job.get('steps', []):
            assert 'upload-artifact' not in step.get('uses', '')
            assert 'docker save' not in step.get('run', '')
    publish = config['jobs']['publish']
    assert publish['if'] == "github.event_name == 'push'"
    assert publish['environment'] == 'release'
    assert publish['permissions'] == {'contents': 'write', 'packages': 'write'}
    assert set(publish['needs']) == {'gate', 'validate', 'build'}
    assert publish['steps'][0]['with']['ref'] == '${{ needs.gate.outputs.sha }}'
    assert publish['steps'][0]['with']['persist-credentials'] == 'false'
