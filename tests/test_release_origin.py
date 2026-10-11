"""Origin proof and checkout regression using only ephemeral local Git repositories."""
from pathlib import Path
import subprocess

import pytest

from scripts.ci import release_guard as guard

ROOT = Path(__file__).resolve().parents[1]
SHA = 'a' * 40
OBJECT = 'c' * 40
REPO = 'test/repo'
REF = 'refs/tags/v0.2.1'


@pytest.mark.parametrize('boundary', ['valid', 'missing', 'lightweight', 'wrong_commit',
                                     'wrong_object', 'duplicate', 'untrusted_origin', 'network'])
def test_origin_proof_refuses_unverifiable_tags(monkeypatch, boundary):
    def git(*args):
        if args[0] == 'remote':
            return f'https://github.com/{REPO}.git' if boundary != 'untrusted_origin' else 'https://evil.example/repo'
        if args[0] == 'ls-remote':
            if boundary == 'network':
                raise guard.ReleaseRefused('Remote unavailable')
            if boundary == 'missing':
                return ''
            if boundary == 'lightweight':
                return f'{SHA}\t{REF}'
            remote_sha = 'b' * 40 if boundary == 'wrong_commit' else SHA
            remote_object = 'd' * 40 if boundary == 'wrong_object' else OBJECT
            result = f'{remote_object}\t{REF}\n{remote_sha}\t{REF}^{{}}'
            return result + f'\n{OBJECT}\t{REF}' if boundary == 'duplicate' else result
        if args == ('rev-parse', REF):
            return OBJECT
        if args == ('rev-parse', f'{REF}^{{commit}}'):
            return SHA
        if args == ('cat-file', '-t', REF):
            return 'tag'
        pytest.fail(f'Unexpected Git call: {args}')

    monkeypatch.setattr(guard, 'git', git)
    if boundary == 'valid':
        guard.verify_origin_tag(REPO, 'v0.2.1', SHA)
    else:
        with pytest.raises(guard.ReleaseRefused):
            guard.verify_origin_tag(REPO, 'v0.2.1', SHA)


@pytest.mark.parametrize('kind', ['annotated', 'lightweight', 'wrong_commit', 'checkout_flattened'])
def test_origin_tag_with_real_git_objects(tmp_path, monkeypatch, kind):
    """All synthetic objects stay in tmp_path; repository commits/tags are untouched."""
    remote = tmp_path / 'origin.git'
    runner = tmp_path / 'runner'

    def run(directory, *args, data=None):
        # CI's source checkout has depth 1. Accept its boundary only in fixtures.
        if args[0] == 'fetch':
            args = ('fetch', '--update-shallow', *args[1:])
        return subprocess.run(['git', '-C', str(directory), *args], input=data,
                              check=True, text=True, capture_output=True, timeout=30).stdout.strip()

    remote.mkdir()
    runner.mkdir()
    run(remote, 'init', '--bare')
    run(remote, 'fetch', '--no-tags', str(ROOT), 'HEAD')
    sha = run(ROOT, 'rev-parse', '--verify', 'HEAD^{commit}')
    assert guard.SHA.fullmatch(sha)
    assert run(remote, 'cat-file', '-t', sha) == 'commit'
    target = sha
    if kind == 'wrong_commit':
        # ROOT's real parent may not exist in a shallow checkout. Make a distinct
        # test commit with the imported commit as its available parent instead.
        tree = run(remote, 'rev-parse', '--verify', f'{sha}^{{tree}}')
        body = (f'tree {tree}\nparent {sha}\n'
                'author Test Fixture <test@example.invalid> 1700000000 +0000\n'
                'committer Test Fixture <test@example.invalid> 1700000000 +0000\n\nWrong commit\n')
        target = run(remote, 'hash-object', '-w', '-t', 'commit', '--stdin', data=body)
        assert guard.SHA.fullmatch(target) and target != sha
        assert run(remote, 'cat-file', '-t', target) == 'commit'
        assert run(remote, 'rev-parse', '--verify', f'{target}^') == sha
    if kind == 'lightweight':
        tag_object = target
    else:
        body = (f'object {target}\ntype commit\ntag v0.2.1\n'
                'tagger Test Fixture <test@example.invalid> 1700000000 +0000\n\nFixture\n')
        tag_object = run(remote, 'hash-object', '-w', '-t', 'tag', '--stdin', data=body)
    run(remote, 'update-ref', REF, tag_object)
    run(runner, 'init')
    run(runner, 'remote', 'add', 'origin', str(remote))
    run(runner, 'fetch', '--no-tags', 'origin', f'+{REF}:{REF}')
    if kind == 'checkout_flattened':
        # Exact fallback refspec seen in the failed GitHub run.
        run(runner, 'fetch', '--no-tags', 'origin', f'+{sha}:{REF}')
        assert run(runner, 'cat-file', '-t', REF) == 'commit'

    def git(*args):
        # Only the trusted URL check is mocked: all Git objects and advertisements are real.
        if args == ('remote', 'get-url', 'origin'):
            return f'https://github.com/{REPO}.git'
        return run(runner, *args)

    monkeypatch.setattr(guard, 'git', git)
    if kind == 'annotated':
        guard.verify_origin_tag(REPO, 'v0.2.1', sha)
    else:
        with pytest.raises(guard.ReleaseRefused):
            guard.verify_origin_tag(REPO, 'v0.2.1', sha)
    if kind == 'checkout_flattened':
        # SHA-only checkout retains the initial full-history tag fetch.
        run(runner, 'fetch', '--no-tags', 'origin', f'+{REF}:{REF}')
        guard.verify_origin_tag(REPO, 'v0.2.1', sha)
        assert run(remote, 'rev-parse', REF) == tag_object
