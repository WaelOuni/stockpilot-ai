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
import math
from pathlib import Path
import re
import subprocess
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse

import httpx

from .campaign import CORPUS, answer, digest, harness
from stockpilot.application.services.identity_authorization import AuthorizationDenied
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
        value = await self.request('/api/embeddings', {'model': 'nomic-embed-text:latest', 'prompt': content, 'keep_alive': '5m'})
        return value['embedding']


class ScopedDocuments:
    def __init__(self, docs):
        self.docs, self.observed = docs, []

    def current(self, request, context, permission):
        if request != (context.organization_id, context.principal.user_id) or permission not in context.principal.permissions:
            raise AuthorizationDenied('Synthetic document permission denied')

    def search(self, request, context, embedding, *, model, version):
        self.current(request, context, Permission.RAG_READ)
        assert model == 'nomic-embed-text:latest' and version == 'v025-isolated-v1'
        candidates = [d for d in self.docs if d['org'] == context.organization_id and context.principal.user_id in d['users']]
        def distance(doc):
            vector = doc['embedding']
            return 1 - sum(a*b for a,b in zip(embedding, vector)) / (math.sqrt(sum(x*x for x in embedding)) * math.sqrt(sum(x*x for x in vector)))
        candidates.sort(key=lambda d: (distance(d), d['id']))
        candidates = candidates[:3]  # Match PgTenantKnowledge.search's production default.
        self.observed.append([d['id'] for d in candidates])
        return [TenantPassage(d['org'], d['id'], 0, d['content'], distance(d)) for d in candidates]


def verify(case, reply, h, docs, calls):
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
    assert len(re.sub(r'\[S\d+\]|\W', '', generated)) >= 20, 'Source labels alone do not deliver the requested answer'
    supports = {'G15-01': 'stocks-guide', 'G15-02': 'preparation-guide', 'G15-03': 'delivery-guide',
                'G15-04': 'preparation-guide', 'G15-06': 'delivery-guide', 'G15-07': 'delivery-guide',
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
            h = harness()
            h[3].principal.permissions.add(Permission.RAG_READ)
            repository = ScopedDocuments([] if case['id'] == 'G15-05' else documents)
            adapter = OllamaAdapter(SimpleNamespace(ollama_model='qwen2.5:7b'))
            h[0].llm = adapter
            h[0].retrieval = TenantRetrieval(repository, provider, adapter, model='nomic-embed-text:latest', version='v025-isolated-v1')
            before = deepcopy(h[1].rows)
            start, call_start, error_start = perf_counter(), len(provider.calls), len(provider.transport_errors)
            trial = {'seed': seed, 'status': 'FAIL'}
            try:
                with patch('stockpilot.infrastructure.llm.ollama_adapter.post_ollama', provider.post):
                    state = None
                    if case['id'] == 'G15-08':
                        _, state = await answer(h, 'Je cherche DEMO-01-0225.')
                        trial['prior_messages'] = ['Je cherche DEMO-01-0225.']
                    if case['id'] == 'G15-06':
                        prior = next(c['message'] for c in CORPUS if c['id'] == 'G15-03')
                        prior_reply, state = await answer(h, prior)
                        trial['prior_messages'], trial['prior_reply'] = [prior], prior_reply
                    reply, _ = await answer(h, case['message'], state)
                trial['reply'] = reply
                if len(provider.transport_errors) > error_start:
                    raise ConnectionError('Isolated provider transport unavailable')
                assert before == h[1].rows
                trial['assertions'] = verify(case, reply, h, repository, provider.calls[call_start:])
                trial['status'] = 'PASS'
            except (TimeoutError, ConnectionError, httpx.HTTPError) as error:
                trial.update(status='SKIP', reason=type(error).__name__ + ': isolated model unavailable')
            except Exception as error:
                trial['reason'] = str(error) or type(error).__name__
            trial.update(latency_ms=round((perf_counter()-start)*1000, 2), provider_calls=provider.calls[call_start:],
                         transport_errors=provider.transport_errors[error_start:],
                         retrieved_document_ids=repository.observed)
            record['trials'].append(trial)
            print(case['id'], seed, trial['status'], trial.get('reason', ''), flush=True)
        statuses = [t['status'] for t in record['trials']]
        record['status'] = 'FAIL' if 'FAIL' in statuses else 'SKIP' if 'SKIP' in statuses else 'PASS'
        records.append(record)
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--isolated-synthetic-only', action='store_true', required=True)
    parser.add_argument('--windows-curl', action='store_true')
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--case', action='append', choices=[f'G15-{i:02}' for i in range(1,11)], help='Exploratory subset only; omit for qualification')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    url = urlparse(args.url)
    if url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1') or url.port in (None,11434):
        parser.error('Use the explicit non-default loopback port of an owned isolated Ollama instance')
    asyncio.run(run(args))


if __name__ == '__main__':
    main()
