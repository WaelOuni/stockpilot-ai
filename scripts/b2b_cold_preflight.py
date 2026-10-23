"""Read-only cold preflight. This module has no backup or lifecycle operation."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, UTC

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from scripts.wsl_launcher import WSL, WINDOWS_CWD, WslLaunchError, run_wsl, list_running
CSCRIPT = '/mnt/c/Windows/System32/cscript.exe'
SERVICES = {'mongo', 'test-postgres', 'keycloak-db', 'keycloak', 'bff', 'proxy'}
VOLUMES = {'mongo': {'stockpilot-b2b-local_mongo-data': '/data/db', 'stockpilot-b2b-local_mongo-config': '/data/configdb'},
           'test-postgres': {'stockpilot-b2b-local_postgres-data': '/var/lib/postgresql/data'},
           'keycloak-db': {'stockpilot-b2b-local_keycloak-data': '/var/lib/postgresql/data'}}

class Refused(RuntimeError):
    pass

def need(ok, code):
    if not ok:
        raise Refused(code)

def command(args):
    try:
        result = subprocess.run(args, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        raise Refused('command_unavailable_or_timeout') from None
    need(result.returncode == 0, 'command_failed')
    return result.stdout

def wsl_command(distribution, args):
    try:
        return run_wsl(distribution, args)
    except WslLaunchError as exc:
        raise Refused(exc.reason) from None

def running_distributions():
    try:
        return list_running()
    except WslLaunchError as exc:
        raise Refused(exc.reason) from None

def data(args):
    try:
        return json.loads(command(args))
    except (ValueError, UnicodeError):
        raise Refused('invalid_command_json') from None

def validate_windows(value, minimum):
    need(isinstance(value, dict) and value.get('version') == 1 and value.get('result') == 'PASS', 'windows_evidence_not_pass')
    free = value.get('free_bytes')
    need(type(free) is int and free >= minimum, 'physical_reserve_insufficient_or_unknown')
    procs = value.get('processes', {})
    need(procs.get('positive_control') is True and type(procs.get('process_count')) is int and procs['process_count'] > 0
         and type(procs.get('candidates_checked')) is int and procs['candidates_checked'] > 0
         and procs.get('concurrent_admin') == [], 'windows_process_evidence_incomplete')
    files = value.get('backing_files')
    need(isinstance(files, list) and len(files) == 2, 'two_backing_files_required')
    paths = []
    for item in files:
        path = item.get('path', '')
        need(isinstance(path, str) and re.match(r'^C:\\', path, re.I) and item.get('drive') == 'C'
             and type(item.get('length')) is int and item['length'] > 0, 'backing_file_unproven')
        paths.append(path.lower())
    need(len(set(paths)) == 2 and paths[0].endswith('\\ext4.vhdx') and paths[1].endswith('\\docker_data.vhdx'), 'backing_identity_unproven')
    return value

def windows(minimum):
    path = command(['wslpath', '-w', str(ROOT / 'scripts/b2b_windows_preflight.js')]).decode().strip()
    try:
        result = subprocess.run([CSCRIPT, '//nologo', path, uuid.uuid4().hex, str(minimum)], capture_output=True, timeout=60, cwd=str(WINDOWS_CWD))
    except (OSError, subprocess.SubprocessError):
        raise Refused('windows_helper_unavailable_or_timeout') from None
    if result.returncode != 0 or result.stderr:
        try:
            refused = json.loads(result.stdout)
            reason = refused.get('reason', '')
            if refused.get('result') == 'BLOCKED' and re.fullmatch(r'[a-z_]{1,80}', reason):
                if reason == 'concurrent_windows_administration' and type(refused.get('process_id')) is int and re.fullmatch(r'[a-zA-Z0-9_.-]{1,80}', refused.get('process_name', '')):
                    raise Refused('windows_helper_' + reason + ' pid=' + str(refused['process_id']) + ' name=' + refused['process_name'])
                raise Refused('windows_helper_' + reason)
        except (ValueError, UnicodeError, AttributeError, TypeError):
            pass
        raise Refused('windows_helper_failed')
    try:
        return validate_windows(json.loads(result.stdout), minimum)
    except (ValueError, UnicodeError):
        raise Refused('windows_helper_invalid_json') from None

def active_distros(raw):
    text = raw.decode('utf-16-le' if b'\x00' in raw else 'utf8').replace('\ufeff', '')
    names = [x.strip() for x in text.splitlines() if x.strip()]
    need(len(names) == 2 and set(names) == {'Ubuntu', 'docker-desktop'}, 'unobserved_running_wsl_distribution')
    return names

def linux_administration():
    excluded = set()
    pid = os.getpid()
    while pid and pid not in excluded:
        excluded.add(pid)
        try:
            pid = int((Path('/proc') / str(pid) / 'stat').read_text().split(') ', 1)[1].split()[1])
        except FileNotFoundError:
            break
    checked = 0
    for path in Path('/proc').glob('[0-9]*'):
        if int(path.name) in excluded:
            continue
        try:
            name = (path / 'comm').read_text().strip()
            if not re.fullmatch(r'(docker.*|python.*|node|bash|sh|cmd\.exe|wsl\.exe|pwsh.*|powershell.*|ssh|uv|uvx)', name, re.I):
                continue
            args = (path / 'cmdline').read_bytes().replace(b'\x00', b' ').decode(errors='strict')
            need(bool(args), 'linux_candidate_commandline_unknown')
            checked += 1
            need(not re.search(r'\bdocker(?:-compose)?(?:\.exe)?(?:\s|$)|stockpilot|b2b_interactive|b2b_upgrade_manifest|b2b_demo', args, re.I), 'concurrent_linux_administration')
        except FileNotFoundError:
            continue  # Process has exited, not an unreadable live process.
        except (PermissionError, UnicodeError):
            raise Refused('linux_candidate_unobservable') from None
    return {'candidates_checked': checked, 'concurrent_admin': []}

def validate_sources(rows, manifest):
    need(set(manifest['containers']) == SERVICES, 'source_service_set')
    source_ids = set(manifest['containers'].values())
    need(len(source_ids) == 6 and len(manifest['volumes']) == 4, 'source_resource_count')
    by_id = {r['Id']: r for r in rows}
    need(len(by_id) == len(rows) and source_ids <= set(by_id), 'source_container_identity')
    for service, cid in manifest['containers'].items():
        row = by_id[cid]
        need(row['Image'] == manifest['images'][service], 'source_image_identity')
        state = row['State']
        need(state['Status'] == 'exited' and state['Running'] is False and state['Pid'] == 0 and state['ExitCode'] == 255, 'source_not_stopped')
    for service, mounts in VOLUMES.items():
        row = by_id[manifest['containers'][service]]
        actual = {m['Name']: m['Destination'] for m in row['Mounts'] if m['Type'] == 'volume'}
        need(actual == mounts, 'volume_destination_identity')
        for volume in mounts:
            users = [r for r in rows if any(m.get('Name') == volume for m in r['Mounts'])]
            need(len(users) == 1 and users[0]['Id'] == row['Id'], 'volume_has_other_consumer')
    live = {r['Id'] for r in rows if r['State']['Running']}
    need(bool(live) and all(re.fullmatch(r'[a-f0-9]{64}', x) for x in source_ids | live), 'positive_control_or_ids_invalid')
    return source_ids, live

def validate_cgroups(text, source_ids, live):
    need(not any(x in text for x in source_ids), 'source_process_present')
    need(bool(live) and all(x in text for x in live), 'positive_cgroup_control_missing')

def cgroups(source_ids, live):
    # A partial /proc read must fail even if a positive control is found elsewhere.
    script = 'bad=0; for f in /proc/[0-9]*/cgroup; do content=$(cat "$f" 2>/dev/null) || { test ! -e "$f" || bad=1; continue; }; printf "%s\\n" "$content"; done; exit "$bad"'
    result = wsl_command('docker-desktop', ['sh', '-c', script]).decode()
    validate_cgroups(result, source_ids, live)
    return {'source_processes': 0, 'positive_controls': len(live), 'all_live_controls_present': True, 'proc_read_complete': True}

def safe_path(path, directory=False):
    need(all(not p.is_symlink() for p in (path, *path.parents)), 'symlink_path_refused')
    if directory:
        need(path.is_dir() and path.stat().st_uid == os.getuid() and path.stat().st_mode & 0o777 == 0o700, 'private_directory_required')

def private_facts(state):
    out = {}
    for path in sorted(state.iterdir()):
        need(path.is_file() and not path.is_symlink(), 'private_regular_files_required')
        st = path.stat()
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        out[path.name] = {'sha256': digest, 'bytes': st.st_size, 'mode': st.st_mode & 0o777, 'uid': st.st_uid, 'gid': st.st_gid, 'mtime_ns': st.st_mtime_ns}
    return out

def snapshot():
    ids = command(['docker', 'ps', '-aq', '--no-trunc']).decode().split()
    rows = data(['docker', 'inspect', *ids])
    containers = {r['Id']: {k:r.get(k) for k in ('Id', 'Name', 'Image', 'RestartCount')} | {
        'Mounts': sorted(r['Mounts'], key=lambda mount: json.dumps(mount, sort_keys=True)),
        'state': {k:r['State'].get(k) for k in ('Status', 'Running', 'Paused', 'Restarting', 'OOMKilled', 'Dead', 'Pid', 'ExitCode', 'Error', 'StartedAt', 'FinishedAt')},
        'ports': r['HostConfig'].get('PortBindings'), 'active_ports':r['NetworkSettings'].get('Ports'),
        'labels':r['Config'].get('Labels'), 'networks': r['NetworkSettings']['Networks']} for r in rows}
    volumes = command(['docker', 'volume', 'ls', '-q']).decode().split()
    networks = command(['docker', 'network', 'ls', '-q', '--no-trunc']).decode().split()
    images = sorted(set(command(['docker', 'image', 'ls', '-aq', '--no-trunc']).decode().split()))
    return rows, {'containers':containers, 'volumes':{r['Name']:r for r in data(['docker', 'volume', 'inspect', *volumes])},
       'networks':{r['Id']:r for r in data(['docker', 'network', 'inspect', *networks])},
       'images':{r['Id']:{k:(sorted(r.get(k) or []) if k in ('RepoTags','RepoDigests') else r.get(k)) for k in ('Id','Descriptor','RepoTags','RepoDigests','Created','Size')} for r in data(['docker','image','inspect',*images])}}

def run(evidence, destination):
    from scripts.b2b_interactive import InteractiveDemo
    os.umask(0o077)
    safe_path(evidence, directory=True)
    need(not any(evidence.iterdir()) and ROOT not in evidence.parents, 'empty_private_evidence_outside_git_required')
    def record(name, value):
        with (evidence / name).open('x') as stream:
            json.dump(value, stream, indent=2); stream.write('\n')
    d = InteractiveDemo()
    safe_path(d.state, directory=True)
    parent = d.state.parent / 'b2b-backups'
    safe_path(parent, directory=True)
    safe_path(destination)
    need(destination.parent == parent and destination.name.startswith('v1393-point-') and not os.path.lexists(destination), 'new_private_destination_required')
    lock = d.state.parent / 'b2b-demo.lock'
    safe_path(lock)
    need(lock.is_file() and lock.stat().st_uid == os.getuid() and lock.stat().st_mode & 0o777 == 0o600, 'unsafe_operator_lock')
    fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('cooperative_operation_already_active') from None
        distros = active_distros(running_distributions())
        linux = linux_administration()
        native = windows(36 * 1024**3)
        endpoint = command(['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}']).decode().strip()
        need(endpoint == 'unix:///var/run/docker.sock' and os.environ.get('DOCKER_HOST', endpoint) == endpoint, 'local_desktop_socket_required')
        d.load(); d.attest()
        need(d.manifest['revision'] == 'cb0c7eb37642bdcf2cc16a11edfdad3636c4d017', 'source_revision_identity')
        need({p.name for p in d.state.iterdir()} == {*d.manifest['files'], 'manifest.json'}, 'private_file_set')
        files_before = private_facts(d.state)
        rows, before = snapshot()
        record('before.json', before)
        record('private-file-hashes.json', files_before)
        source_ids, live = validate_sources(rows, d.manifest)
        process = cgroups(source_ids, live)
        free = {'windows': native['free_bytes'], 'ubuntu': shutil.disk_usage(d.state).free,
                'docker': int(wsl_command('docker-desktop', ['df', '-B1', '/mnt/docker-desktop-disk']).decode().splitlines()[-1].split()[3])}
        need(min(free.values()) >= 36 * 1024**3, 'physical_capacity_and_reserve')
        d.load(); d.attest()
        rows_after, after = snapshot()
        record('after.json', after)
        validate_sources(rows_after, d.manifest)
        cgroups(source_ids, live)
        active_distros(running_distributions())
        linux_administration()
        windows(36 * 1024**3)
        need(before == after, 'docker_metadata_changed')
        need(files_before == private_facts(d.state), 'private_source_changed')
        need(not os.path.lexists(destination), 'destination_created_concurrently')
        result = {'result':'READY TO RETRY COLD BACKUP', 'timestamp':datetime.now(UTC).isoformat(),
            'scope':'preflight-only; no archive, volume copy, image export or lifecycle operation',
            'source_containers': d.manifest['containers'], 'source_volumes': list(d.manifest['volumes']),
            'source_private_files':len(files_before), 'free_bytes':free, 'windows':native, 'linux':linux,
            'cgroups':process, 'running_distributions':distros,
            'unchanged':{k:True for k in before}, 'counts':{k:len(v) for k,v in before.items()},
            'destination_absent':str(destination), 'cooperative_lock_held_for_checks':True,
            'limit':'point-in-time evidence; cooperative lock cannot exclude arbitrary external API clients'}
        record('result.json', result)
        return result
    finally:
        os.close(fd)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.evidence_dir.absolute(), args.destination.absolute())
        print(json.dumps(result))
        return 0
    except Refused as exc:
        print(json.dumps({'result':'BLOCKED','reason':str(exc)}))
        return 1
    except Exception as exc:
        print(json.dumps({'result':'BLOCKED','reason':'unavailable_or_malformed_evidence','exception_type':type(exc).__name__}))
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
