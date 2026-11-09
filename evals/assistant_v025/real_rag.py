"""Opt-in, actual Ollama qualification on an entirely synthetic scoped corpus.

No application settings, database, dotenv or deployment is loaded. The actual
assistant graph, retrieval and Ollama classifier/generation adapter are used;
only the HTTP transport can bridge WSL to an owned Windows loopback instance.
The memory repository proves the model-facing scope contract, not PostgreSQL
authorization. Pair results with the real disposable-store integration proofs.
"""
import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, UTC
import hashlib
import json
from itertools import permutations
import math
from pathlib import Path
import re
import subprocess
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse

import httpx

from .campaign import CORPUS, digest, harness
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.application.ports.tenant_knowledge import TenantDataNotFound
from stockpilot.application.services.tenant_retrieval import TenantRetrieval
from stockpilot.domain.entities.tenant_knowledge import TenantPassage
from stockpilot.domain.enums.permission import Permission
from stockpilot.infrastructure.llm.ollama_adapter import OllamaAdapter

DOCUMENTS = [
    dict(org='org-a', id='stocks-guide', users=['user-a'], content='La procédure de gestion des stocks impose un contrôle quotidien des quantités physiques et réservées. Le disponible est la quantité physique diminuée de la quantité réservée.'),
    dict(org='org-a', id='preparation-guide', users=['user-a'], content='La préparation enregistre un brouillon sans réserver le stock. La réservation exige une confirmation explicite de l’utilisateur autorisé. Une recherche seule ne crée aucune commande.'),
    dict(org='org-a', id='delivery-guide', users=['user-a'], content='Le délai de livraison de l’entreprise Atelier A est de 7 jours ouvrés. Le produit DEMO-01-0225 bénéficie d’une garantie documentaire de 24 mois. Les retours sont acceptés pendant 14 jours si le produit est inutilisé.'),
    dict(org='org-a', id='legacy-price', users=['user-a'], content='Ancien export documentaire non actuel : le produit DEMO-01-0225 était affiché à 1,00 EUR. Ce document ne décrit pas le prix catalogue courant.'),
    dict(org='org-b', id='foreign-guide', users=['user-b'], content='FOREIGN_DECOY_883 : le délai réservé à l’organisation B est de 883 jours.'),
    dict(org='org-a', id='private-guide', users=['other-user'], content='PRIVATE_DECOY_991 : le délai réservé à un autre lecteur est de 991 jours.'),
]


