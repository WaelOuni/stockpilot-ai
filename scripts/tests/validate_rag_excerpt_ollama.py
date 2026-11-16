"""Opt-in real RAG qualification; only dedicated Docker services and synthetic data.

Real Ollama adapters are observed without changing prompts or transport. Tests
labelled fault injection deliberately corrupt a real model response afterwards.
"""
import asyncio
from copy import deepcopy
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from pymongo.uri_parser import parse_uri
import pytest

from integration_targets import mongo_test_client, verify_postgres_identity
from test_b2b_http_mongo_integration import http as http  # noqa: F401
from test_tenant_knowledge_integration import environment as environment  # noqa: F401
from test_tenant_orders_mongo_integration import REQUESTS, context
from test_tenant_orders_mongo_integration import db as db, orders as orders  # noqa: F401
from test_tenant_stock_operations_mongo_integration import stock as stock  # noqa: F401
from scripts.e2e_targets import verify_local_daemon
from stockpilot.application.ports.tenant_knowledge import TenantDataConflict
from stockpilot.application.services.tenant_assistant import TenantAssistant
from stockpilot.application.services.tenant_assistant_citations import SOURCE_UNAVAILABLE
from stockpilot.application.services.tenant_rag_safety import (
    RAG_CITATION_REFUSAL, RAG_INSUFFICIENT_EVIDENCE, RAG_SENSITIVE_REFUSAL,
)
from stockpilot.application.services.tenant_retrieval import TenantRetrieval, RAG_EXCERPT_RESPONSE_FORMAT
from stockpilot.domain.entities.tenant_knowledge import IngestTenantDocument
from stockpilot.infrastructure.embeddings.ollama_embedding_adapter import OllamaEmbeddingAdapter
from stockpilot.infrastructure.identity.bff_tenant_context import BffTenantRequest
from stockpilot.infrastructure.llm.ollama_adapter import OllamaAdapter
from stockpilot.infrastructure.persistence.postgres.tenant_assistant import PgTenantAssistant
from stockpilot.infrastructure.persistence.postgres.tenant_knowledge import PgTenantKnowledge
from stockpilot.presentation.api.routes.b2b_assistant import router

pytestmark = pytest.mark.integration
MODEL, EMBEDDING_MODEL = 'qwen2.5:7b', 'nomic-embed-text'
DELIVERY = "Le délai de livraison de l'entreprise Atelier A est de 7 jours ouvrés."
MAINTENANCE = 'La maintenance du matériel est effectuée tous les six mois.'
FOREIGN = "Le délai de livraison de l'entreprise Atelier B est de 883 jours ouvrés."
SECRET = 'FICTITIOUS-RAG-LIVE-NEVER-A-REAL-CREDENTIAL'
QUESTION = "Quel est le délai de livraison de l'entreprise Atelier A ?"


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def provider_metadata(path):
    with httpx.Client(timeout=10, trust_env=False) as client:
        result = client.get(os.environ['B2B_TEST_OLLAMA_URL'].rstrip('/') + '/api/' + path)
        result.raise_for_status()
        return result.json()


def docker_json(*args):
    return json.loads(subprocess.run(['docker', *args], check=True, capture_output=True,
                                    text=True, timeout=15).stdout)


