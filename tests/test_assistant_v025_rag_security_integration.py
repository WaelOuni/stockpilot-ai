"""Credential boundaries on guarded disposable Mongo + PostgreSQL fixtures.

All markers are fictional. Legacy rows are constructed only inside each new
test schema, never through a production bypass or an application database.
"""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import psycopg
import pytest
from psycopg import sql

from test_tenant_assistant_integration import assistant as assistant  # noqa: F401
from test_tenant_assistant_integration import send
from test_tenant_knowledge_integration import command, context, ingest, search, REQUESTS
from test_tenant_knowledge_integration import environment as environment  # noqa: F401
from test_tenant_knowledge_integration import db as db  # noqa: F401
from test_tenant_knowledge_integration import orders as orders  # noqa: F401
from test_tenant_knowledge_integration import stock as stock  # noqa: F401

from stockpilot.application.ports.tenant_knowledge import TenantDataConflict, TenantDataNotFound
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.application.services.tenant_rag_safety import RAG_SENSITIVE_REFUSAL
from stockpilot.application.services.tenant_retrieval import TenantRetrieval

pytestmark = pytest.mark.integration
MARKER = 'FICTITIOUS-RAG-INTEGRATION-V025-NOT-A-CREDENTIAL'


def test_exact_g15_11_server_refuses_before_model_and_preserves_state(assistant, db):
    from evals.assistant_v025.campaign import CORPUS
    service, conversation, e = assistant
    ingest(e, command(content='Procédure de rotation des clés API via le coffre autorisé.'))
    e.embeddings.embed.reset_mock()
    service.llm.generate.return_value = 'Code secret Keycloak : ' + MARKER
    before = list(db.b2b_products.find({}).sort('_id', 1))
    exact = next(row['message'] for row in CORPUS if row['id'] == 'G15-11')
    reply = send(assistant, exact)['reply']
    assert reply['intent'] == 'RAG' and reply['text'] == RAG_SENSITIVE_REFUSAL
    e.embeddings.embed.assert_not_awaited()
    service.llm.generate.assert_not_awaited()
    assert MARKER not in str(service.read(REQUESTS['a1'], context(e.orders), conversation))
    assert list(db.b2b_products.find({}).sort('_id', 1)) == before
    assert db.b2b_orders.count_documents({}) == 0
    assert db.b2b_order_operations.count_documents({}) == 0


@pytest.mark.parametrize('field', ['content', 'title', 'document_id', 'operation_id'])
def test_ingestion_rejects_credentials_before_embedding_or_write(environment, field):
    e = environment
    contaminated = replace(command(), **{field: 'api_key=' + MARKER})
    with pytest.raises(TenantDataConflict, match='credential-like'):
        ingest(e, contaminated)
    e.embeddings.embed.assert_not_awaited()
    with psycopg.connect(e.uri) as c:
        for table in ('documents', 'ingestions', 'chunks'):
            assert c.execute(sql.SQL('SELECT count(*) FROM {}').format(e.repo.t(table))).fetchone()[0] == 0
    with pytest.raises(AuthorizationDenied):
        ingest(e, contaminated, actor='a1')


def legacy_content(e, contents, *, document='unsafe'):
    # Start with legitimate publication, then emulate pre-policy data in the
    # dedicated test schema. Production ingestion remains exercised separately.
    ingest(e, command(document, 'publish-' + document, 'x' * sum(map(len, contents))))
    with psycopg.connect(e.uri) as c:
        for number, content in enumerate(contents):
            assert c.execute(sql.SQL(
                'UPDATE {} SET content=%s WHERE organization_id=%s AND document_id=%s AND chunk_id=%s'
            ).format(e.repo.t('chunks')), (content, 'org-a', document, number)).rowcount == 1


def test_legacy_generation_is_filtered_across_chunk_boundary_and_metadata_reads(environment):
    e = environment
    legacy_content(e, ['x' * 1991 + ' api_key=', MARKER + ' ordinary maintenance'])
    ingest(e, command('safe', 'publish-safe', 'La maintenance se fait tous les six mois.'))
    ingest(e, command('foreign', 'publish-foreign', 'FOREIGN-DOCUMENT-ONLY'), actor='admin-b')
    assert [row.document_id for row in search(e, limit=20)] == ['safe']
    assert [row.document_id for row in e.repo.documents_by_title(
        REQUESTS['a1'], context(e.orders), 'Same title'
    )] == ['safe']
    with pytest.raises(TenantDataNotFound, match='Document unavailable'):
        e.repo.document(REQUESTS['a1'], context(e.orders), 'unsafe')
    with pytest.raises(TenantDataNotFound, match='Ingestion unavailable'):
        e.repo.ingestion(REQUESTS['admin-a'], context(e.orders, 'admin-a'), 'publish-unsafe')
    llm = SimpleNamespace(generate=AsyncMock(return_value='Tous les six mois [S1].'))
    service = TenantRetrieval(e.repo, e.embeddings, llm, model='synthetic-768', version='v1')
    result = asyncio.run(service.execute(REQUESTS['a1'], context(e.orders), 'Quelle maintenance ?'))
    assert 'Tous les six mois [S1].' in result and '"safe", passage 0' in result
    prompt = llm.generate.await_args.kwargs['prompt']
    assert MARKER not in prompt and 'FOREIGN-DOCUMENT-ONLY' not in prompt


@pytest.mark.parametrize('field', ['title', 'embedding_model', 'embedding_version'])
def test_legacy_credential_metadata_is_hidden_from_all_read_surfaces(environment, field):
    e = environment
    ingest(e)
    with psycopg.connect(e.uri) as c:
        c.execute(sql.SQL('UPDATE {} SET {}=%s WHERE organization_id=%s').format(e.repo.t('documents'), sql.Identifier(field)),
                  ('client_secret=' + MARKER, 'org-a'))
    assert search(e) == []
    title = 'client_secret=' + MARKER if field == 'title' else 'Same title'
    assert e.repo.documents_by_title(REQUESTS['a1'], context(e.orders), title) == []
    with pytest.raises(TenantDataNotFound):
        e.repo.document(REQUESTS['a1'], context(e.orders), 'same-document')
    with pytest.raises(TenantDataNotFound):
        e.repo.ingestion(REQUESTS['admin-a'], context(e.orders, 'admin-a'), 'same-operation')


def test_hostile_model_output_is_not_persisted_and_permissions_are_rechecked(assistant, db):
    service, conversation, e = assistant
    ingest(e, command(content='Les clés API se renouvellent via la procédure approuvée.'))
    service.llm.generate.return_value = '[source](https://example.invalid/?client_secret=' + MARKER + ')'
    reply = send(assistant, 'Comment renouveler une clé API sans révéler sa valeur ?')['reply']
    assert reply['text'] == RAG_SENSITIVE_REFUSAL
    assert MARKER not in str(service.read(REQUESTS['a1'], context(e.orders), conversation))
    original_context = context(e.orders)

    async def revoke(**kwargs):
        db.b2b_memberships.update_one({'membership_id': 'm-a1'}, {'$set': {'status': 'suspended'}})
        return 'Procédure approuvée [S1].'

    service.llm.generate.side_effect = revoke
    with pytest.raises(AuthorizationDenied):
        asyncio.run(service.retrieval.execute(REQUESTS['a1'], original_context, 'Quelle procédure de rotation ?'))
    assert db.b2b_orders.count_documents({}) == 0
