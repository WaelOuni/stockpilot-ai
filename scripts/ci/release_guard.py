"""Read-only release guards. Never create tags, packages, releases or deployments."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SEMVER = re.compile(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z')
SHA = re.compile(r'[0-9a-f]{40}\Z')
PIPELINE_PATHS = ('.github/workflows/release.yml', '.github/workflows/ci.yml',
                  'scripts/ci/release_guard.py')
REQUIRED_JOBS = ('backend-quality', 'eval-framework', 'frontend-quality',
                 'integration-tests', 'containers')


class ReleaseRefused(ValueError):
    """A credential-free diagnostic safe for Actions logs."""


def require(condition, message):
    if not condition:
        raise ReleaseRefused(message)


def git(*arguments):
    try:
        return subprocess.run(['git', *arguments], check=True, capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        raise ReleaseRefused('Git revision/ancestry/pipeline validation failed.') from None


def api(path):
    """Repository metadata with GITHUB_TOKEN. HTTP errors never disclose response bodies."""
    result = subprocess.run(['gh', 'api', '--method', 'GET', path], capture_output=True,
                            text=True, timeout=30)
    if result.returncode:
        raise ReleaseRefused('GitHub metadata unavailable; refusing release.')
    return json.loads(result.stdout)


def pages(path, key=None):
    result = []
    for page in range(1, 101):
        separator = '&' if '?' in path else '?'
        data = api(f'{path}{separator}per_page=100&page={page}')
        batch = data[key] if key else data
        result.extend(batch)
        if len(batch) < 100:
            return result
    raise ReleaseRefused('GitHub pagination exceeded safe limit.')


def verify_environment(repository):
    environment = api(f'repos/{repository}/environments/release')
    require(environment.get('name') == 'release', 'Expected existing release environment.')
    require(environment.get('deployment_branch_policy') == {
        'protected_branches': False, 'custom_branch_policies': True,
    }, 'Release environment must restrict selected tags, not branches/all refs.')
    require(not any(rule.get('type') == 'required_reviewers'
                    for rule in environment.get('protection_rules', [])),
            'Solo release environment must not require reviewers.')
    policies = pages(f'repos/{repository}/environments/release/deployment-branch-policies',
                     'branch_policies')
    require(len(policies) == 1 and policies[0].get('type') == 'tag'
            and policies[0].get('name') == 'v*',
            'Release environment must have exactly the tag policy v*, no branch policy.')


def verify_tag_rules(repository):
    matches = [rule for rule in pages(f'repos/{repository}/rulesets')
               if rule.get('name') == 'release-tags-protection']
    require(len(matches) == 1, 'Expected release-tags-protection ruleset.')
    rule = api(f"repos/{repository}/rulesets/{matches[0]['id']}")
    require(rule.get('target') == 'tag' and rule.get('enforcement') == 'active',
            'Release tag ruleset must be active.')
    require(rule.get('conditions', {}).get('ref_name') == {
        'include': ['refs/tags/v*'], 'exclude': [],
    }, 'Release tag ruleset scope must be v* without exclusions.')
    types = {item['type'] for item in rule.get('rules', [])}
    require({'update', 'deletion', 'non_fast_forward'} <= types and 'creation' not in types,
            'Release tags must allow creation and forbid update/delete/force push.')
    # GitHub hides bypass_actors from read-only tokens. Do not interpret omission as [].
    # Its absence is audited manually with an admin token before enabling publication.
    if 'bypass_actors' in rule:
        require(rule['bypass_actors'] == [], 'Release tag ruleset must not allow bypass.')


def verify_private_packages(repository):
    token = os.environ.get('GHCR_VISIBILITY_TOKEN', '')
    require(bool(token), 'Environment secret GHCR_VISIBILITY_TOKEN (classic read:packages) required.')
    owner = repository.split('/')[0]
    owner_type = api(f'repos/{repository}')['owner']['type']
    require(owner_type in ('User', 'Organization'), 'Unsupported repository owner type.')
    namespace = 'users' if owner_type == 'User' else 'orgs'
    for component in ('api', 'web'):
        name = f'stockpilot-{component}'
        request = Request(f'https://api.github.com/{namespace}/{owner}/packages/container/{name}',
                          headers={'Authorization': f'Bearer {token}',
                                   'Accept': 'application/vnd.github+json',
                                   'X-GitHub-Api-Version': '2022-11-28'})
        try:
            with urlopen(request, timeout=30) as response:
                package = json.load(response)
        except (HTTPError, URLError, TimeoutError):
            raise ReleaseRefused(f'Cannot prove {name} exists and is private; no new package bootstrap allowed.') from None
        require(package.get('name') == name and package.get('package_type') == 'container'
                and package.get('visibility') == 'private'
                and (package.get('repository') or {}).get('full_name', '').lower() == repository.lower(),
                f'{name} must already be private and linked to this repository.')


def validate_jobs(results, sha, expected_sha):
    require(SHA.fullmatch(sha or '') and sha == expected_sha, 'CI did not validate the exact tag SHA.')
    require(set(results) == set(REQUIRED_JOBS)
            and all(results[name].get('result') == 'success' for name in REQUIRED_JOBS),
            'All five mandatory CI jobs must succeed; skipped/cancelled are not success.')


def verify_origin_tag(repository, tag, sha):
    """Prove the annotation and exact commit from origin, not only cached local refs."""
    require(SEMVER.fullmatch(tag) and SHA.fullmatch(sha), 'Invalid tag/SHA for origin verification.')
    remote = git('remote', 'get-url', 'origin')
    require(remote in (f'https://github.com/{repository}', f'https://github.com/{repository}.git'),
            'Origin must be this GitHub repository, without embedded credentials.')
    reference = f'refs/tags/{tag}'
    advertised = {}
    for line in git('ls-remote', '--exit-code', 'origin', reference, f'{reference}^{{}}').splitlines():
        parts = line.split()
        require(len(parts) == 2 and SHA.fullmatch(parts[0])
                and parts[1] in (reference, f'{reference}^{{}}')
                and parts[1] not in advertised, 'Invalid origin tag advertisement.')
        advertised[parts[1]] = parts[0]
    require(set(advertised) == {reference, f'{reference}^{{}}'},
            'Annotated tag required on origin; missing/lightweight remote tag refused.')
    require(advertised[f'{reference}^{{}}'] == sha, 'Origin tag commit differs from execution SHA.')
    require(git('rev-parse', reference) == advertised[reference],
            'Local tag object differs from origin; verify checkout uses the exact commit SHA.')
    require(git('cat-file', '-t', reference) == 'tag', 'Annotated tag required.')
    require(git('rev-parse', f'{reference}^{{commit}}') == sha, 'Tag commit differs from execution SHA.')


def gate(environment, *, publishing=False):
    repository = environment.get('GITHUB_REPOSITORY', '')
    require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository), 'Invalid repository context.')
    require(environment.get('GITHUB_EVENT_NAME') == 'workflow_dispatch', 'Only workflow_dispatch is permitted.')
    tag = environment.get('RELEASE_TAG', '')
    require(SEMVER.fullmatch(tag), 'Existing tag must be strict vMAJOR.MINOR.PATCH.')
    require(environment.get('GITHUB_REF') == f'refs/tags/{tag}',
            'Dispatch must run on the exact requested tag, never a branch or arbitrary SHA.')
    require(environment.get('GITHUB_WORKFLOW_REF') == f'{repository}/.github/workflows/release.yml@refs/tags/{tag}',
            'Workflow must come from the requested release tag in this repository.')
    sha = git('rev-parse', 'HEAD')
    require(SHA.fullmatch(sha) and sha == environment.get('GITHUB_SHA')
            and sha == environment.get('GITHUB_WORKFLOW_SHA'), 'Workflow, checkout and event SHA must match.')
    verify_origin_tag(repository, tag, sha)
    git('merge-base', '--is-ancestor', sha, 'origin/main')
    # Historical main commits may use an obsolete publication policy. Only the
    # currently approved pipeline is eligible; application source stays at the tag SHA.
    git('diff', '--exit-code', sha, 'origin/main', '--', *PIPELINE_PATHS)
    publish = environment.get('RELEASE_PUBLISH', 'false')
    require(publish in ('true', 'false'), 'Invalid publication mode.')
    if publish == 'true':
        require(environment.get('RELEASE_CONFIRMATION') == f'PUBLISH {tag}', 'Explicit PUBLISH <tag> confirmation required.')
        require(environment.get('PUBLICATION_ENABLED') == 'true', 'RELEASE_PUBLICATION_ENABLED is not true.')
    else:
        require(not environment.get('RELEASE_CONFIRMATION'), 'Dry-run must have no publication confirmation.')
    verify_environment(repository)
    verify_tag_rules(repository)
    if publishing:
        require(publish == 'true', 'Dry-run cannot enter a publication step.')
        require(environment.get('VALIDATED_SHA') == sha
                and environment.get('VALIDATE_RESULT') == 'success'
                and environment.get('BUILD_RESULT') == 'success', 'CI/build validation incomplete.')
        verify_private_packages(repository)  # Both before any push, and again before the draft.
        require(not any(release.get('tag_name') == tag
                        for release in pages(f'repos/{repository}/releases')),
                'Release already exists; refuse overwrite/reconciliation without a decision.')
    return {'sha': sha, 'version': tag[1:], 'tag': tag, 'publish': publish}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publication', action='store_true')
    arguments = parser.parse_args()
    try:
        result = gate(os.environ, publishing=arguments.publication)
        if not arguments.publication:
            with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:
                for key, value in result.items():
                    output.write(f'{key}={value}\n')
        print('Read-only release guards passed.' if arguments.publication
              else f"Release {result['tag']}: {'publication requested' if result['publish'] == 'true' else 'dry-run only'}.")
        return 0
    except ReleaseRefused as error:
        print(f'Release refused: {error}')
        return 1
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        # Values are controlled diagnostics; HTTP/subprocess payloads and credentials never printed.
        print('Release guard refused; verify tag/ref, approved pipeline, CI results, flag, '
              'environment/tag rules and existing private packages. No automatic repair.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
