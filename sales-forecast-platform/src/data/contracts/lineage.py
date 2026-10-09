from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from .common import ContractModel


class LineageRecord(ContractModel):
    """Traceability from canonical data back to its source."""

    lineage_id: UUID
    source_id: UUID
    source_dataset: str = Field(min_length=1, max_length=512)
    source_record_id: str = Field(min_length=1, max_length=512)
    ingestion_batch_id: UUID
    adapter_name: str = Field(min_length=1, max_length=128)
    adapter_version: str = Field(min_length=1, max_length=64)
    mapping_version: str = Field(min_length=1, max_length=64)
    ingested_at: datetime
    transformed_at: datetime | None = None
