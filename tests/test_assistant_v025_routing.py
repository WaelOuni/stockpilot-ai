"""Reported natural queries must remain reads even with a hostile/offline model."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from stockpilot.application.services.tenant_assistant import TenantAssistant
from stockpilot.application.services.tenant_assistant_catalogue import TenantAssistantCatalogue
from stockpilot.application.services.tenant_catalogue_discovery import is_low_stock_request, low_stock_criteria


LOW = "je voulais savoir le stock de produit qui bientot épuisé"
PRICE = "je cherche une perceuse disponible avec un prix moins de 100 euros"


def test_reported_low_stock_is_a_list_with_default_strict_threshold():
    assert is_low_stock_request(LOW)
    criteria = low_stock_criteria(LOW, ("perceuses", "scies"))
    assert (criteria.minimum, criteria.maximum, criteria.categories) == (0, 4, ())


def test_reported_budget_is_a_list_with_exact_strict_eur_cap():
    criteria = TenantAssistantCatalogue.criteria(PRICE, None)
    assert criteria.reference is None and criteria.mode == "search"
    assert criteria.category.value == "DRILL"
    assert criteria.available is True
    assert criteria.maximum == 100 and criteria.exclusive_maximum is True


@pytest.mark.parametrize("question", [LOW, PRICE])
@pytest.mark.parametrize("fault", [TimeoutError(), ValueError("malformed")])
def test_explicit_discovery_bypasses_failed_classifier(question, fault):
    llm = SimpleNamespace(classify_intent=AsyncMock(side_effect=fault))
    orders = Mock()
    service = TenantAssistant(Mock(), Mock(), orders, Mock(), llm)
    service._answer = AsyncMock(return_value=service._reply("TEST", "verified"))
    result = asyncio.run(service._graph(object(), object(), "conversation", None).ainvoke({"question": question}))
    assert result["reply"]["timing_ms"]["classification"] == 0
    llm.classify_intent.assert_not_awaited()
    assert orders.mock_calls == []
