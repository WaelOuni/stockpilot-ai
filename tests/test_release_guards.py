"""Release configuration and real guard/publisher code, with all external boundaries mocked."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError

import pytest
import yaml

from scripts.ci import release_guard as guard

ROOT = Path(__file__).resolve().parents[1]
SHA = 'a' * 40
TAG_OBJECT = 'c' * 40
REPO = 'test/repo'
TAG = 'v0.1.0'


def workflow(name='release'):
    return yaml.load((ROOT / f'.github/workflows/{name}.yml').read_text(), Loader=yaml.BaseLoader)


@pytest.fixture
def context(monkeypatch):
    environment = dict(GITHUB_REPOSITORY=REPO, GITHUB_EVENT_NAME='workflow_dispatch',
                       GITHUB_REF=f'refs/tags/{TAG}', GITHUB_SHA=SHA, GITHUB_WORKFLOW_SHA=SHA,
                       GITHUB_WORKFLOW_REF=f'{REPO}/.github/workflows/release.yml@refs/tags/{TAG}',
                       RELEASE_TAG=TAG, RELEASE_PUBLISH='true', RELEASE_CONFIRMATION=f'PUBLISH {TAG}',
                       PUBLICATION_ENABLED='true', VALIDATED_SHA=SHA,
                       VALIDATE_RESULT='success', BUILD_RESULT='success')
    responses = {
        'environment': {'name': 'release', 'protection_rules': [{'type': 'branch_policy'}],
                        'deployment_branch_policy': {'protected_branches': False, 'custom_branch_policies': True}},
        'policies': [{'name': 'v*', 'type': 'tag'}],
        'ruleset': {'name': 'release-tags-protection', 'target': 'tag', 'enforcement': 'active',
                    'conditions': {'ref_name': {'include': ['refs/tags/v*'], 'exclude': []}},
                    'rules': [{'type': item} for item in ('update', 'deletion', 'non_fast_forward')],
                    'bypass_actors': []},
        'releases': [], 'calls': [], 'git_calls': [], 'packages': [],
    }

    def git(*args):
        responses['git_calls'].append(args)
        if args == ('rev-parse', 'HEAD'):
            return SHA
        if args == ('remote', 'get-url', 'origin'):
            return f'https://github.com/{REPO}.git'
        if args[0] == 'ls-remote':
            reference = args[-2]
            return f'{TAG_OBJECT}\t{reference}\n{SHA}\t{reference}^{{}}'
        if args[0] == 'cat-file':
            return 'tag'
        if args[0] == 'rev-parse':
            return SHA if args[1].endswith('^{commit}') else TAG_OBJECT
        return ''

    def api(path, **kwargs):
        responses['calls'].append(path)
        base = f'repos/{REPO}'
        if path == f'{base}/environments/release':
            return responses['environment']
        if path.startswith(f'{base}/environments/release/deployment-branch-policies?'):
            return {'branch_policies': responses['policies']}
        if path.startswith(f'{base}/rulesets?'):
            return [{'name': 'release-tags-protection', 'id': 42}]
        if path == f'{base}/rulesets/42':
            return responses['ruleset']
        if path == base:
            return {'owner': {'type': 'User'}}
        if path.startswith(f'{base}/releases?'):
            return responses['releases']
        pytest.fail(f'Unexpected REST call: {path}')

    def urlopen(request, **kwargs):
        responses['packages'].append(request.full_url)
        name = request.full_url.rsplit('/', 1)[1]
        return io.BytesIO(json.dumps({'name': name, 'package_type': 'container',
                                     'visibility': 'private', 'repository': {'full_name': REPO}}).encode())

    monkeypatch.setattr(guard, 'git', git)
    monkeypatch.setattr(guard, 'api', api)
    monkeypatch.setattr(guard, 'urlopen', urlopen)
    monkeypatch.setenv('GHCR_VISIBILITY_TOKEN', 'not-a-real-token')
    return environment, responses


@pytest.mark.parametrize('tag', ['v0.1.0', 'v0.2.0', 'v0.2.1', 'v1.0.0'])
def test_valid_existing_annotated_tag(context, tag):
    env, responses = context
    env.update(RELEASE_TAG=tag, GITHUB_REF=f'refs/tags/{tag}', RELEASE_CONFIRMATION=f'PUBLISH {tag}',
               GITHUB_WORKFLOW_REF=f'{REPO}/.github/workflows/release.yml@refs/tags/{tag}')
    assert guard.gate(env)['version'] == tag[1:]
    assert ('merge-base', '--is-ancestor', SHA, 'origin/main') in responses['git_calls']
    assert ('diff', '--exit-code', SHA, 'origin/main', '--', *guard.PIPELINE_PATHS) in responses['git_calls']


@pytest.mark.parametrize('tag', ['v01.2.3', 'v0.1.0-rc1', 'v0.1', '0.1.0', 'v0.1.0+metadata',
                               'v0.1.0\n', 'main', SHA, 'v0.1.0;echo bad'])
def test_invalid_semver_refused_before_git_or_api(context, tag):
    env, responses = context
    env['RELEASE_TAG'] = tag
    with pytest.raises(ValueError, match='strict'):
        guard.gate(env)
    assert not responses['git_calls'] and not responses['calls']


@pytest.mark.parametrize('event', ['push', 'pull_request', 'pull_request_target', 'workflow_run'])
def test_no_automatic_or_pr_publication(context, event):
    env, responses = context
    env['GITHUB_EVENT_NAME'] = event
    with pytest.raises(ValueError, match='workflow_dispatch'):
        guard.gate(env, publishing=True)
    assert not responses['calls'] and not responses['packages']


@pytest.mark.parametrize('updates', [
    {'GITHUB_REF': 'refs/heads/main'}, {'GITHUB_REF': 'refs/heads/feature'},
    {'GITHUB_REF': f'refs/tags/v0.2.0'}, {'GITHUB_REF': SHA},
    {'GITHUB_WORKFLOW_REF': f'{REPO}/.github/workflows/release.yml@refs/heads/feature'},
    {'GITHUB_WORKFLOW_SHA': 'b' * 40}, {'GITHUB_SHA': 'b' * 40},
    {'RELEASE_CONFIRMATION': ''}, {'RELEASE_CONFIRMATION': 'yes'},
    {'RELEASE_CONFIRMATION': 'PUBLISH v0.2.0'}, {'PUBLICATION_ENABLED': ''},
    {'PUBLICATION_ENABLED': 'false'}, {'RELEASE_PUBLISH': 'yes'},
])
def test_untrusted_ref_unconfirmed_or_disabled_publication(context, updates):
    env, _ = context
    env.update(updates)
    with pytest.raises(ValueError):
        guard.gate(env, publishing=True)


@pytest.mark.parametrize('boundary', ['ancestry', 'pipeline', 'lightweight', 'tag_sha'])
def test_non_main_or_obsolete_workflow_or_invalid_tag(context, monkeypatch, boundary):
    env, _ = context
    original = guard.git

    def git(*args):
        if args[0] == 'merge-base' and boundary == 'ancestry':
            raise guard.ReleaseRefused('Not on main')
        if args[0] == 'diff' and boundary == 'pipeline':
            raise guard.ReleaseRefused('Obsolete pipeline')
        if args[0] == 'cat-file' and boundary == 'lightweight':
            return 'commit'
        if args[0] == 'rev-parse' and args[1] != 'HEAD' and boundary == 'tag_sha':
            return 'b' * 40
        return original(*args)

    monkeypatch.setattr(guard, 'git', git)
    with pytest.raises(ValueError):
        guard.gate(env)


@pytest.mark.parametrize('change', ['reviewers', 'all_refs', 'protected_branches', 'branch_v',
                                   'wrong_tag', 'extra_branch', 'no_policy', 'missing_environment'])
def test_incorrect_environment_refused(context, change):
    env, responses = context
    if change == 'reviewers':
        responses['environment']['protection_rules'].append({'type': 'required_reviewers', 'reviewers': [{}]})
    elif change == 'all_refs':
        responses['environment']['deployment_branch_policy'] = None
    elif change == 'protected_branches':
        responses['environment']['deployment_branch_policy']['protected_branches'] = True
    elif change == 'branch_v':
        responses['policies'][0]['type'] = 'branch'
    elif change == 'wrong_tag':
        responses['policies'][0]['name'] = '*'
    elif change == 'extra_branch':
        responses['policies'].append({'type': 'branch', 'name': 'main'})
    elif change == 'missing_environment':
        responses['environment'] = {}
    else:
        responses['policies'] = []
    with pytest.raises(ValueError):
        guard.gate(env)


@pytest.mark.parametrize('change', ['disabled', 'evaluate', 'wrong_target', 'update', 'deletion',
                                   'non_fast_forward', 'creation', 'excluded', 'bypass'])
def test_tag_rules_must_really_protect_releases(context, change):
    env, responses = context
    rule = responses['ruleset']
    if change in ('disabled', 'evaluate'):
        rule['enforcement'] = change
    elif change == 'wrong_target':
        rule['target'] = 'branch'
    elif change == 'excluded':
        rule['conditions']['ref_name']['exclude'] = [f'refs/tags/{TAG}']
    elif change == 'creation':
        rule['rules'].append({'type': 'creation'})
    elif change == 'bypass':
        rule['bypass_actors'] = [{'actor_id': 1}]
    else:
        rule['rules'] = [item for item in rule['rules'] if item['type'] != change]
    with pytest.raises(ValueError):
        guard.gate(env)


def test_read_only_ruleset_does_not_claim_hidden_bypass_actors_are_empty(context):
    env, responses = context
    del responses['ruleset']['bypass_actors']
    assert guard.gate(env)['sha'] == SHA  # Manual admin audit is documented, not faked by this token.


@pytest.mark.parametrize('job', guard.REQUIRED_JOBS)
@pytest.mark.parametrize('result', ['failure', 'cancelled', 'skipped'])
def test_every_mandatory_ci_job_must_succeed(job, result):
    results = {name: {'result': 'success'} for name in guard.REQUIRED_JOBS}
    results[job]['result'] = result
    with pytest.raises(ValueError, match='five'):
        guard.validate_jobs(results, SHA, SHA)


def test_ci_success_is_bound_to_tag_sha():
    results = {name: {'result': 'success'} for name in guard.REQUIRED_JOBS}
    guard.validate_jobs(results, SHA, SHA)
    with pytest.raises(ValueError):
        guard.validate_jobs(results, 'b' * 40, SHA)
    del results['containers']
    with pytest.raises(ValueError):
        guard.validate_jobs(results, SHA, SHA)


@pytest.mark.parametrize('updates', [{'VALIDATED_SHA': 'b' * 40}, {'VALIDATE_RESULT': 'failure'},
                                   {'BUILD_RESULT': 'skipped'}])
def test_publication_rechecks_ci_results(context, updates):
    env, responses = context
    env.update(updates)
    with pytest.raises(ValueError, match='incomplete'):
        guard.gate(env, publishing=True)
    assert not responses['packages']


def test_dry_run_without_flag_or_registry_credentials_cannot_publish(context, monkeypatch):
    env, responses = context
    env.update(RELEASE_PUBLISH='false', RELEASE_CONFIRMATION='', PUBLICATION_ENABLED='')
    monkeypatch.delenv('GHCR_VISIBILITY_TOKEN')
    assert guard.gate(env)['publish'] == 'false'
    assert not responses['packages']
    with pytest.raises(ValueError, match='Dry-run'):
        guard.gate(env, publishing=True)
    assert not responses['packages']


@pytest.mark.parametrize('problem', ['public', 'internal', '404', '403', 'wrong_repo', 'missing_secret', 'web_public'])
def test_private_visibility_must_be_proven_for_both_packages(context, monkeypatch, problem):
    env, _ = context
    original = guard.urlopen

    def response(request, **kwargs):
        if problem in ('404', '403'):
            raise HTTPError(request.full_url, int(problem), '', {}, None)
        package = json.load(original(request, **kwargs))
        if problem in ('public', 'internal'):
            package['visibility'] = problem
        elif problem == 'wrong_repo':
            package['repository']['full_name'] = 'another/repo'
        elif problem == 'web_public' and package['name'] == 'stockpilot-web':
            package['visibility'] = 'public'
        return io.BytesIO(json.dumps(package).encode())

    monkeypatch.setattr(guard, 'urlopen', response)
    if problem == 'missing_secret':
        monkeypatch.delenv('GHCR_VISIBILITY_TOKEN')
    with pytest.raises(ValueError):
        guard.gate(env, publishing=True)


def test_existing_draft_refuses_reconciliation(context):
    env, responses = context
    responses['releases'] = [{'tag_name': TAG, 'draft': True}]
    with pytest.raises(ValueError, match='already exists'):
        guard.gate(env, publishing=True)
    assert len(responses['packages']) == 2


def test_organization_package_endpoint(context, monkeypatch):
    env, responses = context
    original = guard.api
    monkeypatch.setattr(guard, 'api', lambda path, **kw: {'owner': {'type': 'Organization'}}
                        if path == f'repos/{REPO}' else original(path, **kw))
    guard.gate(env, publishing=True)
    assert all('/orgs/test/packages/' in url for url in responses['packages'])


def test_main_outputs_and_errors_never_include_credentials(context, monkeypatch, tmp_path, capsys):
    env, _ = context
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv('GITHUB_OUTPUT', str(tmp_path / 'outputs'))
    monkeypatch.setattr('sys.argv', ['release_guard.py'])
    assert guard.main() == 0
    assert f'sha={SHA}' in (tmp_path / 'outputs').read_text()
    monkeypatch.setenv('RELEASE_CONFIRMATION', '')
    assert guard.main() == 1
    assert 'not-a-real-token' not in capsys.readouterr().out


def test_workflow_permissions_ref_and_ci_contract():
    config = workflow()
    assert set(config['on']) == {'workflow_dispatch'}
    assert config['on']['workflow_dispatch']['inputs']['publish']['default'] == 'false'
    assert set(config['on']['workflow_dispatch']['inputs']) == {'tag', 'publish', 'confirmation'}
    assert config['permissions'] == {'contents': 'read'}
    checkout = config['jobs']['gate']['steps'][0]['with']
    assert checkout['ref'] == '${{ github.sha }}'
    assert checkout['fetch-depth'] == '0'
    assert checkout['persist-credentials'] == 'false'
    assert config['concurrency']['group'] == 'stockpilot-release'
    for name, job in config['jobs'].items():
        if name != 'publish':
            assert job.get('permissions', config['permissions']) == {'contents': 'read'}
            assert 'environment' not in job
            assert 'GHCR_VISIBILITY_TOKEN' not in json.dumps(job)
        for step in job.get('steps', []):
            assert 'upload-artifact' not in step.get('uses', '')
            assert 'docker save' not in step.get('run', '')
    publish = config['jobs']['publish']
    for condition in ("github.event_name == 'workflow_dispatch'", "github.ref_type == 'tag'",
                      'inputs.publish == true', "needs.validate.result == 'success'",
                      "needs.build.result == 'success'", 'needs.validate.outputs.validated_sha == needs.gate.outputs.sha'):
        assert condition in publish['if']
    assert publish['environment'] == 'release'
    assert publish['permissions'] == {'contents': 'write', 'packages': 'write'}
    assert set(publish['needs']) == {'gate', 'validate', 'build'}
    assert publish['steps'][0]['with']['ref'] == '${{ needs.gate.outputs.sha }}'
    assert publish['steps'][0]['with']['persist-credentials'] == 'false'
    assert config['jobs']['validate']['with']['release_sha'] == '${{ needs.gate.outputs.sha }}'
    ci = workflow('ci')
    assert set(ci['jobs']['release-validation']['needs']) == set(guard.REQUIRED_JOBS)
    for name in guard.REQUIRED_JOBS:
        assert 'if' not in ci['jobs'][name]  # No mandatory check can be silently skipped.
        assert ci['jobs'][name]['steps'][0]['with']['ref'] == '${{ inputs.release_sha || github.sha }}'
    assert ci['on']['workflow_call']['outputs']['validated_sha']['value'] == '${{ jobs.release-validation.outputs.sha }}'


@pytest.mark.parametrize('failure', ['', 'web_push', 'digest_mismatch', 'visibility_after_push', 'existing_tag', 'registry_denied'])
def test_actual_publish_shell_with_only_mocked_external_commands(tmp_path, failure):
    # Execute the workflow's actual shell with shell functions replacing EVERY external
    # GitHub/registry/guard command. No tags, images or releases are really created.
    code = workflow()['jobs']['publish']['steps'][-1]['run']
    stubs = r'''
    python3() {
      printf 'guard\n' >> "$CALLS"
      if [ "$FAILURE" = visibility_after_push ] && grep -q 'push:' "$CALLS"; then return 1; fi
    }
    docker() {
      case "$1" in
        login) cat >/dev/null ;;
        logout|tag) ;;
        manifest)
          if [ "$FAILURE" = existing_tag ]; then return 0; fi
          if [ "$FAILURE" = registry_denied ]; then echo unauthorized >&2; return 1; fi
          echo 'manifest unknown' >&2; return 1 ;;
        push)
          printf 'push:%s\n' "$2" >> "$CALLS"
          if [ "$FAILURE" = web_push ] && [[ "$2" = *stockpilot-web* ]]; then return 1; fi ;;
        buildx)
          if [ "$FAILURE" = digest_mismatch ] && [[ "$4" = *sha-* ]]; then
            printf 'sha256:%064d\n' 2
          else printf 'sha256:%064d\n' 1; fi ;;
        *) return 99 ;;
      esac
    }
    jq() {
      "$TEST_PYTHON" -c 'import json,os,sys; d=json.load(open(sys.argv[-1])); assert d["isDraft"] is True and d["tagName"]==os.environ["TAG"] and d["targetCommitish"]==os.environ["SHA"]' "$@"
    }
    gh() {
      case "$1 $2" in
        'release create') printf 'draft:%s\n' "$*" >> "$CALLS" ;;
        'release view') printf '{"isDraft":true,"tagName":"%s","targetCommitish":"%s"}\n' "$TAG" "$SHA" ;;
        *) return 99 ;;
      esac
    }
    '''
    environment = dict(os.environ, SHA=SHA, VERSION='0.1.0', TAG=TAG, FAILURE=failure,
                       TEST_PYTHON=sys.executable, GH_TOKEN='not-a-real-token', GITHUB_REPOSITORY=REPO, GITHUB_REPOSITORY_OWNER='Test',
                       GITHUB_ACTOR='test', RUNNER_TEMP=str(tmp_path), GITHUB_STEP_SUMMARY=str(tmp_path / 'summary'),
                       CALLS=str(tmp_path / 'calls'))
    result = subprocess.run(['bash', '-c', stubs + code], env=environment, capture_output=True, text=True)
    calls = (tmp_path / 'calls').read_text()
    assert (result.returncode == 0) == (failure == '')
    assert calls.startswith('guard\n')
    assert ':latest' not in calls
    if failure:
        assert 'draft:' not in calls
        assert 'Release incomplete' in (tmp_path / 'summary').read_text()
        if failure in ('existing_tag', 'registry_denied'):
            assert 'push:' not in calls
    else:
        assert calls.count('push:') == 4
        assert f'--target {SHA} --draft --generate-notes' in calls
        assert calls.count('guard\n') == 3
        assert 'sha256:' in (tmp_path / 'summary').read_text()
    assert 'not-a-real-token' not in result.stdout + result.stderr
