"""V12.24: safe re-preparation, lexical counts and whole numeric tokens."""
import asyncio
from copy import deepcopy
import pytest
from stockpilot.application.order_quantity_evidence import concordant_quantity, inspect_quantity
from stockpilot.application.order_quantity_errors import InvalidOrderQuantityError
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.domain.enums.intent import Intent
from test_order_conversation import make_workflow, invoke


@pytest.mark.parametrize('text,quantity', [
    ('une perceuse',1), ('un outil',1), ('deux outils',2),
    ('une douzaine de vis',12), ('douzaine de vis',12),
    ('une demi-douzaine de vis',6), ('demi-douzaine de vis',6),
    ('3 câbles de 2 mètres',3), ('4 vis de 20 mm',4),
    ('2 perceuses 18V',2), ('la Bosch 18V',1),
    ('2 DEMO-18-230-0002',2), ('1-DRILL-0002',1),
    ('2, DEMO-DRILL-0002',2), ('2. DEMO-DRILL-0002',2),
    ('2\nDEMO-DRILL-0002',2),
])
def test_lexical_count_or_measure_keeps_exact_value(text, quantity):
    evidence = concordant_quantity('Commande '+text,quantity)
    assert evidence.quantity == quantity
    if evidence.start is not None:
        assert evidence.message[evidence.start:evidence.end]


@pytest.mark.parametrize('text', ['une douzaine de vis', 'une demi-douzaine de vis','deux outils'])
def test_lexical_extraction_cannot_replace_explicit_count_with_one(text):
    with pytest.raises(InvalidOrderQuantityError):
        concordant_quantity('Commande '+text,1)


@pytest.mark.parametrize('text', ['une paire de gants','un lot de vis', '2 lots de vis',
    'un couple de gants','une dizaine de vis','zéro vis','1.5abc DEMO-DRILL-0002',
    '1,5abc DEMO-DRILL-0002','2.75unknown DEMO-DRILL-0002', '1.5-DEMO-DRILL-0002',
    '2.. DEMO-DRILL-0002','1e2abc DEMO-DRILL-0002','la Scie Série 20',
    'une.douzaine de vis','une(paire) de gants'])
def test_uncertain_counts_never_default_to_one(text):
    with pytest.raises(InvalidOrderQuantityError):
        concordant_quantity('Commande '+text,1)


@pytest.mark.parametrize('message', ['plutôt 1.5 ou 2','plutôt 2 ou 3','plutôt douze',
    'plutôt vingt','plutôt onze','plutôt quelques','plutôt une paire',
    'plutôt 1.5abc','Je veux 10 pommes','plutôt pas 3 mais 2',
    'plutôt entre 2 et 3','plutôt seulement 2','plutôt pas trois mais deux','plutôt pas .5abc', 'plutôt seulement 2abc'])
def test_modification_disarms_then_requires_new_valid_operation(message):
    workflow,classifier,_,repository,products,reservations,_=make_workflow()
    prepared=invoke(workflow,'Commande-moi 3 Bosch X100','change')
    before=deepcopy(products)
    classifier.execute.side_effect=None
    classifier.execute.return_value=IntentResult(intent=Intent.UNKNOWN)
    rejected=invoke(workflow,message,'change')
    assert rejected['pending_order'] is None and rejected['order_status']=='INVALID_QUANTITY'
    assert rejected['quantity_validation_failed'] is True
    assert 'quantité' in rejected['response']
    assert invoke(workflow,'oui','change')['pending_order'] is None
    assert not repository.orders and not reservations and products==before
    classifier.execute.return_value=IntentResult(intent=Intent.ORDER,product_name='Bosch X100',quantity=2)
    fresh=invoke(workflow,'Commande-moi 2 Bosch X100','change')
    assert fresh['pending_order']['operation_id'] != prepared['pending_order']['operation_id']
    assert fresh['quantity_validation_failed'] is False
    confirmed=invoke(workflow,'oui confirme','change')
    replay=invoke(workflow,'oui confirme','change')
    assert confirmed['created_order_id']==replay['created_order_id']
    assert reservations==[('Bosch X100',2)] and len(repository.orders)==1


def test_finished_order_survives_ambiguous_followup_and_remains_readonly_replayable():
    workflow,classifier,_,repository,products,reservations,_=make_workflow()
    invoke(workflow,'Commande-moi 3 Bosch X100','finished')
    confirmed=invoke(workflow,'oui confirme','finished')
    before=deepcopy(products)
    classifier.execute.side_effect=None;classifier.execute.return_value=IntentResult(intent=Intent.UNKNOWN)
    after=invoke(workflow,'plutôt 1.5 ou 2','finished')
    assert after['pending_order']['status']=='CONFIRMED'
    replay=invoke(workflow,'oui confirme','finished')
    assert replay['created_order_id']==confirmed['created_order_id']
    assert products==before and len(repository.orders)==1 and reservations==[('Bosch X100',3)]


@pytest.mark.parametrize('interruption', [Intent.UNKNOWN,Intent.STOCK_CHECK,Intent.PRODUCT_ADVICE])
@pytest.mark.parametrize('change', ['plutôt 1.5 ou 2','plutôt 2','plutôt douze'])
def test_intervening_intent_cannot_leave_obsolete_preparation_armed(interruption,change):
    workflow,classifier,_,repository,products,reservations,_=make_workflow()
    invoke(workflow,'Commande-moi 3 Bosch X100','interrupt')
    # Restore an interrupted checkpoint without invoking irrelevant providers.
    config={'configurable':{'thread_id':'interrupt'}}
    asyncio.run(workflow.aupdate_state(config,{'last_relevant_intent':interruption}))
    classifier.execute.side_effect=None;classifier.execute.return_value=IntentResult(intent=Intent.UNKNOWN)
    before=deepcopy(products)
    rejected=invoke(workflow,change,'interrupt')
    assert rejected['pending_order'] is None and rejected['quantity_validation_failed'] is True
    assert invoke(workflow,'oui confirme','interrupt')['pending_order'] is None
    assert products==before and not repository.orders and not reservations


