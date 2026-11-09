"""Private provenance and history boundaries with strict scoped memory ports.

These tests exercise production assistant/retrieval code. Real Mongo/PG
authorization is proved separately by test_assistant_v025_citation_integration.
"""
import asyncio
from copy import deepcopy
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from evals.assistant_v025.campaign import CORPUS, harness
from stockpilot.application.ports.tenant_knowledge import TenantDataNotFound
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.application.services.tenant_retrieval import TenantRetrieval
from stockpilot.domain.entities.tenant_knowledge import TenantPassage

CASES = {row['id']: row for row in CORPUS}
CONV = 'private-conversation'
DOCUMENTS = {'delivery-guide': 'Livraison synthétique : 7 jours.', 'stocks-guide': 'Contrôle quotidien synthétique.'}


class ScopedTranscript:
    def __init__(self, request, context):
        self.request, self.context = request, context
        self.rows = {CONV: []}
        self.reads = []

    def current(self, request, context, permission=None):
        if request != self.request or context != self.context:
            raise AuthorizationDenied('Synthetic current context mismatch')
        return context.principal

    def check(self, request, context, conversation):
        self.current(request, context)
        if conversation not in self.rows:
            raise TenantDataNotFound('Conversation unavailable')

    def turn(self, request, context, conversation, turn_id):
        self.check(request, context, conversation)
        self.reads.append((conversation, turn_id))
        return next((deepcopy(t) for t in self.rows[conversation] if t['turn_id'] == turn_id), None)

    def history(self, request, context, conversation, limit=20, offset=0):
        self.check(request, context, conversation)
        self.reads.append((conversation, limit, offset))
        rows = sorted(self.rows[conversation], key=lambda row: (row['created_at'], row['turn_id']), reverse=True)
        return {'items': deepcopy(list(reversed(rows[offset:offset + limit]))), 'has_more': len(rows) > offset + limit}


def setup():
    h = harness()
    service, _, request, context = h
    store = ScopedTranscript(request, context)
    passages = {
        ('org-a', doc, 'generation-' + doc, 0): TenantPassage('org-a', doc, 0, text, 0, 'generation-' + doc)
        for doc, text in DOCUMENTS.items()
    }

    def cited_passage(req, ctx, doc, generation, chunk):
        store.current(req, ctx)
        return passages.get((ctx.organization_id, doc, generation, chunk))

    repo = SimpleNamespace(current=store.current, cited_passage=cited_passage,
                           search=Mock(side_effect=AssertionError('No semantic source guess')))
    embed = SimpleNamespace(embed=AsyncMock(side_effect=AssertionError('No source embedding')))
    generator = SimpleNamespace(generate=AsyncMock(side_effect=AssertionError('No model-made citations')))
    retrieval = TenantRetrieval(repo, embed, generator, model='synthetic-768', version='v1')
    service.store = service.citations.store = service.history.store = store
    service.retrieval = service.citations.retrieval = retrieval
    service.llm.classify_intent.side_effect = AssertionError('Exact citation/history uses no classifier')
    return h, store, passages


def documentary_turn(service, context, identity='origin', document='delivery-guide', *, conversation=CONV, created='2026-11-09T10:00:00+00:00'):
    reference = {'organization_id': 'org-a', 'document_id': document, 'generation_id': 'generation-' + document,
                 'chunk_id': 0, 'content_sha256': hashlib.sha256(DOCUMENTS[document].encode()).hexdigest()}
    return {'turn_id': identity, 'kind': 'message', 'status': 'completed', 'created_at': created,
            'input': {'message': 'Question documentaire synthétique'}, 'action': None,
            'reply': {'intent': 'RAG', 'text': DOCUMENTS[document] + ' [S1]\nDocument consulté : ' + document,
                      'products': [], 'order': None, 'can_confirm': False,
                      'source_turn_id': identity, 'cited_sources': [reference], '_rag_read_sources': [reference],
                      '_citation_scope': service.citations.scope(context, conversation)}}


def pending(identity='current'):
    return {'turn_id': identity, 'kind': 'message', 'status': 'pending', 'reply': None,
            'input': {'message': CASES['G15-06']['message']}, 'action': None, 'created_at': '2026-11-09T11:00:00+00:00'}


def followup(h, conversation=CONV, turn_id='current'):
    return h[0].citations.previous(h[2], h[3], conversation, turn_id)


def no_source(reply):
    assert not reply.get('cited_sources')
    assert not any(doc in str(reply) for doc in DOCUMENTS)
    assert reply['order'] is None and not reply['can_confirm']
    assert 'source' in reply['text'].lower()


