"""G15-06 provenance on attested disposable Mongo/PG, never model semantics.

The controlled generator selects labels from the actual authorized PG passage
records. Following those citations must use the persisted preceding turn, not a
second embedding search or model completion. All documents/actors are synthetic.
"""
import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from unittest.mock import AsyncMock

import psycopg
from psycopg import sql
import pytest

from evals.assistant_v025.campaign import CORPUS
from test_tenant_assistant_integration import assistant, send  # noqa: F401
from test_tenant_knowledge_integration import command, environment, ingest  # noqa: F401
from test_tenant_orders_mongo_integration import REQUESTS, context, db, orders  # noqa: F401
from test_tenant_stock_operations_mongo_integration import stock  # noqa: F401
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.ports.tenant_knowledge import TenantDataNotFound
from stockpilot.application.services.identity_authorization import AuthorizationDenied

pytestmark = pytest.mark.integration
CASES = {row['id']: row for row in CORPUS}
QUESTION = CASES['G15-06']['message']
DOCUMENTS = {
    'delivery-guide': 'La livraison synthétique prend 7 jours ouvrés.',
    'stocks-guide': 'Un contrôle quotidien des quantités physiques et réservées est requis.',
    'preparation-guide': 'La réservation exige une confirmation explicite ; la préparation ne réserve rien.',
}
PRIOR = {'delivery-guide': 'G15-03', 'stocks-guide': 'G15-01', 'preparation-guide': 'G15-04'}


def business_facts(database):
    return {name: list(database[name].find({}).sort('_id', 1)) for name in (
        'b2b_products', 'b2b_orders', 'b2b_order_operations', 'b2b_stock_movements',
    )}


def publish_sources(a):
    service, _, env = a
    for identity, content in DOCUMENTS.items():
        ingest(env, command(identity, 'publish-' + identity, content))
        ingest(env, command(identity, 'publish-foreign-' + identity, 'FOREIGN-ONLY-CONTENT'), actor='admin-b')
    service.llm.classify_intent.return_value = IntentResult(intent='UNKNOWN')


def generate_cited(a, cited, *, question=None, turn='documentary-answer', fabricated=False):
    service, _, _ = a
    selected = tuple(cited)

    async def generate(*, prompt):
        records = json.loads(prompt.split('<documents>\n', 1)[1].split('\n</documents>', 1)[0])
        assert {p['document_id'] for p in records} == set(DOCUMENTS)
        assert 'FOREIGN-ONLY-CONTENT' not in prompt
        labels = {p['document_id']: p['source'] for p in records}
        if fabricated:
            return 'La livraison prend 7 jours [S99].'
        if not selected:
            return 'La livraison synthétique prend 7 jours ouvrés.'
        return ' '.join(DOCUMENTS[identity] + ' [' + labels[identity] + ']' for identity in selected)

    service.llm.generate.side_effect = generate
    message = question or CASES[PRIOR[selected[0] if selected else 'delivery-guide']]['message']
    return send(a, message, turn=turn)


def forbid_model_and_search(a):
    service, _, env = a
    service.llm.classify_intent = AsyncMock(side_effect=AssertionError('Citation follow-up must be deterministic'))
    service.llm.generate = AsyncMock(side_effect=AssertionError('Citation follow-up must not generate references'))
    env.embeddings.embed = AsyncMock(side_effect=AssertionError('Citation follow-up must not search by semantic similarity'))


def stored(a, turn):
    service, conv, env = a
    return service.store.turn(REQUESTS['a1'], context(env.orders), conv, turn)


def assert_no_documentary_source(reply):
    assert not reply.get('cited_sources')
    assert all(identity not in json.dumps(reply) for identity in DOCUMENTS)
    wording = reply['text'].lower()
    assert 'source' in wording and any(term in wording for term in ('aucun', 'indisponible', 'plus vérifiable', 'inaccessible'))
    assert reply['order'] is None and not reply['can_confirm']