@pytest.mark.parametrize('text,extracted', [
    ('exactement une douzaine de vis',1), ('environ deux perceuses',1),
    ('en tout une paire de gants',1), ('un trio de câbles',1),
    ('deux plus trois DEMO-DRILL-0002',2), ('deux voire trois DEMO-DRILL-0002',2),
    ('deux-mille DEMO-DRILL-0002',1), ('trois-cent DEMO-DRILL-0002',1),
    ('deux-et-demi DEMO-DRILL-0002',1), ('une poignée de vis',1), ('un paquet de vis',1), ('une boîte de vis',1),
])
def test_bounded_quantifier_modifiers_and_compounds_clarify(text,extracted):
    with pytest.raises(InvalidOrderQuantityError):
        concordant_quantity('Commande '+text,extracted)


@pytest.mark.parametrize('text', [
    '½ DEMO-DRILL-0002', '.5abc DEMO-DRILL-0002', '.5-DEMO-DRILL-0002',
    ',5abc DEMO-DRILL-0002', '-.5abc DEMO-DRILL-0002',
    'une, douzaine de vis', 'une grosse douzaine de vis',
    'DEMO-DRILL-0002 en quantité de deux', 'DEMO-DRILL-0002 par douzaine',
    'DEMO-DRILL-0002 en lot de deux', 'DEMO-DRILL-0002 environ deux unités',
])
def test_malformed_or_repositioned_quantity_never_becomes_one(text):
    with pytest.raises(InvalidOrderQuantityError):
        concordant_quantity('Commande '+text,1)


@pytest.mark.parametrize('message',['plutôt Bosch 18V','plutôt Makita D200','plutôt DEMO-18-230-0002','plutôt 1-DRILL-0002','plutôt câble de 2 mètres'])
def test_continuation_detector_does_not_harvest_product_specs(message):
    from stockpilot.application.order_quantity_evidence import quantitative_continuation
    assert quantitative_continuation(message) is None


@pytest.mark.parametrize('text',['une½ DEMO-DRILL-0002','perceuse ½ pouce DEMO-DRILL-0002'])
def test_unclassified_fraction_glyph_requires_clarification(text):
    with pytest.raises(InvalidOrderQuantityError):
        concordant_quantity('Commande '+text,1)


@pytest.mark.parametrize('source,start,end',[
    ('Commande 1.5abc DEMO-DRILL-0002',9,10),
    ('Commande une douzaine de DEMO-DRILL-0002',9,12),
])
def test_old_partial_quantity_proof_is_invalidated_before_finalizer(source,start,end):
    from stockpilot.application.workflows.nodes import create_order_confirmation_node
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    workflow,_,_,_,_,_,_=make_workflow()
    pending=invoke(workflow,'Commande-moi Bosch X100','old-proof')
    pending['pending_order']['quantity_evidence']={
        'message':source,'start':start,'end':end,'quantity':1,'kind':'explicit'}
    finalizer=SimpleNamespace(execute=AsyncMock())
    result=asyncio.run(create_order_confirmation_node(finalizer)({**pending,'message':'oui'}))
    assert result['pending_order'] is None and result['order_status']=='INVALID_QUANTITY'
    finalizer.execute.assert_not_awaited()


@pytest.mark.parametrize('text',[
    'seulement deux DEMO-DRILL-0002','pas trois mais deux DEMO-DRILL-0002',
    'DEMO-DRILL-0002 deux unités','DEMO-DRILL-0002 une unité',
])
def test_residual_lexical_counts_cannot_be_claimed_absent(text):
    with pytest.raises(InvalidOrderQuantityError):
        concordant_quantity('Commande '+text,1)


@pytest.mark.parametrize('intent',[Intent.STOCK_CHECK,Intent.PRODUCT_ADVICE])
def test_conflicting_complete_order_intent_cannot_leave_old_pending(intent):
    workflow,classifier,_,repository,products,reservations,_=make_workflow()
    invoke(workflow,'Commande-moi 3 Bosch X100','conflict')
    before=deepcopy(products)
    classifier.execute.side_effect=None
    classifier.execute.return_value=IntentResult(intent=intent,product_name='Bosch X100',quantity=2)
    result=invoke(workflow,'Commande-moi 2 Bosch X100','conflict')
    assert result['pending_order'] is None and result['quantity_validation_failed'] is True
    assert not repository.orders and not reservations and products==before


def test_quantity_clarification_clears_status_card_without_losing_confirmed_order():
    workflow,classifier,_,repository,products,reservations,_=make_workflow()
    invoke(workflow,'Commande-moi 3 Bosch X100','pending')
    invoke(workflow,'Commande-moi Makita D200','other')
    confirmed=invoke(workflow,'oui confirme','other')
    queried=invoke(workflow,f"Statut commande {confirmed['created_order_id']}",'pending')
    assert queried['queried_order_id']==confirmed['created_order_id']
    before=deepcopy(products);before_reservations=deepcopy(reservations)
    classifier.execute.side_effect=None;classifier.execute.return_value=IntentResult(intent=Intent.UNKNOWN)
    rejected=invoke(workflow,'plutôt 1.5 ou 2','pending')
    assert rejected['pending_order'] is None
    assert rejected['queried_order_id'] is None and rejected['order_status_result'] is None
    assert invoke(workflow,'oui confirme','pending')['pending_order'] is None
    assert products==before and reservations==before_reservations and len(repository.orders)==1
