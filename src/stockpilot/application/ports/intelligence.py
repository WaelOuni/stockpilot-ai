"""Read-only, tenant-scoped analytic facts. No command methods."""
from typing import Protocol
from datetime import date
from stockpilot.domain.entities.intelligence import IntelligenceQuery


class IntelligencePort(Protocol):
    def snapshot(self, request, context, query: IntelligenceQuery, as_of: date) -> dict: ...
