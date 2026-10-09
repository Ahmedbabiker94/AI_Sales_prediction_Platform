from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from pydantic import Field

from .common import ContractModel


class SeriesDefinition(ContractModel):
    """Definition of one forecastable/business time series."""

    series_id: UUID
    series_type: str = Field(default="sales", min_length=1, max_length=128)
    dimensions: dict[str, str] = Field(default_factory=dict)
    hierarchy: dict[str, str] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)
    active_from: date | None = None
    active_to: date | None = None

    @property
    def is_active(self) -> bool:
        return self.active_to is None
