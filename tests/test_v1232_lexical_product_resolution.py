"""Keep quantity grammar separate from literal product evidence, never guess a SKU."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from stockpilot.application.use_cases.resolve_product import ResolveProductUseCase, ResolutionStatus, normalize_name
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.application.workflows.nodes import create_order_node, create_classify_node
from stockpilot.application.use_cases.classify_intent import ClassifyIntentUseCase
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.domain.entities.product import Product
from stockpilot.domain.enums.product_category import ProductCategory
from stockpilot.domain.enums.intent import Intent


def catalogue(products):
    async def names(reference, *, exact, limit):
        terms = normalize_name(reference).split()
        return [p for p in products if normalize_name(p.name) == normalize_name(reference)
                or (not exact and all(t in normalize_name(p.name).split() for t in terms))][:limit]
    return SimpleNamespace(find_resolution_skus=AsyncMock(side_effect=lambda sku:
        [p for p in products if p.sku.casefold() == sku.casefold()]),
        find_resolution_candidates=AsyncMock(side_effect=names))


def item(name='Vis Synthétiques V015', sku='DEMO-SCREW-0001', id='507f1f77bcf86cd799439011'):
    return Product(sku, name, ProductCategory.OTHER, 1., 30, id)


@pytest.mark.parametrize('count,quantity', [('une douzaine de',12),('une demi-douzaine de',6),
    ('deux',2),('trois',3),('six',6)])
def test_full_literal_name_after_lexical_count_is_prepared_without_reservation(count, quantity):
    product=item(); repo=catalogue([product]); prepare=PrepareOrderUseCase(SimpleNamespace(),ResolveProductUseCase(repo))
    result=asyncio.run(create_order_node(prepare)({'message':f'Commande {count} Vis Synthétiques V015',
        'quantity':quantity,'product_name':None,'customer_id':'synthetic'}))
    assert result['order_status']=='PENDING_CONFIRMATION'
    pending=result['pending_order']
    assert pending['quantity']==quantity
    assert pending['product_sku']==product.sku and pending['product_document_id']==product.document_id
    assert pending['quantity_evidence']['message']==f'Commande {count} Vis Synthétiques V015'
    assert product.stock==30  # resolver repository has no mutation method


@pytest.mark.parametrize('message', ['Commande une douzaine de vis','Commande une perceuse 18V'])
def test_original_generic_requests_remain_unresolved_even_with_one_candidate(message):
    repo=catalogue([item(),item('Perceuse Synthétique V015 18V','DEMO-DRILL-0001')])
    result=asyncio.run(ResolveProductUseCase(repo).execute(None,message))
    assert result.status==ResolutionStatus.NOT_FOUND and result.product is None


@pytest.mark.parametrize('message', ['Commande une douzaine de Vis Synthétiques V015 Pro',
    'Commande deux Vis Synthétiques V015 230V'])
def test_lexical_count_never_removes_an_unknown_product_discriminant(message):
    result=asyncio.run(ResolveProductUseCase(catalogue([item()])).execute('Vis Synthétiques V015',message))
    assert result.status==ResolutionStatus.NOT_FOUND


def test_duplicate_full_names_remain_ambiguous_after_lexical_count():
    result=asyncio.run(ResolveProductUseCase(catalogue([item(),item(sku='OTHER-001',id='507f1f77bcf86cd799439012')])).execute(None,
        'Commande une douzaine de Vis Synthétiques V015'))
    assert result.status==ResolutionStatus.AMBIGUOUS and result.product is None


@pytest.mark.parametrize('name',['Deux Vis Synthétiques V015','Une Vis Synthétique V015','Un Modèle V015'])
def test_quoted_names_and_words_after_count_keep_product_identity(name):
    repo=catalogue([item(name)])
    for message in [f'Commande "{name}"']:
        result=asyncio.run(ResolveProductUseCase(repo).execute(None,message))
        assert result.status==ResolutionStatus.RESOLVED and result.product.name==name


def test_sku_authority_not_weakened_by_lexical_quantity():
    repo=catalogue([item()]);result=asyncio.run(ResolveProductUseCase(repo).execute('Wrong hint',
        'Commande une douzaine de DEMO-SCREW-0001'))
    assert result.product.sku=='DEMO-SCREW-0001'
    repo.find_resolution_candidates.assert_not_awaited()


def test_observed_status_classification_is_propagated_without_inventing_order_authorization():
    llm=SimpleNamespace(classify_intent=AsyncMock(return_value=IntentResult(
        intent=Intent.ORDER_STATUS,order_id='DEMO-DRILL-0002')))
    result=asyncio.run(create_classify_node(ClassifyIntentUseCase(llm))({
        'message':'Commande DEMO-DRILL-0002','active_order_confirmation':{'operation_id':'obsolete'}}))
    assert result['intent']==Intent.ORDER_STATUS and result['order_id']=='DEMO-DRILL-0002'
    assert result['active_order_confirmation'] is None


@pytest.mark.parametrize('name',['Le Modèle V015','La Vis Synthétique V015','Reserve A V015'])
def test_article_in_full_product_name_after_lexical_count_is_not_stripped_twice(name):
    result=asyncio.run(ResolveProductUseCase(catalogue([item(name)])).execute(None,f'Commande deux {name}'))
    assert result.status==ResolutionStatus.RESOLVED and result.product.name==name

from test_mongo_transaction_integration import database


@pytest.mark.integration
@pytest.mark.parametrize('count,quantity',[('une douzaine de',12),('une demi-douzaine de',6),('deux',2)])
def test_real_mongo_lexical_name_preparation_keeps_identity_and_stock(database,count,quantity):
    from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
    doc=database.products.insert_one({'sku':'LEXICAL-SCREW-001','name':'Vis Synthétiques V015',
        'category':'OTHER','price':1.,'stock':30}).inserted_id
    before=list(database.products.find({}).sort('_id',1))
    prepare=PrepareOrderUseCase(SimpleNamespace(),ResolveProductUseCase(MongoProductRepository(database)))
    result=asyncio.run(create_order_node(prepare)({'message':f'Commande {count} Vis Synthétiques V015',
        'quantity':quantity,'customer_id':'synthetic'}))
    assert result['order_status']=='PENDING_CONFIRMATION'
    assert result['pending_order']['quantity']==quantity
    assert result['pending_order']['product_sku']=='LEXICAL-SCREW-001'
    assert result['pending_order']['product_document_id']==str(doc)
    assert database.orders.count_documents({})==0
    assert list(database.products.find({}).sort('_id',1))==before
