"""Deterministic qualification; real storage/HTTP claims stay in integrations."""
import asyncio
from copy import deepcopy

import pytest

from evals.assistant_v025.campaign import CORPUS, PRIOR, answer, harness, oracle, variants, verify


def test_corpus_ids_and_exact_user_regressions_are_preserved():
    from evals.assistant_v025.campaign import digest
    assert digest() == 'b01fdc4a67ef4cfdac65a8884e85b95492fa19fb868c235170f5b9bf4ba80dac'
    assert len(CORPUS) == len({c['id'] for c in CORPUS}) == 301
    assert CORPUS[1]['message'] == 'je voulais savoir le stock de produit qui bientot épuisé'
    assert next(c for c in CORPUS if c['id']=='G06-01')['message'] == 'je cherche une perceuse disponible avec un prix moins de 100 euros'
    assert {c['id'].split('-')[0] for c in CORPUS} == {f'G{i:02}' for i in range(1,22)}


@pytest.mark.parametrize('case', [c for c in CORPUS if oracle(c)['kind'] in ('list','exact')], ids=lambda c:c['id'])
def test_corpus_result_sets(case):
    h = harness()
    if oracle(case).get('empty'): h[1].rows=[]
    before = deepcopy(h[1].rows)
    state = None
    if case['id'] in PRIOR:
        _, state = asyncio.run(answer(h, PRIOR[case['id']]))
    reply, _ = asyncio.run(answer(h, case['message'], state))
    verify(case, reply, h[1].rows, 'org-a', before, h[1].rows, h[0].orders.mock_calls)


@pytest.mark.parametrize('question', [
    'Je cherche une perceuse à moins de 92233720368547758,07 euros',
    'Je cherche une perceuse à moins de 99999999999999999999999 euros',
    'Je cherche une perceuse à moins de 999999999999999,99 euros',
    'Je cherche une perceuse à moins de 999999999999999,99 euros pour mon atelier',
    'Je cherche une perceuse à moins de 999999999999999,99',
    'Je cherche une perceuse pour exactement 100 euros',
])
def test_price_never_rounds_or_overflows_into_a_different_filter(question):
    reply,_=asyncio.run(answer(harness(),question))
    assert reply['intent']=='CLARIFICATION_REQUIRED' and reply['products']==[]


@pytest.mark.parametrize('identity', ['G13-01','G13-02','G13-05','G13-06','G13-07','G13-09','G13-13','G13-14'])
def test_original_multiturn_criteria_and_explicit_reset(identity):
    from evals.assistant_v025.campaign import MULTI_FINAL
    async def sequence():
        case=next(c for c in CORPUS if c['id']==identity)
        h=harness(); before=deepcopy(h[1].rows); state=None
        for message in case['message'].split('⇒'):
            reply,state=await answer(h,message.strip(),state)
            verify(case,reply,h[1].rows,'org-a',before,h[1].rows,h[0].orders.mock_calls,{'kind':'facts'})
        verify(case,reply,h[1].rows,'org-a',before,h[1].rows,h[0].orders.mock_calls,MULTI_FINAL[identity])
        # A fresh conversation cannot inherit the previous stock filter.
        fresh,_=await answer(h,'Et avec exactement 5 ?')
        assert fresh['intent']=='CLARIFICATION_REQUIRED' and fresh['products']==[]
    asyncio.run(sequence())


@pytest.mark.parametrize('case', variants(), ids=lambda c: c['message'])
def test_1200_distinct_variants_with_independent_stock_oracle(case):
    h = harness()
    before = deepcopy(h[1].rows)
    reply, _ = asyncio.run(answer(h, case['message']))
    expected = sorted((r for r in before if r['org']=='org-a' and r['status']=='active'
                       and r['quantity']-r['reserved'] >= case['minimum']
                       and (case['maximum'] is None or r['quantity']-r['reserved'] <= case['maximum'])),
                      key=lambda r:(r['quantity']-r['reserved'],r['sku']))
    assert reply['intent']=='LOW_STOCK'
    assert [c['sku'] for c in reply['products']]==[r['sku'] for r in expected[:5]]
    assert reply['total']==len(expected)
    assert before==h[1].rows and not h[0].orders.mock_calls
    h[0].llm.classify_intent.assert_not_awaited()


