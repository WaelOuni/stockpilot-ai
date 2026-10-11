from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, field_validator, model_validator

from stockpilot.domain.entities.stock_movement import StockMovement


OperationId = Annotated[
    str,
    StringConstraints(
        strict=True,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    ),
]
Reason = Annotated[str, StringConstraints(strict=True)]
Delta = Annotated[StrictInt, Field(ge=-1_000_000, le=1_000_000)]


class StockAdjustmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: OperationId
    delta: Delta
    reason: Reason

    @field_validator("reason", mode="before")
    @classmethod
    def trim_reason(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        value = value.strip()
        if not value or len(value) > 500:
            raise ValueError("Reason must be 1 to 500 trimmed characters.")
        return value

    @model_validator(mode="after")
    def validate_delta_and_id(self):
        if self.delta == 0:
            raise ValueError("Delta must not be zero.")
        if str(UUID(self.operation_id)) != self.operation_id:
            raise ValueError("Operation ID must be a canonical UUID.")
        return self


class StockMovementResponse(BaseModel):
    movement_id: str
    operation_id: str
    product_sku: str
    delta: int
    stock_before: int
    stock_after: int
    reason: str
    occurred_at: datetime
    source: str

    @classmethod
    def from_movement(cls, movement: StockMovement):
        return cls(
            movement_id=movement.movement_id,
            operation_id=movement.operation_id,
            product_sku=movement.product_sku,
            delta=movement.delta,
            stock_before=movement.stock_before,
            stock_after=movement.stock_after,
            reason=movement.reason,
            occurred_at=movement.occurred_at,
            source=movement.source,
        )


class StockMovementsResponse(BaseModel):
    items: list[StockMovementResponse]
    limit: int
    offset: int
    count: int
    total: int | None = None
    has_more: bool
