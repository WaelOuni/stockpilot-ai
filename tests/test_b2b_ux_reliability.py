"""Scoped contract tests; transactional/race guarantees remain in Mongo integration tests."""
import asyncio
import csv
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from stockpilot.application.services.tenant_csv import parse_tenant_csv
from stockpilot.application.services.tenant_assistant import TenantAssistant
from stockpilot.application.ports.tenant_orders import TenantStockInsufficient
from stockpilot.domain.entities.tenant_stock_operation import ImportMode
from stockpilot.presentation.api.b2b_boundary import failure
from stockpilot.presentation.api.routes.b2b_assistant import MessageIn


def content(attributes, mode=ImportMode.METADATA_ONLY):
    output = io.StringIO()
    headers = ["sku", "name", "description", "category", "price_minor", "currency"]
    values = ["DRILL-001", "Drill", "Description", "Tools", "1234", "EUR"]
    if mode == ImportMode.CREATE_ONLY:
        headers.append("quantity"); values.append("12")
    writer = csv.writer(output)
    writer.writerow(headers + ["attributes"])
    writer.writerow(values + [json.dumps(attributes)])
    return output.getvalue().encode()


@pytest.mark.parametrize("mode", [ImportMode.CREATE_ONLY, ImportMode.METADATA_ONLY])
@pytest.mark.parametrize("attributes", [[], ["Marque : Northline", "Puissance : 800 W"]])
def test_attributes_keep_existing_product_contract(mode, attributes):
    parsed = parse_tenant_csv(content(attributes, mode), mode)
    assert not parsed.issues
    assert parsed.rows[0]["values"]["attributes"] == attributes


@pytest.mark.parametrize("attributes", [["a"] * 33, ["a", "a"], ["x" * 129], [""], [" a"], ["a\nb"], [1], {}, None])
def test_invalid_attributes_never_produce_partial_plan(attributes):
    parsed = parse_tenant_csv(content(attributes), ImportMode.METADATA_ONLY)
    assert parsed.issues and not parsed.rows


@pytest.mark.parametrize("value", ["true", 1, None])
def test_consultation_mode_cannot_be_coerced(value):
    with pytest.raises(ValidationError):
        MessageIn(message_id="turn", message="question", read_only=value)


@pytest.mark.parametrize("intent", ["ORDER", "RAG", "STOCK_CHECK"])
def test_read_only_cannot_prepare_even_with_misleading_model_intent(intent):
    store, orders = Mock(), Mock()
    assistant = TenantAssistant(store, Mock(), orders, Mock(), Mock())
    reply = asyncio.run(assistant._answer(None, SimpleNamespace(), "conversation", {
        "question": "Je veux commander 2 unités du SKU DRILL-001",
        "read_only": True, "reply": {"classification": {"intent": intent}},
    }))
    assert reply["intent"] == "ORDER_REFUSED" and not reply["can_confirm"]
    store.plan.assert_not_called(); orders.prepare.assert_not_called()
    orders.confirm.assert_not_called(); orders.cancel.assert_not_called()


def test_consultation_still_answers_documentary_question():
    retrieval = SimpleNamespace(execute=AsyncMock(return_value="Document vérifié"))
    assistant = TenantAssistant(Mock(), Mock(), Mock(), retrieval, Mock())
    reply = asyncio.run(assistant._answer(None, SimpleNamespace(), "conversation", {
        "question": "Quels sont les délais de livraison ?", "read_only": True,
        "reply": {"classification": {"intent": "RAG"}},
    }))
    assert reply["text"] == "Document vérifié"
    assistant.orders.prepare.assert_not_called()


@pytest.mark.parametrize("available", [None, 8])
def test_stock_refusal_is_typed_without_private_quantities(available):
    result = failure(TenantStockInsufficient("DRILL-001", 12, available))
    assert result.status_code == 409
    body = json.loads(result.body)
    assert body == {"code": "STOCK_INSUFFICIENT", "stock": {"sku": "DRILL-001", "requested": 12, "available": available}}