@pytest.mark.parametrize('source', ['delivery-guide', 'stocks-guide', 'preparation-guide'])
def test_exact_G15_06_uses_previous_cited_sources(assistant, db, source):
    publish_sources(assistant)
    service, conv, env = assistant
    before = business_facts(db)
    original = generate_cited(assistant, [source])
    saved = deepcopy(stored(assistant, original['turn_id']))
    refs = saved['reply']['cited_sources']
    assert len(refs) == 1
    ref = refs[0]
    assert ref['organization_id'] == 'org-a' and ref['document_id'] == source and ref['chunk_id'] == 0
    assert ref['generation_id'] and ref['content_sha256'] == hashlib.sha256(DOCUMENTS[source].encode()).hexdigest()
    assert saved['reply']['source_turn_id'] == original['turn_id']
    assert '_citation_scope' not in original['reply']
    forbid_model_and_search(assistant)
    result = send(assistant, QUESTION, turn='G15-06')
    reply = result['reply']
    assert reply['source_turn_id'] == original['turn_id'] and reply['cited_sources'] == refs
    assert source in reply['text'] and 'passage 0' in reply['text']
    assert not any(other in reply['text'] for other in DOCUMENTS if other != source)
    assert '[S1]' not in reply['text'] and '[S2]' not in reply['text'] and '[S3]' not in reply['text']
    assert reply['order'] is None and not reply['can_confirm']
    assert send(assistant, QUESTION, turn='G15-06') == result
    chained = send(assistant, QUESTION, turn='citation-chain')['reply']
    assert chained['source_turn_id'] == original['turn_id'] and chained['cited_sources'] == refs
    assert stored(assistant, original['turn_id']) == saved
    assert business_facts(db) == before
    assert service.read(REQUESTS['a1'], context(env.orders), conv)['items'][0]['reply'] == original['reply']


def test_multiple_citations_exclude_consulted_but_uncited_documents(assistant, db):
    publish_sources(assistant)
    before = business_facts(db)
    original = generate_cited(assistant, ['stocks-guide', 'preparation-guide'])
    refs = stored(assistant, original['turn_id'])['reply']['cited_sources']
    assert {r['document_id'] for r in refs} == {'stocks-guide', 'preparation-guide'}
    # delivery-guide really was consulted and remains in the original footer.
    assert 'delivery-guide' in original['reply']['text']
    forbid_model_and_search(assistant)
    reply = send(assistant, QUESTION)['reply']
    assert {r['document_id'] for r in reply['cited_sources']} == {'stocks-guide', 'preparation-guide'}
    assert 'stocks-guide' in reply['text'] and 'preparation-guide' in reply['text']
    assert 'delivery-guide' not in json.dumps(reply)
    assert business_facts(db) == before


@pytest.mark.parametrize('kind', ['uncited', 'invented', 'catalogue', 'absent', 'legacy'])
def test_missing_unverifiable_or_non_documentary_prior_never_infers_a_source(assistant, db, kind):
    publish_sources(assistant)
    service, conv, env = assistant
    if kind in ('uncited', 'invented'):
        generate_cited(assistant, [], fabricated=kind == 'invented')
    elif kind in ('catalogue', 'legacy'):
        original = generate_cited(assistant, ['delivery-guide'])
        if kind == 'catalogue':
            # A later catalogue turn must not fall back to an older RAG answer.
            send(assistant, 'Quel est le stock de DRILL-001 ?', turn='current-catalogue')
        else:
            # A transcript from the old schema has no structured provenance.
            # Its checkpoint may still contain a newer snapshot: transcript
            # authority must win, not text/footer parsing or checkpoint guessing.
            with psycopg.connect(env.uri) as connection:
                connection.execute(sql.SQL("UPDATE {} SET reply=reply-'cited_sources'-'source_turn_id'-'_citation_scope' WHERE organization_id=%s AND owner_user_id=%s AND conversation_id=%s AND turn_id=%s").format(service.store.t('assistant_turns')),
                                   ('org-a', 'a1', conv, original['turn_id']))
    before = business_facts(db)
    historical = deepcopy(service.store.history(REQUESTS['a1'], context(env.orders), conv)['items'])
    forbid_model_and_search(assistant)
    assert_no_documentary_source(send(assistant, QUESTION)['reply'])
    assert service.store.history(REQUESTS['a1'], context(env.orders), conv)['items'][:-1] == historical
    assert business_facts(db) == before