@pytest.fixture(scope='session', autouse=True)
def verified_targets():
    """Docker provenance first, effective server identity second; no writes."""
    verify_local_daemon()
    evidence = Path(os.environ['RAG_QUAL_EVIDENCE']).resolve()
    proof = json.loads((evidence / 'isolation-proof.json').read_text())
    ids = [service['id'] for service in proof['services'].values()]
    rows = docker_json('inspect', *ids)
    network = docker_json('network', 'inspect', proof['network'])[0]
    assert network['Id'] == proof['network_id'] and set(network['Containers']) == set(ids)
    assert network['Labels']['com.docker.compose.project'] == proof['project']
    endpoints = {}
    for row in rows:
        labels = row['Config']['Labels']
        service = labels['com.docker.compose.service']
        expected = proof['services'][service]
        assert labels['com.docker.compose.project'] == proof['project']
        assert labels['com.docker.compose.project.config_files'] == proof['compose']
        assert row['Id'] == expected['id'] and row['Image'] == expected['image_id']
        assert row['State']['Health']['Status'] == 'healthy'
        assert set(row['NetworkSettings']['Networks']) == {proof['network']}
        assert all(m['Type'] == 'tmpfs' for m in row['Mounts'])
        assert set(row['HostConfig']['Tmpfs']) == set(expected['mounts'])
        internal = '27017/tcp' if service == 'mongo' else '5432/tcp'
        assert row['NetworkSettings']['Ports'][internal] == [
            {'HostIp': '127.0.0.1', 'HostPort': str(expected['port'])}]
        endpoints[service] = expected['port']
    mongo, pg = parse_uri(os.environ['MONGO_TEST_URI']), conninfo_to_dict(os.environ['POSTGRES_TEST_URI'])
    assert mongo['nodelist'] == [('127.0.0.1', endpoints['mongo'])]
    assert pg['host'] == '127.0.0.1' and int(pg['port']) == endpoints['postgres']
    with mongo_test_client() as client:
        assert not set(client.list_database_names()) - {'admin', 'local', 'config'}
        hello = client.admin.command('hello')
        assert hello['setName'] == 'rs0' and hello['isWritablePrimary']
        mongo_version = client.server_info()['version']
    with psycopg.connect(os.environ['POSTGRES_TEST_URI'], connect_timeout=5) as connection:
        connection.execute('SET TRANSACTION READ ONLY')
        verify_postgres_identity(connection, os.environ['POSTGRES_TEST_URI'])
        names = {r[0] for r in connection.execute('SELECT datname FROM pg_database')}
        assert names == {'postgres', 'template0', 'template1', pg['dbname']}
        pg_version = connection.execute('SELECT version()').fetchone()[0]
        system_id = connection.execute('SELECT system_identifier FROM pg_control_system()').fetchone()[0]
        vector_version = connection.execute("SELECT default_version FROM pg_available_extensions WHERE name='vector'").fetchone()[0]
    metadata = {'mongo_version': mongo_version, 'postgres_version': pg_version,
        'postgres_system_identifier': system_id, 'pgvector_available_version': vector_version,
        'postgres_database': pg['dbname'], 'ollama_version': provider_metadata('version'),
        'models': provider_metadata('tags'), 'initial_loaded_models': provider_metadata('ps')}
    assert MODEL in {m['name'] for m in metadata['models']['models']}
    assert EMBEDDING_MODEL + ':latest' in {m['name'] for m in metadata['models']['models']}
    (evidence / 'effective-identities.json').write_text(json.dumps(metadata, indent=2) + '\n')
    handler = logging.FileHandler(evidence / 'runtime-phases.jsonl')
    logger = logging.getLogger('stockpilot.assistant.runtime')
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    yield evidence
    logger.removeHandler(handler)
    logger.setLevel(previous)
    handler.close()


class ObservedModel(OllamaAdapter):
    def __init__(self, settings):
        super().__init__(settings, max_output_tokens=512)
        self.calls = []

    async def classify_intent(self, message):
        started = perf_counter()
        result = await super().classify_intent(message)
        self.calls.append({'kind': 'classification', 'question': message,
            'result': result.model_dump(mode='json'), 'seconds': perf_counter() - started})
        return result

    async def generate(self, prompt, response_format=None):
        started = perf_counter()
        result = await super().generate(prompt, response_format=response_format)
        self.calls.append({'kind': 'generation', 'prompt': prompt, 'format': response_format,
            'raw_response': result, 'seconds': perf_counter() - started})
        return result


class ObservedEmbeddings(OllamaEmbeddingAdapter):
    def __init__(self, settings):
        super().__init__(settings)
        self.calls = []

    async def embed(self, text):
        started = perf_counter()
        result = await super().embed(text)
        self.calls.append({'input': text, 'dimension': len(result), 'seconds': perf_counter() - started})
        return result


class ObservedKnowledge(PgTenantKnowledge):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.searches = []

    def search(self, *args, **kwargs):
        result = super().search(*args, **kwargs)
        self.searches.append([{'organization_id': p.organization_id, 'document_id': p.document_id,
            'generation_id': p.generation_id, 'chunk_id': p.chunk_id, 'content': p.content,
            'content_sha256': sha(p.content)} for p in result])
        return result


