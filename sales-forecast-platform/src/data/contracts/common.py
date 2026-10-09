from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    """Base configuration shared by all enterprise data contracts."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        use_enum_values=False,
    )


class AssetType(str, Enum):
    STRUCTURED_DATASET = "structured_dataset"
    DOCUMENT_DATASET = "document_dataset"


class Frequency(str, Enum):
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    IRREGULAR = "irregular"
    CUSTOM = "custom"


class ValueStatus(str, Enum):
    ACTUAL = "actual"
    ADJUSTED = "adjusted"
    CORRECTED = "corrected"
    ESTIMATED = "estimated"
    IMPUTED = "imputed"


class QualityStatus(str, Enum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class QualityDisposition(str, Enum):
    """Outcome of data-quality processing, independent of model gating policy."""

    ACCEPTED = "accepted"
    ACCEPTED_WITH_WARNINGS = "accepted_with_warnings"
    PARTIALLY_REPAIRED = "partially_repaired"
    QUARANTINED = "quarantined"
    REJECTED = "rejected"


class DataFormat(str, Enum):
    CSV = "csv"
    JSON = "json"
    PARQUET = "parquet"
    EXCEL = "excel"
    DATABASE = "database"
    API = "api"
    ERP = "erp"
    PDF = "pdf"
    IMAGE = "image"
    DOCX = "docx"
    OTHER = "other"


class MultilingualMetadata(ContractModel):
    """Language metadata without assuming English as the default."""

    language: str | None = Field(default=None, min_length=2, max_length=35)
    languages: list[str] = Field(default_factory=list)
    script: str | None = Field(default=None, min_length=2, max_length=32)
    language_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @property
    def is_multilingual(self) -> bool:
        return len(set(self.languages)) > 1


class AssetMetadata(ContractModel):
    """Metadata common to all data assets."""

    asset_id: UUID
    organization_id: UUID
    source_id: UUID
    contract_version: str = Field(default="1.0", min_length=1, max_length=32)
    ingestion_batch_id: UUID
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime | None = None


class Measurement(ContractModel):
    """Semantic measurement information for numeric observations."""

    metric: str = Field(min_length=1, max_length=128)
    unit: str = Field(min_length=1, max_length=64)
    currency: str | None = Field(default=None, min_length=3, max_length=3)

    def validate_value(self, value: Decimal | float | int) -> None:
        # Deliberately lightweight: domain-specific rules belong to business validation.
        if value is None:
            raise ValueError("Measurement values cannot be None.")