@pytest.mark.parametrize('change', ['deleted', 'replaced', 'sensitive', 'content_changed'])
def test_current_source_revalidation_protects_followup_replay_and_history(assistant, db, change):
    publish_sources(assistant)
    service, conv, env = assistant
    original = generate_cited(assistant, ['delivery-guide'])
    first = send(assistant, QUESTION, turn='citation-before-change')
    old_source = deepcopy(stored(assistant, original['turn_id']))
    old_citation = deepcopy(stored(assistant, first['turn_id']))
    if change == 'deleted':
        env.repo.delete(REQUESTS['admin-a'], context(env.orders, 'admin-a'), 'delivery-guide',
                        operation_id='delete-cited-source', expected_revision=1)
    elif change == 'replaced':
        ingest(env, command('delivery-guide', 'replace-cited-source', 'Nouvelle règle synthétique : 9 jours.', revision=1))
    else:
        # Emulate legacy/corrupt data solely in the attested disposable schema.
        content = 'api_key=FICTITIOUS-CITATION-NEVER-A-CREDENTIAL' if change == 'sensitive' else 'Passage modifié sans identité de génération nouvelle.'
        with psycopg.connect(env.uri) as connection:
            connection.execute(sql.SQL('UPDATE {} SET content=%s WHERE organization_id=%s AND document_id=%s').format(env.repo.t('chunks')),
                               (content, 'org-a', 'delivery-guide'))
    before = business_facts(db)
    forbid_model_and_search(assistant)
    assert_no_documentary_source(send(assistant, QUESTION, turn='new-citation-after-change')['reply'])
    assert_no_documentary_source(send(assistant, QUESTION, turn='citation-before-change')['reply'])
    assert_no_documentary_source(send(assistant, CASES['G15-03']['message'], turn=original['turn_id'])['reply'])
    public = service.read(REQUESTS['a1'], context(env.orders), conv)
    assert 'delivery-guide' not in json.dumps(public)
    assert 'FICTITIOUS-CITATION' not in json.dumps(public)
    assert stored(assistant, original['turn_id']) == old_source
    assert stored(assistant, first['turn_id']) == old_citation
    assert business_facts(db) == before


def test_one_revoked_source_does_not_return_a_partial_historical_citation(assistant):
    publish_sources(assistant)
    _, _, env = assistant
    generate_cited(assistant, ['delivery-guide', 'stocks-guide'])
    env.repo.delete(REQUESTS['admin-a'], context(env.orders, 'admin-a'), 'stocks-guide',
                    operation_id='delete-one-of-two', expected_revision=1)
    forbid_model_and_search(assistant)
    assert_no_documentary_source(send(assistant, QUESTION)['reply'])


def test_private_previous_turn_cannot_cross_conversations_users_or_organizations(assistant, db):
    publish_sources(assistant)
    service, conv, env = assistant
    original = generate_cited(assistant, ['delivery-guide'])
    saved = deepcopy(stored(assistant, original['turn_id']))
    before = business_facts(db)
    forbid_model_and_search(assistant)
    for actor in ('a3', 'admin-a', 'b1'):
        with pytest.raises((AuthorizationDenied, TenantDataNotFound)):
            send(assistant, QUESTION, actor=actor)
        with pytest.raises((AuthorizationDenied, TenantDataNotFound)):
            service.read(REQUESTS[actor], context(env.orders, actor), conv)
    other = service.store.open(REQUESTS['a1'], context(env.orders), 'fresh-citation-conversation').conversation_id
    assert_no_documentary_source(send((service, other, env), QUESTION)['reply'])
    assert stored(assistant, original['turn_id']) == saved
    assert business_facts(db) == before


