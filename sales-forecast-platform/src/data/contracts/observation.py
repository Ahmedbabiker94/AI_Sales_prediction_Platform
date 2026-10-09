from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field, model_validator

from .common import ContractModel, Frequency, ValueStatus


class Observation(ContractModel):
    """Canonical time-series observation.

    This is a contract for one logical observation. Bulk ingestion should use
    an efficient tabular representation (for example Arrow/DataFrame) while
    preserving these field semantics.
    """

    series_id: UUID
    period_start: datetime
    period_end: datetime
    frequency: Frequency
    metric: str = Field(min_length=1, max_length=128)
    value: Decimal | float | int
    unit: str = Field(min_length=1, max_length=64)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    value_status: ValueStatus = ValueStatus.ACTUAL
    source_record_id: str | None = Field(default=None, max_length=512)
    lineage_id: UUID | None = None

    @model_validator(mode="after")
    def validate_period(self) -> "Observation":
        if self.period_end <= self.period_start:
            raise ValueError("period_end must be later than period_start.")
        return self
