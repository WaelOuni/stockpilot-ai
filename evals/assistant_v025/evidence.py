"""Join exact corpus IDs to executed JUnit cases, never to suite-level success."""
import argparse
import asyncio
from collections import Counter
import json
import hashlib
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

from .campaign import ROOT, CORPUS, decision, digest, markdown_report, run_corpus


def junit_results(paths):
    results = {}
    for path in paths:
        for case in ET.parse(path).iter('testcase'):
            classname = case.get('classname', '')
            node = ('frontend/' + classname if classname.endswith('.spec.ts') else classname.replace('.', '/') + '.py') + '::' + case.get('name', '')
            status = 'FAIL' if case.find('failure') is not None or case.find('error') is not None else 'SKIP' if case.find('skipped') is not None else 'PASS'
            # Multiple attempts must not hide an earlier failure or skip.
            previous = results.get(node, 'PASS')
            results[node] = max((previous, status), key={'PASS': 0, 'SKIP': 1, 'FAIL': 2}.get)
    return results


def proof_results(paths, sha):
    results = {}
    for path in paths:
        proof = json.loads(path.read_text())
        if proof['head_sha'] != sha or proof['corpus_sha256'] != digest():
            raise ValueError('Evidence does not match the requested HEAD and corpus')
        for row in proof['cases']:
            for node, status in row.get('layer_evidence', {}).get('results', {}).items():
                if status == 'NOT_EXECUTED':
                    continue
                results[node] = max((results.get(node, 'PASS'), status), key={'PASS': 0, 'SKIP': 1, 'FAIL': 2}.get)
    return results


def model_results(paths, sha):
    """Real-provider trials are separate from mocks and from storage proofs."""
    results = {}
    originals = {row['id']: row for row in CORPUS}
    for path in paths:
        proof = json.loads(path.read_text())
        if proof.get('head_sha') != sha or proof.get('checkout_sha') != sha or proof.get('corpus_sha256') != digest():
            raise ValueError('Real-model evidence has a different HEAD or corpus')
        if not proof.get('model', {}).get('digest'):
            raise ValueError('Real-model identity digest required')
        if proof.get('working_tree_clean') is not True:
            raise ValueError('Real-model evidence must execute the clean, recorded commit')
        seen = set()
        for row in proof['cases']:
            identity = row['id']
            if identity in seen or identity not in {f'G15-{i:02}' for i in range(1, 11)}:
                raise ValueError('Unexpected or duplicate real-model corpus ID')
            seen.add(identity)
            if any(row.get(key) != originals[identity][key] for key in ('message', 'expected')):
                raise ValueError('Real-model input/expectation differs from original corpus')
            trials = row.get('trials', [])
            if not trials and row.get('status') == 'SKIP':
                status = 'SKIP'
            elif len(trials) < 3 or len({trial['seed'] for trial in trials}) != len(trials):
                raise ValueError('At least three distinct, recorded trial seeds required')
            else:
                outcomes = [trial['status'] for trial in trials] + [row['status']]
                if any(value not in ('PASS', 'FAIL', 'SKIP') for value in outcomes):
                    raise ValueError('Invalid real-model trial outcome')
                status = max(outcomes, key={'PASS': 0, 'SKIP': 1, 'FAIL': 2}.get)
            node = 'ollama:' + identity
            results[node] = max((results.get(node, 'PASS'), status), key={'PASS': 0, 'SKIP': 1, 'FAIL': 2}.get)
    return results


