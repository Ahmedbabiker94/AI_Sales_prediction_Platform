from __future__ import annotations

from datetime import datetime
from typing import Any, Annotated
from uuid import UUID

from pydantic import Field

from .common import AssetMetadata, AssetType, ContractModel, DataFormat, Frequency
from .document import DocumentDatasetContent
from .forecast import ForecastConfig
from .lineage import LineageRecord
from .observation import Observation
from .quality import DataQualityReport
from .series import SeriesDefinition


class DataAsset(ContractModel):
    """Stable root abstraction for all ingested enterprise data."""

    asset: AssetMetadata
    asset_type: AssetType
    name: str = Field(min_length=1, max_length=512)
    source_format: DataFormat
    input_frequency: Frequency | None = None
    canonical_frequency: Frequency | None = None
    calendar_id: str | None = Field(default=None, max_length=128)
    lineage: list[LineageRecord] = Field(default_factory=list)
    quality: DataQualityReport | None = None


class StructuredDataset(DataAsset):
    """Canonical structured dataset used by downstream business/ML layers."""

    asset_type: Annotated[AssetType, Field(default=AssetType.STRUCTURED_DATASET)] = (
        AssetType.STRUCTURED_DATASET
    )
    metric_semantics: list[str] = Field(default_factory=list)
    series: list[SeriesDefinition] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    forecast_config: ForecastConfig | None = None


class DocumentDataset(DataAsset):
    """Canonical document dataset for document/OCR processing."""

    asset_type: Annotated[AssetType, Field(default=AssetType.DOCUMENT_DATASET)] = (
        AssetType.DOCUMENT_DATASET
    )
    content: DocumentDatasetContent = Field(default_factory=DocumentDatasetContent)


DataAssetLike = StructuredDataset | DocumentDataset