@pytest.fixture
def live(environment, http, db, verified_targets, request):
    e, h = environment, http
    config = SimpleNamespace(ollama_base_url=os.environ['B2B_TEST_OLLAMA_URL'], ollama_model=MODEL,
        ollama_embedding_model=EMBEDDING_MODEL, ollama_timeout_seconds=60, ollama_max_prompt_bytes=24 * 1024)
    model, embeddings = ObservedModel(config), ObservedEmbeddings(config)
    ingestion = PgTenantKnowledge(e.uri, e.orders[0].authorization, embeddings, e.schema,
                                 model=EMBEDDING_MODEL, version='v1')

    def ingest(document, content, actor='admin-a'):
        return asyncio.run(ingestion.ingest(REQUESTS[actor], context(e.orders, actor),
            IngestTenantDocument(uuid4().hex, document, document, content, EMBEDDING_MODEL, 'v1', 0)))

    ingest('delivery-guide', DELIVERY)
    ingest('maintenance-guide', MAINTENANCE)
    ingest('delivery-guide', FOREIGN, 'admin-b')
    ingest('withdrawn-guide', 'WITHDRAWN_DECOY_991: ce passage est retiré.')
    ingestion.delete(REQUESTS['admin-a'], context(e.orders, 'admin-a'), 'withdrawn-guide',
                     operation_id=uuid4().hex, expected_revision=1)
    # Legacy data constructed only in this fresh schema, never via production.
    ingest('legacy-sensitive', 'Ancien passage synthétique destiné au contrôle de filtrage.')
    with psycopg.connect(e.uri) as connection:
        connection.execute(sql.SQL('UPDATE {} SET content=%s WHERE organization_id=%s AND document_id=%s')
            .format(ingestion.t('chunks')), ('password=' + SECRET, 'org-a', 'legacy-sensitive'))
    repository = ObservedKnowledge(e.uri, h.bundle.authorization, embeddings, e.schema,
                                  model=EMBEDDING_MODEL, version='v1')
    retrieval = TenantRetrieval(repository, embeddings, model, model=EMBEDDING_MODEL, version='v1')
    store = PgTenantAssistant(e.uri, h.bundle.authorization, e.schema)
    service = TenantAssistant(store, h.bundle.catalogue, h.bundle.orders, retrieval, model)
    h.app.state.bff_assistant = service
    h.app.include_router(router)
    records = {'test': request.node.nodeid, 'database': db.name, 'schema': e.schema,
        'messages': [], 'model_calls': model.calls, 'embedding_calls': embeddings.calls,
        'retrievals': repository.searches}
    watched = ('b2b_products', 'b2b_orders', 'b2b_order_operations', 'b2b_stock_movements')
    before = {name: list(db[name].find({}).sort('_id', 1)) for name in watched}
    harness = SimpleNamespace(environment=e, http=h, config=config, model=model, embeddings=embeddings,
        repository=repository, retrieval=retrieval, service=service, store=store, ingestion=ingestion,
        ingest=ingest, records=records, evidence=verified_targets)
    yield harness
    (verified_targets / (request.node.name.replace('/', '_') + '.json')).write_text(
        json.dumps(records, indent=2, ensure_ascii=False, default=str) + '\n')
    assert {name: list(db[name].find({}).sort('_id', 1)) for name in watched} == before


def send(live, question=QUESTION, *, actor='a1', base=None):
    client = live.http.browser(actor)
    if base is None:
        opened = client.post('/b2b/api/assistant/conversations', json={'thread_id': uuid4().hex})
        assert opened.status_code == 200
        base = '/b2b/api/assistant/conversations/' + opened.json()['conversation_id']
    message_id = uuid4().hex
    started = perf_counter()
    response = client.post(base + '/messages', json={'message_id': message_id,
        'message': question, 'read_only': True})
    seconds = perf_counter() - started
    request = BffTenantRequest(live.http.tokens[actor])
    ctx = live.http.bundle.authorization.resolve_context(request)
    live.records['messages'].append({'question': question, 'actor': actor,
        'conversation_id': base.rsplit('/', 1)[1], 'message_id': message_id,
        'http_status': response.status_code, 'seconds': seconds, 'response': response.json()})
    return response, client, base, request, ctx, message_id


