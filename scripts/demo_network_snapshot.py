"""Read-only database identity/storage snapshot and private Compose rollback file.

Never starts, stops, recreates or seeds a service. Credentials in the rollback
file are private: directory must be new and outside the repository.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CONTAINERS = {'mongodb': 'stockpilot-mongodb', 'postgres': 'stockpilot-postgres'}


def bind_source(mount, configured_mounts):
    """Use the WSL Compose source, never Docker Desktop's internal translation."""
    matches = [item for item in configured_mounts
               if item['type'] == 'bind' and item['target'] == mount['Destination']]
    if len(matches) != 1:
        raise ValueError('Bind mount must match exactly one Compose source.')
    source = Path(matches[0]['source']).resolve(strict=True)
    expected = (ROOT / 'docker/init-replica-set.js').resolve(strict=True)
    if mount['Destination'] != '/opt/stockpilot/init-replica-set.js' or source != expected:
        raise ValueError('Unexpected database bind source.')
    if not source.is_file() or not os.access(source, os.R_OK) or source.stat().st_mode & 0o022:
        raise ValueError('Bind source must be readable and not group/world writable.')
    if mount['RW']:
        raise ValueError('Replica-set initialization script must remain read-only.')
    return str(source)


def run(*args, **kwargs):
    return subprocess.run(args, capture_output=True, check=True, timeout=30, **kwargs).stdout.decode().strip()


def collect():
    compose = json.loads(run('docker', 'compose', '--project-directory', str(ROOT),
                             '-f', str(ROOT / 'docker-compose.yml'), 'config', '--format', 'json'))
    containers = json.loads(run('docker', 'inspect', *CONTAINERS.values()))
    by_name = {item['Name'].lstrip('/'): item for item in containers}
    compose['services'] = {key: compose['services'][key] for key in CONTAINERS}
    compose['volumes'] = {}
    storage = {}
    ports = {}
    container_ids = {}
    for service, name in CONTAINERS.items():
        item = by_name[name]
        if not item['State']['Running']:
            raise ValueError('Both database containers must be running.')
        definition = compose['services'][service]
        definition['image'] = item['Image']  # Preserve the exact local image, no pull needed.
        definition['environment'] = dict(value.split('=', 1) for value in item['Config']['Env'])
        configured_mounts = definition['volumes']
        definition['volumes'] = []
        mounts = []
        for index, mount in enumerate(item['Mounts']):
            entry = {'type': mount['Type'], 'target': mount['Destination'], 'read_only': not mount['RW']}
            if mount['Type'] == 'volume':
                alias = f'kept-{service}-{index}'
                compose['volumes'][alias] = {'external': True, 'name': mount['Name']}
                entry['source'] = alias
                source = mount['Name']
            elif mount['Type'] == 'bind':
                entry['source'] = bind_source(mount, configured_mounts)
                source = mount['Source']  # Compare effective Docker mounts unchanged.
            else:
                raise ValueError('Unexpected database mount type.')
            definition['volumes'].append(entry)
            mounts.append([mount['Type'], source, mount['Destination'], mount['RW']])
        published = []
        for target, bindings in item['HostConfig']['PortBindings'].items():
            for binding in bindings or []:
                published.append({'target': int(target.split('/')[0]), 'published': binding['HostPort'],
                                  'host_ip': binding['HostIp'] or '0.0.0.0', 'protocol': target.split('/')[1]})
        definition['ports'] = published
        storage[service] = {'image': item['Image'], 'mounts': sorted(mounts)}
        ports[service] = item['NetworkSettings']['Ports']
        container_ids[service] = item['Id']
    mongo = json.loads(run('docker', 'exec', CONTAINERS['mongodb'], 'mongosh', '--quiet', '--eval',
        'const c=db.adminCommand({replSetGetConfig:1}).config; print(JSON.stringify({'
        'id:String(c.settings.replicaSetId),name:c._id,members:c.members.map(m=>m.host),'
        'primary:db.hello().isWritablePrimary,counts:Object.fromEntries('
        '["stockpilot","stockpilot_demo","stockpilot_e2e"].map(n=>[n,Object.fromEntries('
        'db.getSiblingDB(n).getCollectionNames().sort().map(c=>[c,db.getSiblingDB(n).getCollection(c).countDocuments({})]))]))}))'))
    pg_user = dict(value.split('=', 1) for value in by_name[CONTAINERS['postgres']]['Config']['Env'])['POSTGRES_USER']
    pg = run('docker', 'exec', '-i', CONTAINERS['postgres'], 'psql', '-X', '-At', '-U', pg_user,
             '-d', 'postgres', '-v', 'ON_ERROR_STOP=1',
             input=b'BEGIN READ ONLY; SELECT system_identifier FROM pg_control_system(); COMMIT;')
    pg_identity = next(line for line in pg.splitlines() if line.isdigit())
    if not mongo.pop('primary'):
        raise ValueError('MongoDB must be primary.')
    return {'fingerprint': {'storage': storage, 'mongo': mongo, 'postgres_identity': pg_identity},
            'ports': ports, 'container_ids': container_ids}, compose


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='New private directory outside the repository')
    parser.add_argument('--compare', type=Path, help='Previous snapshot.json; compare identity, storage and Mongo counts')
    args = parser.parse_args()
    directory = args.directory.resolve()
    try:
        if directory == ROOT or ROOT in directory.parents:
            raise ValueError('Snapshot must stay outside the repository.')
        snapshot, rollback = collect()
        loopback = copy.deepcopy(rollback)
        for service, port in [('mongodb', 27017), ('postgres', 5432)]:
            loopback['services'][service]['ports'] = [
                {'target': port, 'published': str(port), 'host_ip': '127.0.0.1', 'protocol': 'tcp'},
            ]
        if args.compare and json.loads(args.compare.read_text())['fingerprint'] != snapshot['fingerprint']:
            raise ValueError('Identity, storage or Mongo counts changed; investigate before resuming.')
        directory.mkdir(mode=0o700, parents=False, exist_ok=False)
        for name, contents in [('snapshot.json', snapshot), ('rollback.compose.json', rollback),
                               ('loopback.compose.json', loopback)]:
            descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as output:
                json.dump(contents, output, indent=2)
                output.write('\n')
        print('Private snapshot saved; identities, exact images, all volumes and Mongo counts recorded. No data changed.')
        return 0
    except (ValueError, KeyError, TypeError, StopIteration, OSError, subprocess.SubprocessError):
        print('Snapshot refused; check Docker access, target identities and private directory. Details withheld.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