def qualify(records, mapping, executed, sha, checkout_sha):
    if not all(re.fullmatch('[0-9a-f]{40}', v) for v in (sha, checkout_sha)):
        raise ValueError('Exact source and checkout SHA required')
    by_id = {row['id']: row for row in records}
    assert len(mapping) == len({row['id'] for row in mapping}) == 91
    for entry in mapping:
        row = by_id[entry['id']]
        row['service_status'] = row['status']
        # These fields describe the read-only service harness, not a real
        # ORDER fixture which can explicitly prepare/confirm synthetic orders.
        row['service_harness_metadata'] = {key: row.pop(key) for key in (
            'role', 'tenant', 'prior_conversation', 'mutations_authorized',
            'expected_side_effects', 'permissions', 'http', 'provenance',
        ) if key in row}
        tests = entry.get('tests', [])
        row['layer_evidence'] = {**entry, 'head_sha': sha, 'checkout_sha': checkout_sha,
                                 'scope': 'Exact named test fixture and assertions; disposable Mongo/PG or synthetic HTTP only',
                                 'setup_effects': 'Synthetic setup may explicitly prepare/confirm orders; no protected data or deployments',
                                 'prompt_effects': entry.get('acceptance', ''),
                                 'results': {node: executed.get(node, 'NOT_EXECUTED') for node in tests}}
        outcomes = [executed.get(node, 'NOT_EXECUTED') for node in tests]
        qualification = entry.get('qualification', 'missing')
        if 'FAIL' in outcomes:
            row['status'] = 'FAIL'
            row['reason'] = 'Direct target test failed: ' + entry.get('acceptance', '')
        elif tests and all(value == 'PASS' for value in outcomes) and qualification in ('direct', 'unsupported'):
            row['status'] = 'PASS' if qualification == 'direct' else 'FAIL'
            row['reason'] = entry.get('reason') or entry.get('acceptance', '')
        else:
            # Never downgrade a genuine failure into an unexecuted scenario.
            row['status'] = 'FAIL' if row['status'] == 'FAIL' or entry.get('baseline_status') == 'FAIL' else 'SKIP'
            row['reason'] = entry.get('reason') or 'Direct target evidence incomplete or not executed'
        row['decision'] = decision(row)
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--junit', type=Path, action='append', default=[])
    parser.add_argument('--proof', type=Path, action='append', default=[])
    parser.add_argument('--model-proof', type=Path, action='append', default=[])
    parser.add_argument('--sha', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--markdown', type=Path)
    args = parser.parse_args()
    if not args.junit and not args.proof and not args.model_proof:
        parser.error('At least one executed JUnit report or SHA-matched proof is required')
    checkout = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    executed = proof_results(args.proof, args.sha)
    for node, status in junit_results(args.junit).items():
        executed[node] = max((executed.get(node, 'PASS'), status), key={'PASS': 0, 'SKIP': 1, 'FAIL': 2}.get)
    for node, status in model_results(args.model_proof, args.sha).items():
        executed[node] = max((executed.get(node, 'PASS'), status), key={'PASS': 0, 'SKIP': 1, 'FAIL': 2}.get)
    records = qualify(asyncio.run(run_corpus()), json.loads((ROOT / 'layer_evidence.json').read_text()),
                      executed, args.sha, checkout)
    sources = []
    for path in [*args.proof, *args.model_proof]:
        source = json.loads(path.read_text())
        sources.append({'name': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                        'head_sha': source['head_sha'], 'checkout_sha': source.get('checkout_sha'),
                        'model': source.get('model')})
    for row in records:
        row['decision'] = decision(row)
        if sources and 'layer_evidence' in row:
            # This checkout ran the consolidation, not every imported test.
            row['layer_evidence']['consolidation_checkout_sha'] = row['layer_evidence'].pop('checkout_sha')
    report = {'head_sha': args.sha, 'checkout_sha': checkout, 'source_proofs': sources, 'corpus_sha256': digest(),
              'counts': dict(Counter(row['status'] for row in records)), 'cases': records}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if args.markdown:
        args.markdown.write_text(markdown_report(records))
    print(json.dumps({'head_sha': args.sha, 'checkout_sha': checkout, 'counts': report['counts']}))
    for row in records:
        if 'layer_evidence' in row:
            print(row['id'], row['status'], json.dumps(row['layer_evidence']['results']))


if __name__ == '__main__':
    main()