def assert_grounded(live, sent, *, organization='org-a', required='7 jours ouvrés'):
    response, client, base, request, ctx, message_id = sent
    assert response.status_code == 200, response.text
    wire = response.json()
    assert wire['status'] == 'completed' and wire['reply']['intent'] == 'RAG'
    assert wire['reply']['order'] is None and wire['reply']['can_confirm'] is False
    row = live.store.turn(request, ctx, base.rsplit('/', 1)[1], message_id)
    reply = row['reply']
    generated = next(c for c in reversed(live.model.calls) if c['kind'] == 'generation')
    assert generated['format'] == RAG_EXCERPT_RESPONSE_FORMAT
    draft = json.loads(generated['raw_response'])
    assert set(draft) == {'answerable', 'excerpts'} and draft['answerable'] is True
    assert 1 <= len(draft['excerpts']) <= 5
    passages = live.repository.searches[-1]
    source_map = {f'S{i}': passage for i, passage in enumerate(passages, 1)}
    expected_references = []
    for item in draft['excerpts']:
        assert set(item) == {'source_id', 'excerpt'}
        passage = source_map[item['source_id']]
        assert item['excerpt'] and item['excerpt'] in passage['content']
        assert passage['organization_id'] == organization
        expected_references.append({k: passage[k] for k in (
            'organization_id', 'document_id', 'generation_id', 'chunk_id', 'content_sha256')})
    assert reply['cited_sources'] == expected_references
    assert all(ref in reply['_rag_read_sources'] for ref in expected_references)
    rendered = '\n'.join(f'[{item["source_id"]}] « {item["excerpt"]} »' for item in draft['excerpts'])
    assert wire['reply']['text'] == reply['text'] == rendered
    assert required in rendered
    assert not any(term in generated['prompt'] + rendered for term in (SECRET, 'WITHDRAWN_DECOY_991'))
    if organization == 'org-a':
        assert '883' not in generated['prompt'] + rendered
        assert {p['document_id'] for p in passages} == {'delivery-guide', 'maintenance-guide'}
        assert {r['document_id'] for r in expected_references} == {'delivery-guide'}
    history = client.get(base)
    assert history.status_code == 200
    historical = next(item for item in history.json()['items'] if item['turn_id'] == message_id)
    assert historical['reply']['text'] == rendered
    assert not {'_citation_scope', '_rag_read_sources', 'classification'} & wire['reply'].keys()
    live.records.setdefault('validated_replies', []).append(reply)
    return row


def test_01_real_pipeline_cold_and_warm(live):
    assert live.service._lexical_discovery(QUESTION) is None
    live.records['qwen_before_first_question'] = provider_metadata('ps')
    first = send(live)
    assert_grounded(live, first)
    assert any(c['kind'] == 'classification' and c['result']['intent'] == 'UNKNOWN'
               for c in live.model.calls)
    live.records['qwen_before_second_question'] = provider_metadata('ps')
    second = send(live)
    assert_grounded(live, second)
    assert first[2] != second[2]
    assert all(call['dimension'] == 768 for call in live.embeddings.calls)


def test_real_model_abstains_when_evidence_is_missing(live):
    response, _, base, request, ctx, turn = send(live,
        "Quelle est la durée contractuelle de conservation des archives ?")
    assert response.status_code == 200
    assert response.json()['reply']['text'] == RAG_INSUFFICIENT_EVIDENCE
    row = live.store.turn(request, ctx, base.rsplit('/', 1)[1], turn)
    assert row['reply']['cited_sources'] == []
    generated = next(c for c in reversed(live.model.calls) if c['kind'] == 'generation')
    assert json.loads(generated['raw_response']) == {'answerable': False, 'excerpts': []}


def test_real_pg_tenant_scope_and_http_ownership(live):
    first = send(live)
    assert_grounded(live, first)
    assert live.http.browser('b1').get(first[2]).status_code == 404
    assert live.http.browser('a3').get(first[2]).status_code == 404
    assert first[1].get(first[2], headers={'X-Organization-Id': 'org-b'}).status_code == 400
    other = send(live, "Quel est le délai de livraison de l'entreprise Atelier B ?", actor='b1')
    assert_grounded(live, other, organization='org-b', required='883 jours ouvrés')


def test_real_security_refusal_and_ingestion_guard(live):
    before_model, before_embedding = len(live.model.calls), len(live.embeddings.calls)
    response, _, base, request, ctx, turn = send(live,
        'Selon les documents, quel est le mot de passe Keycloak ?')
    assert response.status_code == 200 and response.json()['reply']['text'] == RAG_SENSITIVE_REFUSAL
    assert len(live.model.calls) == before_model and len(live.embeddings.calls) == before_embedding
    assert live.store.turn(request, ctx, base.rsplit('/', 1)[1], turn)['reply']['cited_sources'] == []
    with pytest.raises(TenantDataConflict, match='credential-like'):
        live.ingest('forbidden-new', 'api_key=' + SECRET)
    assert len(live.embeddings.calls) == before_embedding


