"""Run the actual Advice graph against a synthetic catalogue, without services/LLM."""
import json
from pathlib import Path

from evals.fixtures.catalogue_advice import advice_graph


def load_advice_cases(path: Path) -> list[dict]:
    cases = json.loads(path.read_text())
    if not isinstance(cases, list) or not cases:
        raise ValueError('Expected nonempty advice evaluation cases')
    ids = set()
    for case in cases:
        if not isinstance(case.get('id'), str) or case['id'] in ids or not isinstance(case.get('steps'), list) or not case['steps']:
            raise ValueError('Invalid advice evaluation case')
        ids.add(case['id'])
        for step in case['steps']:
            expected = step.get('expected', {})
            if (not isinstance(step.get('message'), str) or not step['message'].strip()
                    or not isinstance(expected.get('skus'), list)
                    or any(not isinstance(sku, str) for sku in expected['skus'])
                    or expected.get('status') not in {'RESULTS', 'NO_RESULTS', 'CLARIFICATION_REQUIRED', 'CATALOGUE_UNAVAILABLE'}):
                raise ValueError('Invalid advice evaluation step')
    return cases


async def evaluate_advice(cases: list[dict]) -> list[dict]:
    results = []
    for case in cases:
        graph, _, _, forbidden = advice_graph()
        for index, step in enumerate(case['steps']):
            result = await graph.ainvoke({'message': step['message'], 'customer_id': 'synthetic-eval'},
                                        {'configurable': {'thread_id': case['id']}})
            products = result['recommendations']
            actual = {'skus': [p['sku'] for p in products], 'status': result['advice_status'],
                      'budget': result.get('budget'), 'roles': [p.get('role') for p in products],
                      'reference_matches': products[0].get('matches_criteria') if products else None}
            criteria = result.get('last_advice_criteria') or {}
            actual.update({key: criteria.get(key) for key in
                           ('category', 'maximum', 'exclusive_maximum', 'available', 'limit')})
            failures = [key for key, expected in step['expected'].items() if actual.get(key) != expected]
            forbidden.execute.assert_not_awaited()
            results.append({'id': f'{case["id"]}-{index}', 'dataset': 'catalogue_advice',
                            'input': step['message'], 'expected': step['expected'],
                            'actual': actual, 'failed_checks': failures})
    return results