@pytest.mark.parametrize('field,value', [('version', 99), ('version', True), ('organization_id', 'org-b'),
                                        ('owner_user_id', 'other-user'), ('conversation_id', 'other-conversation')])
def test_altered_private_provenance_scope_cannot_supply_citations(field, value):
    h, store, _ = setup()
    old = documentary_turn(h[0], h[3])
    old['reply']['_citation_scope'][field] = value
    store.rows[CONV] = [old, pending()]
    before = deepcopy(store.rows)
    no_source(followup(h))
    assert store.rows == before


def test_copied_transcript_cannot_reuse_sources_from_another_conversation():
    h, store, _ = setup()
    old = documentary_turn(h[0], h[3])
    store.rows[CONV] = [old]
    store.rows['other-conversation'] = [deepcopy(old), pending()]
    no_source(followup(h, 'other-conversation'))
    assert all(read[0] == 'other-conversation' for read in store.reads)


def test_tied_preceding_turn_timestamps_are_ambiguous_even_with_current_pending_turn():
    h, store, _ = setup()
    store.rows[CONV] = [documentary_turn(h[0], h[3], 'prior-a'),
                        documentary_turn(h[0], h[3], 'prior-b', 'stocks-guide'), pending()]
    no_source(followup(h))


@pytest.mark.parametrize('variant', ['rag-altered-origin', 'missing-origin', 'self-cycle', 'two-turn-cycle'])
def test_source_turn_identity_cannot_be_repointed_or_cyclic(variant):
    h, store, _ = setup()
    original = documentary_turn(h[0], h[3])
    prior = deepcopy(original)
    prior['turn_id'] = 'citation-1'
    prior['created_at'] = '2026-11-09T10:01:00+00:00'
    prior['reply']['intent'] = 'RAG_CITATION'
    if variant == 'rag-altered-origin':
        original['reply']['source_turn_id'] = 'other-origin'
    elif variant == 'missing-origin':
        prior['reply']['source_turn_id'] = 'missing'
    elif variant == 'self-cycle':
        prior['reply']['source_turn_id'] = prior['turn_id']
    else:
        original['reply']['intent'] = 'RAG_CITATION'
        original['reply']['source_turn_id'] = prior['turn_id']
    store.rows[CONV] = [original, prior, pending()]
    before = deepcopy(store.rows)
    no_source(followup(h))
    assert store.rows == before


def test_old_checkpoint_cannot_replace_missing_transcript_provenance():
    h, store, _ = setup()
    original = documentary_turn(h[0], h[3])
    stale_checkpoint = deepcopy(original['reply'])
    for field in ('cited_sources', '_rag_read_sources', '_citation_scope', 'source_turn_id'):
        original['reply'].pop(field)
    store.rows[CONV] = [original, pending()]
    result = asyncio.run(h[0]._graph(h[2], h[3], CONV, None).ainvoke({
        'turn_id': 'current', 'question': CASES['G15-06']['message'],
        'reply': stale_checkpoint, 'historical_turn_id': 'origin', 'last_order_id': None,
    }))
    no_source(result['reply'])
    assert not h[0].orders.mock_calls


def test_G14_05_refuses_already_revoked_document_without_changing_snapshot():
    h, store, passages = setup()
    original = documentary_turn(h[0], h[3])
    store.rows[CONV] = [original, pending()]
    before = deepcopy(store.rows)
    passages.clear()
    result = asyncio.run(h[0]._graph(h[2], h[3], CONV, None).ainvoke({
        'turn_id': 'current', 'question': CASES['G14-05']['message'],
    }))['reply']
    assert result['intent'] == 'CLARIFICATION_REQUIRED'
    assert 'delivery-guide' not in str(result) and '7 jours' not in str(result)
    assert store.rows == before


def test_G14_05_snapshot_replay_revalidates_sources_after_later_revocation():
    h, store, passages = setup()
    original = documentary_turn(h[0], h[3])
    store.rows[CONV] = [original, pending()]
    reply = asyncio.run(h[0]._graph(h[2], h[3], CONV, None).ainvoke({
        'turn_id': 'current', 'question': CASES['G14-05']['message'],
    }))['reply']
    assert reply['intent'] == 'HISTORY' and 'delivery-guide' in reply['text']
    historical = {**pending(), 'status': 'completed', 'reply': reply}
    store.rows[CONV][-1] = historical
    before = deepcopy(store.rows)
    passages.clear()
    public = h[0]._public_turn(h[2], h[3], historical, CONV)
    assert 'delivery-guide' not in str(public) and '7 jours' not in str(public)
    assert store.rows == before
    assert not h[0].orders.mock_calls