class Provider:
    def __init__(self, url, *, windows_curl=False, timeout=120):
        self.url, self.windows_curl, self.timeout = url.rstrip('/'), windows_curl, timeout
        self.seed = 25051
        self.calls = []
        self.transport_errors = []
        self.embedding_inputs = []

    async def request(self, path, payload=None):
        try:
            return await self._request(path, payload)
        except (TimeoutError, ConnectionError, httpx.HTTPError) as error:
            self.transport_errors.append({'path': path, 'kind': type(error).__name__})
            raise

    async def _request(self, path, payload=None):
        if self.windows_curl:
            arguments = ['/mnt/c/Windows/System32/curl.exe', '--silent', '--show-error', '--fail',
                         '--max-time', str(self.timeout), self.url + path]
            if payload is not None:
                arguments += ['--header', 'Content-Type: application/json', '--data-binary', '@-']
            process = await asyncio.create_subprocess_exec(*arguments, stdin=asyncio.subprocess.PIPE,
                                                          stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                output, _ = await asyncio.wait_for(process.communicate(
                    json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None), self.timeout + 5)
            except TimeoutError:
                process.kill()
                await process.wait()
                raise TimeoutError('Owned loopback model transport timed out') from None
            if process.returncode:
                raise ConnectionError('Owned loopback model request failed')
            return json.loads(output)
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            response = await (client.get(self.url + path) if payload is None else client.post(self.url + path, json=payload))
            response.raise_for_status()
            return response.json()

    async def post(self, settings, path, payload, prompt):
        del settings
        payload = deepcopy(payload)
        options = {'num_predict': 256, 'num_ctx': 4096, 'temperature': .2, **payload.get('options', {}), 'seed': self.seed}
        payload.update(options=options, keep_alive='5m')
        result = await self.request(path, payload)
        self.calls.append({'model': payload['model'], 'kind': 'classification' if 'format' in payload else 'generation',
                           'options': options, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                           'raw_response': result.get('response'),
                           'eval_count': result.get('eval_count'), 'duration_ns': result.get('total_duration')})
        return result

    async def embed(self, content):
        self.embedding_inputs.append(hashlib.sha256(content.encode()).hexdigest())
        value = await self.request('/api/embeddings', {'model': 'nomic-embed-text:latest', 'prompt': content, 'keep_alive': '5m'})
        return value['embedding']


class ScopedDocuments:
    def __init__(self, docs):
        self.docs, self.observed, self.resolved = docs, [], []

    def current(self, request, context, permission):
        if request != (context.organization_id, context.principal.user_id) or permission not in context.principal.permissions:
            raise AuthorizationDenied('Synthetic document permission denied')

    def search(self, request, context, embedding, *, model, version):
        self.current(request, context, Permission.RAG_READ)
        assert model == 'nomic-embed-text:latest' and version == 'v025-isolated-v1'
        candidates = [d for d in self.docs if d['org'] == context.organization_id and context.principal.user_id in d['users'] and d.get('active', True)]
        def distance(doc):
            vector = doc['embedding']
            return 1 - sum(a*b for a,b in zip(embedding, vector)) / (math.sqrt(sum(x*x for x in embedding)) * math.sqrt(sum(x*x for x in vector)))
        candidates.sort(key=lambda d: (distance(d), d['id']))
        candidates = candidates[:3]  # Match PgTenantKnowledge.search's production default.
        self.observed.append([d['id'] for d in candidates])
        return [TenantPassage(d['org'], d['id'], 0, d['content'], distance(d),
                              d.get('generation', 'synthetic-v1-' + d['id'])) for d in candidates]

    def cited_passage(self, request, context, document_id, generation_id, chunk_id):
        self.current(request, context, Permission.RAG_READ)
        self.resolved.append((document_id, generation_id, chunk_id))
        document = next((d for d in self.docs if d['org'] == context.organization_id
                         and context.principal.user_id in d['users'] and d['id'] == document_id
                         and d.get('generation', 'synthetic-v1-' + d['id']) == generation_id
                         and chunk_id == 0 and d.get('active', True)), None)
        if document is None:
            return None
        return TenantPassage(document['org'], document_id, chunk_id, document['content'], 0.0, generation_id)


class SyntheticTurns:
    """Private immutable journal substitute, never proof of PostgreSQL storage."""
    def __init__(self, context):
        self.active_sessions = {}
        self.conversations = {}
        self.rows = {}
        self.register(context)

    def register(self, context):
        self.active_sessions[(context.organization_id, context.principal.user_id)] = context.authorization_context_id

    def current(self, request, context, permission):
        key = context.organization_id, context.principal.user_id
        if (request != key or self.active_sessions.get(key) != context.authorization_context_id
                or permission not in context.principal.permissions):
            raise AuthorizationDenied('Synthetic current identity/session/permission denied')
        return context.principal

    def open(self, request, context, conversation_id):
        self.current(request, context, Permission.CONVERSATION_OWN)
        owner = context.organization_id, context.principal.user_id
        if conversation_id in self.conversations and self.conversations[conversation_id] != owner:
            raise TenantDataNotFound('Synthetic private conversation unavailable')
        self.conversations.setdefault(conversation_id, owner)
        self.rows.setdefault(conversation_id, [])

    def _scope(self, request, context, conversation_id):
        self.current(request, context, Permission.CONVERSATION_OWN)
        if self.conversations.get(conversation_id) != (context.organization_id, context.principal.user_id):
            raise TenantDataNotFound('Synthetic private conversation unavailable')

    def history(self, request, context, conversation_id, limit=20, offset=0):
        self._scope(request, context, conversation_id)
        rows = self.rows[conversation_id]
        return {'items': deepcopy(rows[max(0,len(rows)-offset-limit):len(rows)-offset]),
                'has_more': len(rows) > offset+limit}

    def turn(self, request, context, conversation_id, turn_id):
        self._scope(request, context, conversation_id)
        row = next((r for r in self.rows[conversation_id] if r['turn_id'] == turn_id), None)
        if row is None:
            raise TenantDataNotFound('Synthetic private turn unavailable')
        return deepcopy(row)


def model_harness():
    h = harness()
    h[3].principal.permissions.update({Permission.CONVERSATION_OWN, Permission.CATALOGUE_READ,
                                      Permission.STOCK_READ, Permission.RAG_READ})
    h[0].store = SyntheticTurns(h[3])
    h[0].history.store = h[0].store
    h[0].citations.store = h[0].store
    return h


async def answer(h, question, state=None, *, conversation_id='synthetic-conversation', turn_id=None):
    service, _, request, context = h
    store = service.store
    store.open(request, context, conversation_id)
    turn_id = turn_id or f"model-turn-{len(store.rows[conversation_id])+1}"
    existing = next((r for r in store.rows[conversation_id] if r['turn_id'] == turn_id), None)
    if existing is not None:
        assert existing['input']['message'] == question and existing['status'] == 'completed'
        return service._public_turn(request, context, deepcopy(existing), conversation_id)['reply'], state or {}
    row = {'organization_id': context.organization_id, 'owner_user_id': context.principal.user_id,
           'conversation_id': conversation_id, 'turn_id': turn_id, 'kind': 'message', 'status': 'pending',
           'created_at': datetime.now(UTC).isoformat(), 'input': {'message': question}, 'action': None, 'reply': None}
    store.rows[conversation_id].append(row)
    result = await service._graph(request, context, conversation_id, None).ainvoke({
        **(state or {}), 'question': question, 'turn_id': turn_id})
    row.update(status='completed', reply=deepcopy(result['reply']))
    return service._public_turn(request, context, deepcopy(row), conversation_id)['reply'], {
        k: result[k] for k in ('criteria', 'catalogue_categories', 'advice_skus', 'catalogue_search') if k in result}


def verify(case, reply, h, docs, calls, *, expected_source=None, expected_sources=None, expected_source_turn=None):
    value = reply['text']
    folded = value.lower()
    assert reply['order'] is None and reply['can_confirm'] is False
    assert not h[0].orders.mock_calls
    assert not any(term in value for term in ('FOREIGN_DECOY_883', 'PRIVATE_DECOY_991', '883', '991'))
    assert all(set(row) <= {'stocks-guide', 'preparation-guide', 'delivery-guide', 'legacy-price'} for row in docs.observed)
    identity = case['id']
    if identity == 'G15-05':
        assert not docs.docs and any(term in folded for term in ('aucun passage', 'indisponible', 'unavailable', 'no document'))
        assert not any(call['kind'] == 'generation' for call in calls)
        return ['empty-source abstention', 'no generation without evidence', 'no mutation']
    if identity == 'G15-08':
        assert reply['products'] and reply['products'][0]['sku'] == 'DEMO-01-0225'
        assert reply['products'][0]['price_minor'] == 9999
        assert not any(call['kind'] == 'generation' for call in calls), 'Document generation must not decide current catalogue price'
        return ['current exact catalogue price 9999 EUR minor units', 'no document price authority', 'no mutation']
    if identity == 'G15-06':
        assert reply['intent'] == 'RAG_CITATION', 'Expected verified historical sources, not a fresh semantic search'
        assert expected_sources and expected_source_turn
        assert reply.get('cited_sources') == expected_sources, 'Historical citations differ from sources actually cited in the prior answer'
        assert reply.get('source_turn_id') == expected_source_turn, 'Wrong source turn identity'
        assert expected_source in {source['document_id'] for source in reply['cited_sources']}
        assert all(source['document_id'] in value for source in reply['cited_sources'])
        assert not any(call['kind'] == 'generation' for call in calls), 'Historical citation may not be invented by a new model call'
        return ['actual prior model answer verified', 'exact cited permanent document/chunk references',
                'authorized source turn binding', 'no new generation or semantic retrieval', 'no mutation']
    assert reply['intent'] == 'RAG', 'Expected documentary answer, not a catalogue/ORDER shortcut'
    # A deterministic footer attests consulted documents, not support for prose.
    # Require the actual generated answer to cite a source label as well.
    generated = value.split('Documents consult')[0]
    folded = generated.lower()
    # [S2], [source: S2], and an explicit "document S2" reference identify
    # the same source. Formatting is not a stronger oracle than verifiability.
    citations = re.findall(r'(?<![\w-])S(\d+)(?![\w-])', generated)
    assert citations, 'Generated answer has no verifiable inline citation'
    allowed = docs.observed[-1]
    assert all(1 <= int(number) <= len(allowed) for number in citations), 'Fabricated source label'
    # Independent fixture oracle: permanent references must correspond only to
    # labels in generated prose, never to the appended list of consulted data.
    expected_references = []
    for number, document_id in enumerate(allowed, 1):
        if str(number) not in citations:
            continue
        document = next(d for d in docs.docs if d['id'] == document_id and d['org'] == h[3].organization_id)
        expected_references.append({'organization_id': document['org'], 'document_id': document_id,
                                    'generation_id': document.get('generation', 'synthetic-v1-' + document_id),
                                    'chunk_id': 0,
                                    'content_sha256': hashlib.sha256(document['content'].encode()).hexdigest()})
    assert reply.get('cited_sources') == expected_references, 'Structured provenance includes an uncited or mismatched passage'
    assert reply.get('source_turn_id') == h[0].store.rows['synthetic-conversation'][-1]['turn_id']
    assert len(re.sub(r'\[S\d+\]|\W', '', generated)) >= 20, 'Source labels alone do not deliver the requested answer'
    supports = {'G15-01': 'stocks-guide', 'G15-02': 'preparation-guide', 'G15-03': 'delivery-guide',
                'G15-04': 'preparation-guide', 'G15-06': expected_source, 'G15-07': 'delivery-guide',
                'G15-09': 'preparation-guide', 'G15-10': 'delivery-guide'}
    assert supports[identity] in [allowed[int(number)-1] for number in citations], 'Cited source does not support requested fixture fact'
    assert not re.search(r'\b(?:30|48|72|365|50|1000)\b', generated), 'Unsupported numeric policy'
    requirements = {
        'G15-01': ('stock',), 'G15-02': ('préparation', 'confirmation'),
        'G15-03': ('7', 'jour'), 'G15-04': ('réserv', 'confirmation'),
        'G15-06': (), 'G15-07': ('24', 'mois'),
        'G15-09': ('stock', 'confirmation'), 'G15-10': ('14', 'jour', 'inutilisé'),
    }
    for phrase in requirements[identity]:
        assert phrase in folded, f'Missing verified fact: {phrase}'
    if identity == 'G15-02':
        assert 'sans réserv' in folded or 'sans reservation' in folded or 'ne réserve' in folded
        assert 'prédiction' not in folded, 'Preparation may not be recast as an unsupported forecasting function'
    if identity == 'G15-09':
        assert 'pas de document mentionnant une politique' not in folded, 'Unsupported absence claim contradicts the documented stock policy'
    return ['actual generation with inline source labels', 'required fixture facts', 'no foreign/private markers', 'no mutation']


async def run_permutations(provider, documents):
    """Additional experiments; never substituted for the original thirty trials."""
    originals = {case['id']: case for case in CORPUS}
    citation_case = originals['G15-06']
    expected = {'G15-03': 'delivery-guide', 'G15-01': 'stocks-guide', 'G15-04': 'preparation-guide',
                'G15-09': 'preparation-guide'}
    plans = [(f'context-order-{index+1}', order) for index, order in enumerate(permutations(('G15-03', 'G15-01', 'G15-04')))]
    plans += [(name, ('G15-09' if name == 'multiple-citations' else 'G15-03',)) for name in (
        'multiple-citations', 'catalogue-reset', 'deleted-source', 'withdrawn-source',
        'other-conversation', 'other-user', 'other-organization', 'session-change', 'replay-after-deletion')]
    records = []
    for index, (name, order) in enumerate(plans):
        seed = (25051, 25052, 25053)[index % 3]
        provider.seed = seed
        h = model_harness()
        repository = ScopedDocuments(deepcopy(documents))
        adapter = OllamaAdapter(SimpleNamespace(ollama_model='qwen2.5:7b'))
        h[0].llm = adapter
        h[0].retrieval = TenantRetrieval(repository, provider, adapter,
                                       model='nomic-embed-text:latest', version='v025-isolated-v1')
        h[0].citations.retrieval = h[0].retrieval
        original_catalogue = deepcopy(h[1].rows)
        start, call_start, error_start = perf_counter(), len(provider.calls), len(provider.transport_errors)
        record = {'name': name, 'seed': seed, 'message': citation_case['message'], 'status': 'FAIL', 'steps': []}
        try:
            with patch('stockpilot.infrastructure.llm.ollama_adapter.post_ollama', provider.post):
                state = None
                for prior_id in order:
                    prior_start = len(provider.calls)
                    prior, state = await answer(h, originals[prior_id]['message'], state)
                    prior_assertions = verify(originals[prior_id], prior, h, repository, provider.calls[prior_start:])
                    sources = deepcopy(prior.get('cited_sources'))
                    source_turn = prior.get('source_turn_id')
                    assert sources and source_turn, 'Actual prior answer lacks verified structured citations'
                    if name == 'multiple-citations':
                        assert len(sources) >= 2, 'Actual model did not produce the multiple-source prerequisite'
                    prior_rows = deepcopy(h[0].store.rows['synthetic-conversation'])
                    step = {'prior_id': prior_id, 'prior_reply': prior, 'prior_assertions': prior_assertions,
                            'expected_sources': sources, 'source_turn_id': source_turn}
                    record['steps'].append(step)
                    target_h, target_conv = h, 'synthetic-conversation'
                    absent = name in {'catalogue-reset', 'deleted-source', 'withdrawn-source', 'other-conversation'}
                    if name == 'catalogue-reset':
                        reset, state = await answer(h, 'Je cherche DEMO-01-0225.', state)
                        assert reset['products'] and not reset.get('cited_sources')
                    elif name == 'deleted-source':
                        repository.docs = [d for d in repository.docs if d['id'] not in {s['document_id'] for s in sources}]
                    elif name == 'withdrawn-source':
                        for doc in repository.docs:
                            if doc['id'] in {s['document_id'] for s in sources}: doc['active'] = False
                    elif name == 'other-conversation':
                        target_conv = 'different-synthetic-conversation'
                    elif name in {'other-user', 'other-organization', 'session-change'}:
                        new_context = deepcopy(h[3])
                        if name == 'other-user': new_context.principal.user_id = 'other-authorized-user'
                        elif name == 'other-organization': new_context.organization_id = 'org-b'
                        else: new_context.authorization_context_id = 'reauthenticated-synthetic-session'
                        h[0].store.register(new_context)
                        target_h = h[0], h[1], (new_context.organization_id, new_context.principal.user_id), new_context
                    follow_start, embedding_start, search_start = len(provider.calls), len(provider.embedding_inputs), len(repository.observed)
                    if name == 'session-change':
                        try:
                            await answer(h, citation_case['message'], state)
                        except AuthorizationDenied:
                            step['stale_session_denied'] = True
                        else:
                            raise AssertionError('Stale session read was accepted')
                    if name in {'other-user', 'other-organization'}:
                        try:
                            await answer(target_h, citation_case['message'], state)
                        except (AuthorizationDenied, TenantDataNotFound):
                            step['denied'] = True
                        else:
                            raise AssertionError('Foreign principal read was accepted')
                    else:
                        reply, state = await answer(target_h, citation_case['message'], state, conversation_id=target_conv)
                        step['reply'] = reply
                        if absent:
                            assert not reply.get('cited_sources')
                            assert not any(s['document_id'] in reply['text'] for s in sources)
                            assert any(term in reply['text'].lower() for term in ('aucun', 'indisponible', 'vérifiable'))
                        else:
                            step['assertions'] = verify(citation_case, reply, target_h, repository, provider.calls[follow_start:],
                                                        expected_source=expected[prior_id], expected_sources=sources,
                                                        expected_source_turn=source_turn)
                        if name == 'replay-after-deletion':
                            turn_id = h[0].store.rows[target_conv][-1]['turn_id']
                            replay, _ = await answer(h, citation_case['message'], state, turn_id=turn_id)
                            assert replay == reply
                            repository.docs = [d for d in repository.docs if d['id'] not in {s['document_id'] for s in sources}]
                            removed, _ = await answer(h, citation_case['message'], state, turn_id=turn_id)
                            step['replay_after_deletion'] = removed
                            assert not removed.get('cited_sources')
                            assert not any(s['document_id'] in removed['text'] for s in sources)
                    assert len(provider.calls) == follow_start, 'Citation follow-up called the model'
                    assert len(provider.embedding_inputs) == embedding_start, 'Citation follow-up embedded its text'
                    assert len(repository.observed) == search_start, 'Citation follow-up searched by semantic resemblance'
                    assert h[0].store.rows['synthetic-conversation'][:len(prior_rows)] == prior_rows
                assert original_catalogue == h[1].rows and not h[0].orders.mock_calls
            if len(provider.transport_errors) > error_start:
                raise ConnectionError('Isolated provider transport unavailable')
            record['status'] = 'PASS'
        except (TimeoutError, ConnectionError, httpx.HTTPError) as error:
            record.update(status='SKIP', reason=type(error).__name__ + ': isolated model unavailable')
        except Exception as error:
            record['reason'] = str(error) or type(error).__name__
        record.update(latency_ms=round((perf_counter()-start)*1000, 2), provider_calls=provider.calls[call_start:],
                      transport_errors=provider.transport_errors[error_start:],
                      retrieved_document_ids=repository.observed, revalidated_passages=repository.resolved)
        records.append(record)
        print('supplemental', name, seed, record['status'], record.get('reason', ''), flush=True)
    return records


async def run(args):
    started_at = datetime.now(UTC).isoformat()
    start_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    start_dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip())
    provider = Provider(args.url, windows_curl=args.windows_curl, timeout=args.timeout)
    version = await provider.request('/api/version')
    tags = await provider.request('/api/tags')
    models = {m['name']: m for m in tags['models']}
    for name in ('qwen2.5:7b', 'nomic-embed-text:latest'):
        if name not in models:
            raise RuntimeError('Required model absent from the isolated instance: ' + name)
    documents = deepcopy(DOCUMENTS)
    for doc in documents:
        doc['embedding'] = await provider.embed(doc['content'])
    records = []
    for case in [c for c in CORPUS if c['id'] in (set(args.case) if args.case else {f'G15-{i:02}' for i in range(1,11)})]:
        record = {**case, 'trials': [], 'layer': 'actual Ollama + production graph/retrieval + synthetic memory ports'}
        for seed in (25051, 25052, 25053):
            provider.seed = seed
            h = model_harness()
            repository = ScopedDocuments([] if case['id'] == 'G15-05' else documents)
            adapter = OllamaAdapter(SimpleNamespace(ollama_model='qwen2.5:7b'))
            h[0].llm = adapter
            h[0].retrieval = TenantRetrieval(repository, provider, adapter, model='nomic-embed-text:latest', version='v025-isolated-v1')
            h[0].citations.retrieval = h[0].retrieval
            before = deepcopy(h[1].rows)
            start, call_start, error_start = perf_counter(), len(provider.calls), len(provider.transport_errors)
            trial = {'seed': seed, 'status': 'FAIL'}
            expected_source, prior_failure = None, None
            expected_sources, expected_source_turn = None, None
            follow_call_start, follow_embedding_start, follow_retrieval_start = call_start, len(provider.embedding_inputs), 0
            try:
                with patch('stockpilot.infrastructure.llm.ollama_adapter.post_ollama', provider.post):
                    state = None
                    if case['id'] == 'G15-08':
                        _, state = await answer(h, 'Je cherche DEMO-01-0225.')
                        trial['prior_messages'] = ['Je cherche DEMO-01-0225.']
                    if case['id'] == 'G15-06':
                        prior_id, expected_source = {
                            25051: ('G15-03', 'delivery-guide'),
                            25052: ('G15-01', 'stocks-guide'),
                            25053: ('G15-04', 'preparation-guide'),
                        }[seed]
                        prior_case = next(c for c in CORPUS if c['id'] == prior_id)
                        prior_reply, state = await answer(h, prior_case['message'])
                        expected_sources = deepcopy(prior_reply.get('cited_sources'))
                        expected_source_turn = prior_reply.get('source_turn_id')
                        trial.update(prior_messages=[prior_case['message']], prior_reply=prior_reply,
                                     prior_case_id=prior_id, expected_source_id=expected_source)
                        try:
                            trial['prior_assertions'] = verify(prior_case, prior_reply, h, repository, provider.calls[call_start:])
                            trial['prior_status'] = 'PASS'
                        except AssertionError as error:
                            prior_failure = str(error)
                            trial.update(prior_status='FAIL', prior_reason=prior_failure)
                    prior_turns = deepcopy(h[0].store.rows.get('synthetic-conversation', []))
                    follow_call_start = len(provider.calls)
                    follow_embedding_start = len(provider.embedding_inputs)
                    follow_retrieval_start = len(repository.observed)
                    reply, _ = await answer(h, case['message'], state)
                trial['reply'] = reply
                if len(provider.transport_errors) > error_start:
                    raise ConnectionError('Isolated provider transport unavailable')
                assert before == h[1].rows
                trial['assertions'] = verify(case, reply, h, repository, provider.calls[follow_call_start:], expected_source=expected_source,
                                             expected_sources=expected_sources, expected_source_turn=expected_source_turn)
                if case['id'] == 'G15-06':
                    assert len(provider.embedding_inputs) == follow_embedding_start
                    assert len(repository.observed) == follow_retrieval_start
                    assert h[0].store.rows['synthetic-conversation'][:len(prior_turns)] == prior_turns
                assert prior_failure is None, 'Previous answer was not qualified: ' + str(prior_failure)
                trial['status'] = 'PASS'
            except (TimeoutError, ConnectionError, httpx.HTTPError) as error:
                trial.update(status='SKIP', reason=type(error).__name__ + ': isolated model unavailable')
            except Exception as error:
                trial['reason'] = str(error) or type(error).__name__
            trial.update(latency_ms=round((perf_counter()-start)*1000, 2), provider_calls=provider.calls[call_start:],
                         transport_errors=provider.transport_errors[error_start:],
                         retrieved_document_ids=repository.observed, revalidated_passages=repository.resolved,
                         followup_provider_calls=provider.calls[follow_call_start:])
            record['trials'].append(trial)
            print(case['id'], seed, trial['status'], trial.get('reason', ''), flush=True)
        statuses = [t['status'] for t in record['trials']]
        record['status'] = 'FAIL' if 'FAIL' in statuses else 'SKIP' if 'SKIP' in statuses else 'PASS'
        records.append(record)
    supplemental = await run_permutations(provider, documents) if args.permutations_output else None
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip())
    report = {'head_sha': sha, 'checkout_sha': start_sha,
              'working_tree_clean': not dirty and not start_dirty and sha == start_sha,
              'started_at': started_at, 'completed_at': datetime.now(UTC).isoformat(), 'corpus_sha256': digest(),
              'document_corpus_sha256': hashlib.sha256(json.dumps(DOCUMENTS, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
              'model': {'name': 'qwen2.5:7b', 'digest': models['qwen2.5:7b']['digest'], 'version': version['version'],
                        'options': {'temperature_generation': .2, 'temperature_classifier': 0, 'num_predict': 256, 'num_ctx': 4096},
                        'embedding_name': 'nomic-embed-text:latest', 'embedding_digest': models['nomic-embed-text:latest']['digest']},
              'trials_per_case': 3, 'cases': records,
              'limitations': ['Memory repository scope is not proof of PostgreSQL enforcement; join direct CI evidence.',
                              'The extra private-document decoy uses a harness-only ACL; production permissions are organization plus RAG_READ, not per-document user grants.',
                              'Finite numeric/fact/citation assertions need review of synthetic answer prose for other unsupported claims.',
                              'No real business data, deployment, private environment, or external service used.']}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if args.permutations_output:
        separate = {key: value for key, value in report.items() if key not in {'cases', 'trials_per_case'}}
        separate.update(kind='additional G15-06 permutations, separate from original 301 corpus', cases=supplemental)
        args.permutations_output.write_text(json.dumps(separate, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--isolated-synthetic-only', action='store_true', required=True)
    parser.add_argument('--windows-curl', action='store_true')
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--case', action='append', choices=[f'G15-{i:02}' for i in range(1,11)], help='Exploratory subset only; omit for qualification')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--permutations-output', type=Path, help='Separate additional G15-06 permutation evidence')
    args = parser.parse_args()
    url = urlparse(args.url)
    if url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1') or url.port in (None,11434):
        parser.error('Use the explicit non-default loopback port of an owned isolated Ollama instance')
    asyncio.run(run(args))


if __name__ == '__main__':
    main()