def test_revoked_permission_and_old_session_cannot_replay_a_citation(assistant, db, monkeypatch):
    publish_sources(assistant)
    service, conv, env = assistant
    generate_cited(assistant, ['delivery-guide'])
    previous = send(assistant, QUESTION, turn='citation')
    old_context = context(env.orders)
    renewed = replace(old_context, authorization_context_id='renewed-synthetic-session')
    resolve_current = env.orders[1].current

    def renewed_current(request):
        current = resolve_current(request)
        return replace(current, authorization_context_id=renewed.authorization_context_id) if current else None

    monkeypatch.setattr(env.orders[1], 'current', renewed_current)
    forbid_model_and_search(assistant)
    before = business_facts(db)
    with pytest.raises(AuthorizationDenied):
        asyncio.run(service.send(REQUESTS['a1'], old_context, conv, 'citation', QUESTION))
    # New authorized session of the same owner may read, but still verifies the
    # current document. The stale session never supplies authority.
    assert asyncio.run(service.send(REQUESTS['a1'], renewed, conv, 'citation', QUESTION)) == previous
    db.b2b_memberships.update_one({'membership_id': 'm-a1'}, {'$set': {'status': 'suspended'}})
    with pytest.raises(AuthorizationDenied):
        asyncio.run(service.send(REQUESTS['a1'], renewed, conv, 'citation', QUESTION))
    assert business_facts(db) == before


def test_G14_05_documentary_snapshot_replay_cannot_bypass_later_revocation(assistant, db):
    publish_sources(assistant)
    service, conv, env = assistant
    original = generate_cited(assistant, ['delivery-guide'])
    forbid_model_and_search(assistant)
    historical = send(assistant, CASES['G14-05']['message'], turn='historical-documentary-copy')
    assert historical['reply']['intent'] == 'HISTORY'
    assert 'delivery-guide' in historical['reply']['text']
    saved_origin = deepcopy(stored(assistant, original['turn_id']))
    saved_copy = deepcopy(stored(assistant, historical['turn_id']))
    env.repo.delete(REQUESTS['admin-a'], context(env.orders, 'admin-a'), 'delivery-guide',
                    operation_id='delete-after-historical-copy', expected_revision=1)
    before = business_facts(db)
    replay = send(assistant, CASES['G14-05']['message'], turn=historical['turn_id'])
    assert 'delivery-guide' not in json.dumps(replay)
    assert '7 jours' not in json.dumps(replay)
    assert 'delivery-guide' not in json.dumps(service.read(REQUESTS['a1'], context(env.orders), conv))
    assert stored(assistant, original['turn_id']) == saved_origin
    assert stored(assistant, historical['turn_id']) == saved_copy
    assert business_facts(db) == before


def test_exact_pg_passage_identity_is_scoped_to_current_org_generation_and_chunk(assistant, db):
    publish_sources(assistant)
    _, _, env = assistant
    from test_tenant_knowledge_integration import V

    ctx = context(env.orders)
    own = env.repo.search(REQUESTS['a1'], ctx, V, model='synthetic-768', version='v1')
    foreign = env.repo.search(REQUESTS['b1'], context(env.orders, 'b1'), V, model='synthetic-768', version='v1')
    a = next(p for p in own if p.document_id == 'delivery-guide')
    b = next(p for p in foreign if p.document_id == a.document_id)
    assert a.generation_id != b.generation_id
    exact = env.repo.cited_passage(REQUESTS['a1'], ctx, a.document_id, a.generation_id, a.chunk_id)
    assert exact.content == DOCUMENTS[a.document_id] and exact.organization_id == 'org-a'
    assert env.repo.cited_passage(REQUESTS['a1'], ctx, a.document_id, b.generation_id, 0) is None
    assert env.repo.cited_passage(REQUESTS['a1'], ctx, a.document_id, a.generation_id, 1) is None
    assert env.repo.cited_passage(REQUESTS['a1'], ctx, 'absent-document', a.generation_id, 0) is None
    with pytest.raises(AuthorizationDenied):
        env.repo.cited_passage(REQUESTS['b1'], ctx, a.document_id, a.generation_id, 0)
    ingest(env, command('delivery-guide', 'replace-before-exact-read', 'Version synthétique courante.', revision=1))
    assert env.repo.cited_passage(REQUESTS['a1'], ctx, a.document_id, a.generation_id, 0) is None
    db.b2b_memberships.update_one({'membership_id': 'm-a1'}, {'$set': {'status': 'suspended'}})
    with pytest.raises(AuthorizationDenied):
        env.repo.cited_passage(REQUESTS['a1'], ctx, a.document_id, a.generation_id, 0)
