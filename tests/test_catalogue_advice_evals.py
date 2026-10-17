import asyncio

from evals.runners.catalogue_advice import evaluate_advice, load_advice_cases
from evals.runners.run import ROOT


def test_advice_evaluations_exercise_graph_and_detect_wrong_expected_facts():
    cases = load_advice_cases(ROOT / 'evals/datasets/catalogue_advice_cases.json')
    assert all(not result['failed_checks'] for result in asyncio.run(evaluate_advice(cases)))
    regression = [{'id': 'detect-regression', 'steps': [
        {'message': 'Je cherche une perceuse disponible à moins de 100 euros.',
         'expected': {'skus': ['INVENTED'], 'status': 'RESULTS'}}]}]
    result, = asyncio.run(evaluate_advice(regression))
    assert result['actual']['skus'] == ['D-A', 'D-B', 'D-C']
    assert result['failed_checks'] == ['skus']


def test_advice_business_failure_fails_enriched_cli(monkeypatch):
    from evals.runners import catalogue_advice, run
    monkeypatch.setattr(catalogue_advice, 'load_advice_cases', lambda path: [
        {'id': 'gate-wrong-facts', 'steps': [
            {'message': 'Je cherche une perceuse disponible à moins de 100 euros.',
             'expected': {'skus': ['INVENTED'], 'status': 'RESULTS'}}]}])
    assert run.main(['--mode', 'deterministic', '--suite', 'enriched']) == 1


def test_advice_evaluations_detect_wrong_bound_even_when_first_three_products_match():
    regression = [{'id': 'detect-inclusive-regression', 'steps': [
        {'message': 'Rechercher trois perceuses disponibles sous 100',
         'expected': {'skus': ['D-A', 'D-B', 'D-C'], 'status': 'RESULTS',
                      'exclusive_maximum': False}}]}]
    result, = asyncio.run(evaluate_advice(regression))
    assert result['actual']['maximum'] == 100
    assert result['actual']['exclusive_maximum'] is True
    assert result['failed_checks'] == ['exclusive_maximum']
