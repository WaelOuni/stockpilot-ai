from copy import deepcopy
import json
import pytest

from evals.assistant_v025.evidence import junit_results, proof_results, qualify
from evals.assistant_v025.campaign import digest


def test_exact_evidence_does_not_promote_neighbor_or_hide_failure(tmp_path):
    report = tmp_path / 'junit.xml'
    report.write_text('<testsuite><testcase classname="tests.test_direct" name="test_case[G10-01]"/>'
                      '<testcase classname="tests.test_direct" name="test_case[G10-02]"><failure/></testcase>'
                      '<testcase classname="tests.test_direct" name="test_case[G10-03]"><skipped/></testcase></testsuite>')
    nodes = junit_results([report])
    rows = [{'id': f'X{i}', 'status': 'SKIP'} for i in range(91)]
    rows[4]['status'] = 'FAIL'
    mapping = [{'id': row['id'], 'qualification': 'direct', 'tests': [f'tests/test_direct.py::test_case[G10-{i+1:02}]']} for i, row in enumerate(rows)]
    actual = qualify(deepcopy(rows), mapping, nodes, 'a' * 40, 'b' * 40)
    assert [row['status'] for row in actual[:5]] == ['PASS', 'FAIL', 'SKIP', 'SKIP', 'FAIL']
    assert actual[0]['layer_evidence']['head_sha'] == 'a' * 40
    mapping[0]['qualification'] = 'unsupported'
    assert qualify(deepcopy(rows), mapping, nodes, 'a' * 40, 'b' * 40)[0]['status'] == 'FAIL'


def test_repeated_execution_cannot_mask_an_earlier_failure(tmp_path):
    paths = [tmp_path / 'one.xml', tmp_path / 'two.xml']
    for path, result in zip(paths, ['<failure/>', '']):
        path.write_text(f'<testsuite><testcase classname="tests.test_direct" name="test_case">{result}</testcase></testsuite>')
    assert junit_results(paths)['tests/test_direct.py::test_case'] == 'FAIL'


def test_foreign_sha_or_corpus_cannot_supply_evidence(tmp_path):
    path = tmp_path / 'proof.json'
    report = {'head_sha': 'a'*40, 'corpus_sha256': digest(), 'cases': []}
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError): proof_results([path], 'b'*40)
    report['corpus_sha256'] = 'altered'
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError): proof_results([path], 'a'*40)


def test_browser_evidence_keeps_exact_spec_and_prompt_id(tmp_path):
    path = tmp_path / 'browser.xml'
    path.write_text('<testsuite><testcase classname="ux-e2e/assistant-corpus.spec.ts" name="G20-01"/></testsuite>')
    assert junit_results([path]) == {'frontend/ux-e2e/assistant-corpus.spec.ts::G20-01': 'PASS'}
