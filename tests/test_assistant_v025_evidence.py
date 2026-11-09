from copy import deepcopy
import json
import pytest

from evals.assistant_v025.evidence import junit_results, model_results, proof_results, qualify
from evals.assistant_v025.campaign import CORPUS, decision, digest


def test_exact_evidence_does_not_promote_neighbor_or_hide_failure(tmp_path):
    report = tmp_path / 'junit.xml'
    report.write_text('<testsuite><testcase classname="tests.test_direct" name="test_case[G10-01]"/>'
                      '<testcase classname="tests.test_direct" name="test_case[G10-02]"><failure/></testcase>'
                      '<testcase classname="tests.test_direct" name="test_case[G10-03]"><skipped/></testcase></testsuite>')
    nodes = junit_results([report])
    rows = [{'id': f'X{i}', 'status': 'SKIP'} for i in range(91)]
    rows[4]['status'] = 'FAIL'
    rows[0]['mutations_authorized'] = False
    mapping = [{'id': row['id'], 'qualification': 'direct', 'tests': [f'tests/test_direct.py::test_case[G10-{i+1:02}]']} for i, row in enumerate(rows)]
    actual = qualify(deepcopy(rows), mapping, nodes, 'a' * 40, 'b' * 40)
    assert [row['status'] for row in actual[:5]] == ['PASS', 'FAIL', 'SKIP', 'SKIP', 'FAIL']
    assert actual[0]['layer_evidence']['head_sha'] == 'a' * 40
    assert 'mutations_authorized' not in actual[0]
    assert actual[0]['service_harness_metadata']['mutations_authorized'] is False
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


def test_baseline_failure_cannot_be_hidden_by_missing_new_evidence():
    rows = [{'id': f'X{i}', 'status': 'SKIP'} for i in range(91)]
    mapping = [{'id': r['id'], 'qualification': 'direct', 'tests': ['missing'], 'baseline_status': 'FAIL'} for r in rows]
    assert all(r['status'] == 'FAIL' for r in qualify(rows, mapping, {}, 'a'*40, 'b'*40))
    assert decision({'id':'G08-12','status':'FAIL'}) == 'ambiguous_clarification'
    assert decision({'id':'G15-11','status':'FAIL','layer_evidence':{'qualification':'unsupported','failure_decision':'security_regression'}}) == 'security_regression'


def model_report():
    original = next(r for r in CORPUS if r['id']=='G15-01')
    return {'head_sha':'a'*40,'checkout_sha':'a'*40,'corpus_sha256':digest(),
            'working_tree_clean':True,'model':{'name':'synthetic-test-provider','digest':'test-digest'},
            'cases':[{**original,'status':'PASS','trials':[{'seed':i,'status':'PASS'} for i in range(3)]}]}


def test_real_model_proof_keeps_all_trials_and_overall_failures(tmp_path):
    path=tmp_path/'model.json'; report=model_report()
    path.write_text(json.dumps(report))
    assert model_results([path],'a'*40)=={'ollama:G15-01':'PASS'}
    report['cases'][0]['trials'][1]['status']='FAIL'
    path.write_text(json.dumps(report))
    assert model_results([path],'a'*40)=={'ollama:G15-01':'FAIL'}
    report=model_report(); report['cases'][0]['status']='FAIL'
    path.write_text(json.dumps(report))
    assert model_results([path],'a'*40)=={'ollama:G15-01':'FAIL'}


@pytest.mark.parametrize('mutation',['dirty','wrong_input','wrong_expected','too_few','duplicate_seed','wrong_sha','wrong_checkout'])
def test_unqualified_model_reports_are_rejected(tmp_path,mutation):
    path=tmp_path/'model.json'; report=model_report(); case=report['cases'][0]
    if mutation=='dirty': report['working_tree_clean']=False
    elif mutation=='wrong_input': case['message']='Rewritten prompt'
    elif mutation=='wrong_expected': case['expected']='easier expectation'
    elif mutation=='too_few': case['trials'].pop()
    elif mutation=='duplicate_seed': case['trials'][1]['seed']=0
    elif mutation=='wrong_checkout': report['checkout_sha']='b'*40
    else: report['head_sha']='b'*40
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError): model_results([path],'a'*40)
