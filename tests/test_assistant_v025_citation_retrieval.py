"""Stable references are server-owned evidence, never local model labels."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from stockpilot.application.ports.tenant_knowledge import TenantDataUnavailable
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.application.services.tenant_rag_safety import RAG_CITATION_REFUSAL, RAG_SENSITIVE_REFUSAL
from stockpilot.application.services.tenant_retrieval import TenantRetrieval
from stockpilot.domain.entities.tenant_knowledge import TenantPassage


def harness(answer='La livraison prend sept jours [S2].'):
    passages = [
        TenantPassage('org-a', 'stocks-guide', 0, 'Le contrôle du stock est quotidien.', .1, 'generation-stock'),
        TenantPassage('org-a', 'delivery-guide', 2, 'La livraison prend sept jours.', .2, 'generation-delivery'),
        TenantPassage('org-a', 'preparation-guide', 1, 'La préparation ne réserve aucun stock.', .3, 'generation-preparation'),
    ]
    active = {(p.document_id, p.generation_id, p.chunk_id): p for p in passages}
    repo = SimpleNamespace(current=Mock(), search=Mock(return_value=passages),
                           cited_passage=Mock(side_effect=lambda request, ctx, doc, gen, chunk: active.get((doc, gen, chunk))))
    embed = SimpleNamespace(embed=AsyncMock(return_value=[1.0]+[0.0]*767))
    model = SimpleNamespace(generate=AsyncMock(return_value=answer))
    service = TenantRetrieval(repo, embed, model, model='synthetic', version='v1')
    return service, repo, embed, model, SimpleNamespace(organization_id='org-a'), active


def execute(h, citations=None, consulted=None):
    return asyncio.run(h[0].execute('request', h[4], 'Quelle est la livraison ?',
                                    citations=citations, consulted_sources=consulted))


def test_only_actually_cited_sources_have_stable_references_and_consulted_is_separate():
    h = harness()
    cited, consulted = [{'stale': True}], [{'stale': True}]
    result = execute(h, cited, consulted)
    assert 'sept jours [S2]' in result
    assert [r['document_id'] for r in cited] == ['delivery-guide']
    assert [r['document_id'] for r in consulted] == ['stocks-guide','delivery-guide','preparation-guide']
    assert cited[0] == h[0]._reference(h[1].search.return_value[1])
    assert set(cited[0]) == {'organization_id','document_id','generation_id','chunk_id','content_sha256'}
    assert 'S2' not in str(cited)
    assert h[1].cited_passage.call_count == 3
    # Changing retrieval order only renames local labels; permanent identities
    # continue to refer to the exact passage that was actually cited.
    h[1].search.return_value.reverse()
    h[3].generate.return_value='Le contrôle du stock est quotidien [S3].'
    execute(h, cited, consulted)
    assert [r['document_id'] for r in cited] == ['stocks-guide']


@pytest.mark.parametrize('answer', [
    'Stock quotidien [S1] et livraison sept jours [S2].',
    'Stock quotidien [source: S1], livraison sept jours, document S2.',
])
def test_multiple_citations_preserve_exact_passages_without_consulted_only_source(answer):
    h=harness(answer); citations=[]
    execute(h, citations)
    assert [r['document_id'] for r in citations] == ['stocks-guide','delivery-guide']
    assert [r['chunk_id'] for r in citations] == [0,2]
    assert 'preparation-guide' not in str(citations)


def test_server_consulted_footer_does_not_create_a_model_citation():
    h=harness('Les informations sont présentes dans les documents.')
    citations=[]; consulted=[]
    assert '[S1]' in execute(h,citations,consulted)
    assert citations == [] and len(consulted)==3


@pytest.mark.parametrize('answer', ['Inventé [S99].','Inventé [source: S99].','Inventé selon le document S99.'])
def test_unknown_model_reference_cannot_create_proof(answer):
    h=harness(answer); citations=[]; consulted=[]
    assert execute(h,citations,consulted)==RAG_CITATION_REFUSAL
    assert citations==consulted==[]


def test_secret_output_never_creates_proof():
    h=harness('client_secret=FICTITIOUS-CITATION-TEST-VALUE [S1]')
    citations=[]; consulted=[]
    assert execute(h,citations,consulted)==RAG_SENSITIVE_REFUSAL
    assert citations==consulted==[]


@pytest.mark.parametrize('change',['deleted','replacement','content','secret'])
def test_source_changed_during_generation_blocks_prose_and_all_provenance(change):
    h=harness(); citations=[]; consulted=[]
    async def changed(**kwargs):
        # The withdrawn source was consulted but not cited. Its identity and
        # possible contribution to prose must not escape through the footer.
        key=('stocks-guide','generation-stock',0)
        old=h[5][key]
        if change=='deleted': h[5].pop(key)
        elif change=='replacement': h[5][key]=replace(old,generation_id='replacement')
        elif change=='content': h[5][key]=replace(old,content='Different content')
        else: h[5][key]=replace(old,content='password=FICTITIOUS-CITATION-TEST-VALUE')
        return 'La livraison prend sept jours [S2].'
    h[3].generate.side_effect=changed
    assert execute(h,citations,consulted)==RAG_CITATION_REFUSAL
    assert citations==consulted==[]


def test_authorization_revocation_during_generation_cannot_publish_refs():
    h=harness(); citations=[]; consulted=[]
    async def revoke(**kwargs):
        h[1].current.side_effect=AuthorizationDenied('revoked')
        return 'Livraison sept jours [S2].'
    h[3].generate.side_effect=revoke
    with pytest.raises(AuthorizationDenied): execute(h,citations,consulted)
    assert citations==consulted==[]


@pytest.mark.parametrize('mutation', ['organization','document','generation','chunk','hash','extra','duplicate','bool','oversized','nested','missing'])
def test_historical_reference_validation_is_exact_bounded_and_does_not_search(mutation):
    h=harness(); references=[h[0]._reference(h[1].search.return_value[0])]
    source=references[0]
    if mutation=='organization': source['organization_id']='org-b'
    elif mutation=='document': source['document_id']='foreign-guide'
    elif mutation=='generation': source['generation_id']='old-generation'
    elif mutation=='chunk': source['chunk_id']=2
    elif mutation=='hash': source['content_sha256']='a'*64
    elif mutation=='extra': source['label']='S1'
    elif mutation=='duplicate': references.append(dict(source))
    elif mutation=='bool': source['chunk_id']=True
    elif mutation=='oversized': source['chunk_id']=32
    elif mutation=='nested': source['document_id']={'password':'FICTITIOUS-VALUE'}
    else: source.pop('generation_id')
    assert h[0].revalidate_citations('request',h[4],references)==[]
    h[1].search.assert_not_called(); h[2].embed.assert_not_awaited(); h[3].generate.assert_not_awaited()


def test_historical_current_passage_has_same_identity_but_changed_hash_is_rejected():
    h=harness(); sources=[h[0]._reference(h[1].search.return_value[0])]
    h[5][('stocks-guide','generation-stock',0)]=replace(h[1].search.return_value[0],content='new content')
    assert h[0].revalidate_citations('request',h[4],sources)==[]


def test_legacy_adapter_without_generation_cannot_create_historical_proof():
    h=harness(); h[1].search.return_value=[replace(p,generation_id=None) for p in h[1].search.return_value]
    cited=[]; consulted=[]
    assert 'sept jours' in execute(h,cited,consulted)
    assert cited==consulted==[]
    h[1].cited_passage.assert_not_called()


def test_revalidation_failure_is_sanitized_without_private_error():
    h=harness(); refs=[h[0]._reference(h[1].search.return_value[0])]
    h[1].cited_passage.side_effect=RuntimeError('password=FICTITIOUS-TEST-ONLY')
    with pytest.raises(TenantDataUnavailable) as failure:
        h[0].revalidate_citations('request',h[4],refs)
    assert 'FICTITIOUS' not in str(failure.value) and failure.value.__suppress_context__


def test_rendered_citations_use_document_and_passage_not_label_or_content():
    h=harness(); refs=[h[0]._reference(h[1].search.return_value[0])]
    verified=h[0].revalidate_citations('request',h[4],refs)
    result=h[0].render_citations(verified)
    assert '"stocks-guide", passage 0' in result
    assert 'S1' not in result and 'quotidien' not in result
    assert refs==verified and refs is not verified and refs[0] is not verified[0]


def test_rendered_history_sources_respect_the_same_public_size_limit():
    h=harness()
    references=[h[0]._reference(replace(h[1].search.return_value[0],document_id=f'{i:02}'+ '<'*126))
                for i in range(20)]
    with pytest.raises(TenantDataUnavailable,match='public bound'):
        h[0].render_citations(references)


def test_concurrent_calls_have_separate_citation_collectors():
    h=harness(); seen=asyncio.Event()
    async def generate(**kwargs):
        if 'first question' in kwargs['prompt']:
            await seen.wait()
            return 'Stock quotidien [S1].'
        seen.set()
        return 'Livraison sept jours [S2].'
    h[3].generate.side_effect=generate
    async def scenario():
        first=[]; second=[]
        await asyncio.gather(
            h[0].execute('request',h[4],'first question',citations=first),
            h[0].execute('request',h[4],'second question',citations=second),
        )
        return first,second
    first,second=asyncio.run(scenario())
    assert [r['document_id'] for r in first]==['stocks-guide']
    assert [r['document_id'] for r in second]==['delivery-guide']
