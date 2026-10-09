from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from .common import ContractModel, DataFormat, MultilingualMetadata


class DocumentMetadata(ContractModel):
    document_id: UUID
    filename: str = Field(min_length=1, max_length=1024)
    media_type: str = Field(min_length=1, max_length=255)
    source_format: DataFormat
    page_count: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, max_length=2048)
    language: MultilingualMetadata = Field(default_factory=MultilingualMetadata)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentElement(ContractModel):
    """Canonical element emitted by native parsing or OCR."""

    element_id: UUID
    document_id: UUID
    element_type: str = Field(min_length=1, max_length=64)
    page_number: int | None = Field(default=None, ge=1)
    text: str = ""
    language: MultilingualMetadata = Field(default_factory=MultilingualMetadata)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    bounding_box: list[float] | None = None
    parent_element_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_bbox(self) -> "DocumentElement":
        if self.bounding_box is not None and len(self.bounding_box) not in (4, 8):
            raise ValueError("bounding_box must contain 4 or 8 numeric values.")
        return self


class DocumentDatasetContent(ContractModel):
    documents: list[DocumentMetadata] = Field(default_factory=list)
    elements: list[DocumentElement] = Field(default_factory=list)
    processed_at: datetime | None = None
