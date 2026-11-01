"""Natural-language references are not necessarily valid exact SKU keys."""

import asyncio
from unittest.mock import MagicMock, Mock

import pytest

from stockpilot.application.services.tenant_assistant_catalogue import TenantAssistantCatalogue
from stockpilot.application.use_cases.catalogue_advice import ClarificationRequired, extract_criteria
from stockpilot.infrastructure.persistence.mongodb.tenant_catalogue import MongoTenantCatalogue


FREE_TEXT = (
    "Quels produits de qualification v0.2.0 sont disponibles dans mon catalogue ? "
    "Donne leur prix et leur stock, sans préparer de commande."
)


@pytest.mark.parametrize("reference", [
    extract_criteria(FREE_TEXT).reference,
    "SKU " + "X" * 129,
    "mot " * 100,
])
def test_non_identifier_reference_requests_clarification(monkeypatch, reference):
    # Exercise the actual Mongo SKU validator, without connecting to any database.
    catalogue = MongoTenantCatalogue(MagicMock(), Mock())
    monkeypatch.setattr(catalogue, "_scope", Mock(return_value="org-a"))
    monkeypatch.setattr(catalogue, "resolution_candidates", Mock(return_value=()))
    service = TenantAssistantCatalogue(catalogue)
    with pytest.raises(ClarificationRequired, match="introuvable"):
        asyncio.run(service.resolve(object(), object(), reference))


def test_valid_sku_lookup_errors_are_not_converted_to_clarification():
    catalogue = Mock()
    catalogue.by_sku.side_effect = ValueError("unexpected catalogue invariant")
    with pytest.raises(ValueError, match="unexpected catalogue invariant"):
        asyncio.run(TenantAssistantCatalogue(catalogue).resolve(object(), object(), "DRILL-001"))


@pytest.mark.parametrize("length", [128, 129])
def test_exact_sku_limit_is_preserved_and_never_truncated(length):
    product = Mock(sku="X" * 128, status="active", product_id="id", organization_id="org-a")
    catalogue = Mock()
    catalogue.by_sku.return_value = product
    service = TenantAssistantCatalogue(catalogue)
    request, context = object(), Mock(organization_id="org-a")
    if length == 128:
        assert asyncio.run(service.resolve(request, context, "SKU " + "X" * length)) is product
        assert all(call.args[2] == product.sku for call in catalogue.by_sku.call_args_list)
    else:
        with pytest.raises(ClarificationRequired):
            asyncio.run(service.resolve(request, context, "SKU " + "X" * length))
        catalogue.by_sku.assert_not_called()
    catalogue.resolution_candidates.assert_not_called()


def test_long_name_is_resolved_without_truncation():
    name = "Perceuse professionnelle " + "qualification " * 9
    name = name.strip()
    assert 128 < len(name) <= 256
    product = Mock(sku="LONG-001", status="active", product_id="id", organization_id="org-a")
    product.name = name
    catalogue = Mock()
    catalogue.by_sku.return_value = product
    catalogue.resolution_candidates.return_value = (product,)
    request, context = object(), Mock(organization_id="org-a")
    assert asyncio.run(TenantAssistantCatalogue(catalogue).resolve(request, context, name)) is product
    catalogue.resolution_candidates.assert_called_once_with(request, context, name, exact=True, limit=2)
    catalogue.by_sku.assert_called_once_with(request, context, "LONG-001")
