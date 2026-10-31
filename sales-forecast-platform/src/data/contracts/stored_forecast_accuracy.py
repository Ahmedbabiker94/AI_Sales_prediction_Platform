"""Read-only evaluation declarations. None establish authentication or write authority."""
from decimal import Decimal, InvalidOperation
import hashlib
import math
from typing import Literal
from uuid import UUID
from pydantic import Field, StrictStr, model_validator
from src.data.contracts.forecast_evidence_storage import (
    StorageContract, ForecastStorageScope, Digest, Timestamp, ForecastLogicalKey,
)


class LabelTarget(StorageContract):
    series_id: UUID
    target_period: Timestamp
    target_period_end: Timestamp
    target_metric: StrictStr
    measurement_json: StrictStr = Field(max_length=4096)
    temporal_json: StrictStr = Field(max_length=4096)
    transformation: Literal['identity_target_v1'] = 'identity_target_v1'

    @classmethod
    def from_forecast(cls, key: ForecastLogicalKey):
        return cls(**{name: getattr(key, name) for name in cls.model_fields})


class ActualLabel(StorageContract):
    target: LabelTarget
    # Canonical finite decimal text is authoritative for synthetic labels; binary64
    # conversion is explicit evaluation arithmetic, never a forecast re-encoding.
    decimal_value: StrictStr = Field(min_length=1, max_length=160)
    available_at: Timestamp

    @model_validator(mode='after')
    def finite(self):
        try:
            value = Decimal(self.decimal_value)
        except InvalidOperation:
            raise ValueError('finite bounded decimal required') from None
        if (not value.is_finite() or len(value.as_tuple().digits) > 128
                or abs(value.as_tuple().exponent) > 300 or not math.isfinite(float(value))
                or str(value) != self.decimal_value):
            raise ValueError('canonical finite bounded decimal required')
        return self


class SyntheticLabelSnapshot(StorageContract):
    version: Literal['1.0'] = '1.0'
    classification: Literal['SYNTHETIC_CONTROLLED_OFFLINE_LABELS_ONLY'] = 'SYNTHETIC_CONTROLLED_OFFLINE_LABELS_ONLY'
    scope: ForecastStorageScope
    revision_id: Digest
    published_at: Timestamp
    effective_at: Timestamp
    interval_start: Timestamp
    interval_end: Timestamp
    # Incomplete snapshots are allowed only to represent LABELS_NOT_READY.
    labels: tuple[ActualLabel, ...] = Field(max_length=32)
    sign_policy: Literal['SIGNED_FINITE_ACTUALS_V1'] = 'SIGNED_FINITE_ACTUALS_V1'

    @model_validator(mode='after')
    def coherent(self):
        if self.interval_start >= self.interval_end:
            raise ValueError('exact nonempty target interval required')
        series = tuple(row.target.series_id for row in self.labels)
        if len(set(series)) != len(series) or not set(series) <= set(self.scope.series_ids):
            raise ValueError('unique authorized series only')
        for row in self.labels:
            if (row.target.target_period != self.interval_start
                    or row.target.target_period_end != self.interval_end
                    or row.available_at < self.published_at):
                raise ValueError('label target interval/publication mismatch')
        return self

    @property
    def content_sha256(self):
        return hashlib.sha256(self.canonical_bytes).hexdigest()


class ExactLabelRead(StorageContract):
    version: Literal['1.0'] = '1.0'
    purpose: Literal['PINNED_ACTUAL_LABEL_READ'] = 'PINNED_ACTUAL_LABEL_READ'
    scope: ForecastStorageScope
    revision_id: Digest
    expected_snapshot_sha256: Digest
    interval_start: Timestamp
    interval_end: Timestamp
    evaluation_as_of: Timestamp
    availability_policy: Literal['PUBLISHED_EFFECTIVE_AND_AVAILABLE_BY_AS_OF_V1'] = 'PUBLISHED_EFFECTIVE_AND_AVAILABLE_BY_AS_OF_V1'

    @model_validator(mode='after')
    def interval(self):
        if self.interval_start >= self.interval_end:
            raise ValueError('exact nonempty target interval required')
        return self


class AccuracyPair(StorageContract):
    ordinal: int = Field(ge=0, le=31, strict=True)
    logical_key_id: Digest
    forecast_row_sha256: Digest
    prediction_float32_le: StrictStr = Field(pattern=r'^[0-9a-f]{8}$')
    label: ActualLabel


class AccuracyEvidence(StorageContract):
    version: Literal['1.0'] = '1.0'
    classification: Literal['READ_ONLY_EVALUATION_NO_WRITE_OR_EXECUTION_AUTHORITY'] = 'READ_ONLY_EVALUATION_NO_WRITE_OR_EXECUTION_AUTHORITY'
    forecast_selection_json: StrictStr = Field(max_length=8192)
    forecast_receipt_json: StrictStr = Field(max_length=16384)
    # Exact immutable batch (including original origin/cutoff/publication/lineage),
    # retained once, never repeated inside each metric/pair.
    forecast_batch_json: StrictStr = Field(max_length=512*1024)
    label_selection: ExactLabelRead
    label_snapshot_sha256: Digest
    label_snapshot_json: StrictStr = Field(max_length=512*1024)
    pairs: tuple[AccuracyPair, ...] = Field(min_length=1, max_length=32)
    population_size: int = Field(ge=1, le=32, strict=True)
    matched_count: int = Field(ge=1, le=32, strict=True)
    missing_count: Literal[0] = 0
    ineligible_count: Literal[0] = 0
    arithmetic: Literal['BINARY64_SCALED_FSUM_V1'] = 'BINARY64_SCALED_FSUM_V1'
    error_sign: Literal['PREDICTION_MINUS_ACTUAL'] = 'PREDICTION_MINUS_ACTUAL'
    missing_policy: Literal['COMPLETE_COVERAGE_REQUIRED'] = 'COMPLETE_COVERAGE_REQUIRED'
    zero_policy: Literal['ZERO_ALLOWED_NO_PERCENTAGE_METRIC'] = 'ZERO_ALLOWED_NO_PERCENTAGE_METRIC'
    mae: float = Field(ge=0, allow_inf_nan=False, strict=True)
    rmse: float = Field(ge=0, allow_inf_nan=False, strict=True)
    bias: float = Field(allow_inf_nan=False, strict=True)

    @model_validator(mode='after')
    def complete(self):
        if self.matched_count != self.population_size or len(self.pairs) != self.matched_count:
            raise ValueError('complete evaluated coverage required')
        if tuple(p.ordinal for p in self.pairs) != tuple(range(len(self.pairs))):
            raise ValueError('original complete ordering required')
        if len(self.canonical_bytes) > 512*1024:
            raise ValueError('bounded evaluation encoding required')
        return self

    @property
    def content_sha256(self):
        return hashlib.sha256(self.canonical_bytes).hexdigest()


class AccuracyOutcome(StorageContract):
    version: Literal['1.0'] = '1.0'
    status: Literal['VERIFIED_EVALUATION', 'DENIED', 'LABELS_NOT_READY', 'INCOMPATIBLE_SCOPE', 'UNAVAILABLE']
    evidence: AccuracyEvidence | None = None

    @model_validator(mode='after')
    def result(self):
        if (self.status == 'VERIFIED_EVALUATION') != (self.evidence is not None):
            raise ValueError('evidence only on verified evaluation')
        return self
