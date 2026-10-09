from __future__ import annotations

from pydantic import Field

from .common import ContractModel, QualityDisposition, QualityStatus


class DataQualityReport(ContractModel):
    """Quality result produced during ingestion/processing.

    This contract records what happened to the data; it does not itself decide
    whether the forecasting pipeline must stop. That decision belongs to a
    later, explicit quality-policy/gating layer.
    """

    status: QualityStatus
    disposition: QualityDisposition
    quality_score: float = Field(ge=0.0, le=1.0)

    row_count: int = Field(default=0, ge=0)
    missing_count: int = Field(default=0, ge=0)
    duplicate_count: int = Field(default=0, ge=0)
    invalid_count: int = Field(default=0, ge=0)
    anomaly_count: int = Field(default=0, ge=0)

    repaired_count: int = Field(default=0, ge=0)
    imputed_count: int = Field(default=0, ge=0)
    dropped_count: int = Field(default=0, ge=0)
    quarantined_count: int = Field(default=0, ge=0)

    processing_operations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