@pytest.mark.parametrize('index', range(100))
def test_100_multiturn_sequences_with_budget_removal_and_identity_reset(index):
    async def sequence():
        org = 'org-a' if index%2 else 'org-b'
        role = ('READ_ONLY','USER','MANAGER','ORGANIZATION_ADMIN')[index%4]
        h = harness(org,role,user=f'user-{index%2}')
        ceiling = 101+index
        first, state = await answer(h,f'Je cherche des perceuses disponibles à moins de {ceiling} euros')
        second, state = await answer(h,'Et uniquement à moins de 100 euros',state)
        assert first['criteria']['maximum']==ceiling
        assert second['criteria']['maximum']==100 and second['criteria']['available'] is True
        assert second['criteria']['category']=='DRILL'
        assert all(c['price_minor']<10000 and c['organization_id']==org and c['available']>0 for c in second['products'])
        third,_ = await answer(h,'Et sans plafond',state)
        assert third['criteria']['maximum'] is None
        assert third['criteria']['available'] is True
        fresh,_ = await answer(h,'Je cherche des visseuses')
        assert fresh['criteria']['maximum'] is None and fresh['criteria']['available'] is None
        assert fresh['criteria']['category']=='SCREWDRIVER'
        assert h[0].orders.mock_calls==[]
    asyncio.run(sequence())


@pytest.mark.parametrize('org', ['org-a','org-b'])
def test_set_laws_pagination_and_concurrent_changes(org):
    async def scenario():
        h=harness(org)
        service, catalogue, request, context=h
        async def results(question):
            reply,_=await answer(h,question)
            saved=deepcopy(reply)
            all_rows=[]
            for offset in range(0,200,20):
                page=service.catalogue.results(request,context,reply['catalogue_search'],offset=offset)
                all_rows.extend(page['items'])
                if not page['has_more']: break
            assert saved==reply
            assert len({c['sku'] for c in all_rows})==len(all_rows)==reply['total']
            return {c['sku'] for c in all_rows},reply
        below5,_=await results('Produits avec moins de 5 unités disponibles')
        below15,_=await results('Produits avec moins de 15 unités disponibles')
        exactly5,_=await results('Produits avec exactement 5 unités disponibles')
        gte5,_=await results('Produits avec au moins 5 unités disponibles')
        lte5,_=await results('Produits avec 5 unités ou moins disponibles')
        assert below5 < below15 and exactly5 == gte5 & lte5
        strict,_=await results('Produits disponibles à moins de 100 euros')
        inclusive,_=await results('Produits disponibles à 100 euros maximum')
        assert strict < inclusive
        all_skus, original=await results('Produits avec au moins 0 unités disponibles')
        assert len(all_skus)>100 and len(original['products'])==5
        before=deepcopy(original)
        source=next(r for r in catalogue.rows if r['org']==org and r['sku']==original['products'][0]['sku'])
        source['quantity']+=1
        page=service.catalogue.results(request,context,original['catalogue_search'])
        assert original==before
        assert page['items'] != original['products']
        assert not service.orders.mock_calls
    asyncio.run(scenario())


@pytest.mark.parametrize('mutant', ['tenant','price','available','identity','order','mutation'])
def test_fact_and_side_effect_oracles_detect_injected_mutants(mutant):
    case=next(c for c in CORPUS if c['id']=='G06-01')
    h=harness(); before=deepcopy(h[1].rows)
    reply,_=asyncio.run(answer(h,case['message']))
    writes=[]
    if mutant=='tenant': reply['products'][0]['organization_id']='org-b'
    if mutant=='price': reply['products'][0]['price_minor']=10000
    if mutant=='available': reply['products'][0]['available']+=3
    if mutant=='identity': reply['products'][0]['name']='Model invented a different name'
    if mutant=='order': reply['can_confirm']=True
    if mutant=='mutation': writes=['prepare']
    with pytest.raises(AssertionError): verify(case,reply,h[1].rows,'org-a',before,h[1].rows,writes)