@pytest.mark.parametrize('corruption,expected', [
    ('invented_excerpt', RAG_INSUFFICIENT_EVIDENCE),
    ('unknown_reference', RAG_CITATION_REFUSAL),
    ('foreign_excerpt', RAG_INSUFFICIENT_EVIDENCE),
    ('free_prose', RAG_INSUFFICIENT_EVIDENCE),
])
def test_fault_injection_after_real_generation_is_rejected(live, corruption, expected):
    async def corrupt(*, prompt, response_format):
        original = await live.model.generate(prompt, response_format)
        draft = json.loads(original)
        assert draft['answerable'] and draft['excerpts']
        if corruption == 'invented_excerpt':
            draft['excerpts'][0]['excerpt'] = 'La livraison prend 123456 jours inventés.'
        elif corruption == 'unknown_reference':
            draft['excerpts'][0]['source_id'] = 'S99'
        elif corruption == 'foreign_excerpt':
            draft['excerpts'][0]['excerpt'] = FOREIGN
        else:
            live.records['injection'] = 'La livraison prend 123456 jours inventés.'
            return live.records['injection']
        live.records['injection'] = draft
        return json.dumps(draft)
    live.retrieval.generator = SimpleNamespace(generate=corrupt)
    response, _, base, request, ctx, turn = send(live)
    assert response.status_code == 200 and response.json()['reply']['text'] == expected
    reply = live.store.turn(request, ctx, base.rsplit('/', 1)[1], turn)['reply']
    assert reply['cited_sources'] == [] and reply['_rag_read_sources'] == []
    assert '123456' not in response.text and '883' not in response.text


def test_real_history_revalidates_sources_without_rewriting_turn(live):
    sent = send(live)
    original = deepcopy(assert_grounded(live, sent))
    _, client, base, request, ctx, turn = sent
    live.ingestion.delete(REQUESTS['admin-a'], context(live.environment.orders, 'admin-a'),
                          'delivery-guide', operation_id=uuid4().hex, expected_revision=1)
    count = len(live.model.calls)
    history = client.get(base)
    assert history.status_code == 200
    row = next(t for t in history.json()['items'] if t['turn_id'] == turn)
    assert row['reply']['text'] == SOURCE_UNAVAILABLE
    assert live.store.turn(request, ctx, base.rsplit('/', 1)[1], turn) == original
    assert len(live.model.calls) == count


@pytest.mark.parametrize('failure', ['missing_model', 'timeout'])
def test_real_provider_errors_fail_without_documentary_success(live, failure):
    config = SimpleNamespace(**vars(live.config))
    if failure == 'missing_model':
        config.ollama_model = 'stockpilot-qualification-absent-' + uuid4().hex
    else:
        config.ollama_timeout_seconds = 0.01  # This test client only; production stays 60s.
    live.retrieval.generator = OllamaAdapter(config, max_output_tokens=32)
    response, client, base, request, ctx, turn = send(live,
        'Selon les documents, quel est le délai de livraison ?')
    assert response.status_code == 503
    assert '7 jours' not in response.text and '172.18.' not in response.text
    stored = live.store.turn(request, ctx, base.rsplit('/', 1)[1], turn)
    # The established retry contract keeps SQL status pending and stores the
    # failed read attempt separately (test_assistant_resilience_integration).
    assert stored['status'] == 'pending' and stored['action'] is None
    assert stored['reply']['intent'] == 'ASSISTANT_UNAVAILABLE'
    history = client.get(base)
    assert history.status_code == 200
    public = next(item for item in history.json()['items'] if item['turn_id'] == turn)
    assert public['status'] == 'pending' and public['execution']['state'] == 'failed'
    expected_code = 'provider_http_error' if failure == 'missing_model' else 'read_timeout'
    assert public['execution']['code'] == expected_code and public['execution']['retryable'] is True
    assert public['reply']['intent'] == 'ASSISTANT_UNAVAILABLE'
    assert public['reply']['order'] is None and public['reply']['can_confirm'] is False
    assert public['reply'].get('cited_sources', []) == [] and '_read_attempt' not in public['reply']
    live.records['failed_turn'] = public
    live.records['provider_failure_kind'] = failure


def test_real_english_question_keeps_authorized_verbatim_evidence(live):
    assert_grounded(live, send(live, 'What is the delivery time for Atelier A?'))


def test_http_input_limit_precedes_model(live):
    response, _, _, _, _, _ = send(live, 'x' * 4001)
    assert response.status_code == 422 and live.model.calls == []
