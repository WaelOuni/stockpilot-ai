"""Pure validation: these tests do not claim storage/transaction isolation."""

from dataclasses import FrozenInstanceError

import pytest

from stockpilot.domain.entities.tenant_order import (
    CancelTenantOrder,
    ConfirmTenantOrder,
    PrepareTenantOrder,
    RequestedLine,
)


@pytest.mark.parametrize("quantity", [0, -1, 1.1, 1.0, True, False, "1", None, 1000001])
def test_strict_quantity(quantity):
    with pytest.raises(ValueError):
        RequestedLine("DRILL-001", quantity)


@pytest.mark.parametrize("value", ["", " ", " x", "x ", None, {}, "a" * 129])
@pytest.mark.parametrize("field", ["sku", "customer", "operation", "order"])
def test_scalar_identifiers(value, field):
    with pytest.raises((ValueError, TypeError)):
        if field == "sku":
            RequestedLine(value, 1)
        elif field == "customer":
            PrepareTenantOrder("op", value, (RequestedLine("DRILL-001", 1),))
        elif field == "operation":
            PrepareTenantOrder(value, "buyer", (RequestedLine("DRILL-001", 1),))
        else:
            CancelTenantOrder("op", value)


@pytest.mark.parametrize("confirmed", [False, None, 1, "true", {}, []])
def test_explicit_confirmation(confirmed):
    with pytest.raises(ValueError):
        ConfirmTenantOrder("confirm", "order", "digest", confirmed)


@pytest.mark.parametrize(
    "lines",
    [
        (),
        [],
        ("sku",),
        (RequestedLine("SKU", 1),) * 2,
        tuple(RequestedLine(str(n), 1) for n in range(21)),
    ],
)
def test_typed_bounded_unique_lines(lines):
    with pytest.raises((ValueError, TypeError)):
        PrepareTenantOrder("op", "buyer", lines)


def test_immutable_request_and_no_identity_inference():
    line = RequestedLine("DRILL-001", 1)
    command = PrepareTenantOrder("op", "business-buyer", (line,))
    with pytest.raises(FrozenInstanceError):
        command.customer_id = "org"
    assert not hasattr(command, "organization_id")
    assert not hasattr(command, "created_by")
