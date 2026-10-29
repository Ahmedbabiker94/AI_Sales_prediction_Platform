"""Adapter-independent storage contents, never producer or write authority.

The accepted logical-key encoding is preserved. B4 must separately project and
authorize original live producer/write-grant evidence; no such bridge exists here.
"""
from datetime import datetime
import hashlib
import json
import math
import struct
from typing import Annotated, Literal
from uuid import UUID

import pandas as pd
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictInt, StrictStr, model_validator
from src.data.contracts.dataset import operational_json
from src.data.contracts.features import TemporalFeatureRules
from src.data.contracts.mapping import _DeclaredMeasurement

Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


def closed_json(text, limit=512*1024):
    def pairs(values):
        out = {}
        for key, value in values:
            if key in out:
                raise ValueError('duplicate JSON field')
            out[key] = value
        return out
    def invalid(_):
        raise ValueError('nonfinite JSON constant')
    if type(text) is not str or len(text.encode('utf-8')) > limit:
        raise ValueError('bounded JSON required')
    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


def _time(value):
    if type(value) is str:
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('explicit aware timestamp required')
    return value


Timestamp = Annotated[datetime, BeforeValidator(_time)]


def _integer(value):
    if type(value) is not int:
        raise ValueError('exact integer required')
    return value


One = Annotated[Literal[1], BeforeValidator(_integer)]
Zero = Annotated[Literal[0], BeforeValidator(_integer)]


class StorageContract(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra='forbid', revalidate_instances='always', hide_input_in_errors=True)

    @property
    def canonical_json(self):
        return operational_json(self.model_dump(mode='json', warnings='error'))

    @property
    def canonical_bytes(self):
        return self.canonical_json.encode('utf-8')

    @classmethod
    def model_validate_json(cls, text, **kwargs):
        if any(kwargs.get(k) is not None for k in ('strict', 'extra')):
            raise ValueError('validation overrides unavailable')
        closed_json(text)
        return super().model_validate_json(text, **kwargs)


class ForecastStorageScope(StorageContract):
    organization_id: UUID
    dataset_id: UUID
    series_ids: tuple[UUID, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode='after')
    def ordered(self):
        if self.series_ids != tuple(sorted(set(self.series_ids))):
            raise ValueError('complete unique ordered series scope required')
        return self


class ForecastLogicalKey(StorageContract):
    organization_id: UUID
    dataset_id: UUID
    series_id: UUID
    candidate_id: Digest
    family: StrictStr = Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
    forecast_origin: Timestamp
    target_period: Timestamp
    target_period_end: Timestamp
    horizon_step: One = 1
    target_metric: StrictStr = Field(min_length=1, max_length=128)
    measurement_json: StrictStr = Field(max_length=4096)
    temporal_json: StrictStr = Field(max_length=4096)
    transformation: Literal['identity_target_v1'] = 'identity_target_v1'

    @model_validator(mode='after')
    def semantics(self):
        measurement = _DeclaredMeasurement.model_validate_json(self.measurement_json)
        temporal = TemporalFeatureRules.model_validate_json(self.temporal_json)
        for encoded, model in ((self.measurement_json, measurement), (self.temporal_json, temporal)):
            closed_json(encoded, 4096)
            if operational_json(model.model_dump(mode='json')) != encoded:
                raise ValueError('accepted canonical target semantics required')
        if measurement.metric != self.target_metric:
            raise ValueError('target metric mismatch')
        # Same daily/weekly, local-midnight calendar geometry as the accepted
        # canonical temporal rules. No features, tensors or availability inferred.
        start = pd.Timestamp(self.forecast_origin).tz_convert(temporal.timezone)
        step = 1 if temporal.frequency.value == 'daily' else 7
        if (start != start.normalize() or step == 7 and start.weekday() != temporal.week_start
                or self.target_period != self.forecast_origin
                or pd.Timestamp(self.target_period_end).tz_convert(temporal.timezone) != start+pd.DateOffset(days=step)):
            raise ValueError('exact accepted H1 calendar geometry required')
        return self

    @property
    def key_canonical(self):
        # EXACT R7-05A/R7-05B2 preimage; publication/cutoff/execution are lineage.
        return operational_json(['canonical_candidate_h1_forecast_key_v1', str(self.organization_id),
            str(self.dataset_id), str(self.series_id), self.candidate_id, self.family,
            self.forecast_origin.isoformat(), self.target_period.isoformat(), self.target_period_end.isoformat(),
            self.horizon_step, self.target_metric, closed_json(self.measurement_json),
            closed_json(self.temporal_json), self.transformation])

    @property
    def logical_key_id(self):
        return hashlib.sha256(self.key_canonical.encode()).hexdigest()


class ForecastStorageLineage(StorageContract):
    """Immutable declared references only. Digests do not attest original ownership."""
    dataset_version: Digest
    publication_id: Digest
    capture_reference: Digest
    capture_cutoff: Timestamp
    artifact_id: Digest
    artifact_sha256: Digest
    executable_input_id: Digest
    snapshot_id: Digest
    representation_id: Digest
    frozen_state_id: Digest
    owner_reference: Digest
    execution_id: Digest
    producer_issuance_id: Digest
    context_json: StrictStr = Field(default='{}', max_length=64*1024)

    @model_validator(mode='after')
    def canonical_context(self):
        data = closed_json(self.context_json, 64*1024)
        if type(data) is not dict or operational_json(data) != self.context_json:
            raise ValueError('canonical immutable provenance object required')
        return self


class ForecastEvidenceRow(StorageContract):
    version: Literal['1.0'] = '1.0'
    key: ForecastLogicalKey
    lineage: ForecastStorageLineage
    revision: One = 1
    expected_current_version: Zero = 0
    physical_dtype: Literal['float32'] = 'float32'
    float32_le: StrictStr = Field(pattern=r'^[0-9a-f]{8}$')

    @model_validator(mode='after')
    def exact(self):
        if (not math.isfinite(struct.unpack('<f', bytes.fromhex(self.float32_le))[0])
                or self.lineage.capture_cutoff > self.key.forecast_origin):
            raise ValueError('finite binary32 and origin-eligible cutoff required')
        return self

    @property
    def row_sha256(self):
        return hashlib.sha256(self.canonical_bytes).hexdigest()


class CanonicalForecastStorageBatch(StorageContract):
    version: Literal['1.0'] = '1.0'
    classification: Literal['CONTENT_ONLY_NO_PRODUCER_OR_WRITE_AUTHORITY'] = 'CONTENT_ONLY_NO_PRODUCER_OR_WRITE_AUTHORITY'
    scope: ForecastStorageScope
    idempotency_id: Digest
    correction_policy: Literal['INITIAL_WRITE_ONLY'] = 'INITIAL_WRITE_ONLY'
    row_order: Literal['declared_original_row_order_v1'] = 'declared_original_row_order_v1'
    rows: tuple[ForecastEvidenceRow, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode='after')
    def coherent(self):
        keys = tuple(r.key.logical_key_id for r in self.rows)
        if len(set(keys)) != len(keys) or tuple(sorted(r.key.series_id for r in self.rows)) != self.scope.series_ids:
            raise ValueError('unique complete H1 row-set required')
        first = self.rows[0]
        for row in self.rows:
            if (row.key.organization_id != self.scope.organization_id or row.key.dataset_id != self.scope.dataset_id
                    or (row.key.candidate_id, row.key.family, row.key.forecast_origin, row.key.target_period,
                        row.key.target_period_end, row.key.measurement_json, row.key.temporal_json) !=
                       (first.key.candidate_id, first.key.family, first.key.forecast_origin, first.key.target_period,
                        first.key.target_period_end, first.key.measurement_json, first.key.temporal_json)
                    or row.lineage != first.lineage):
                raise ValueError('one coherent scoped lineage required')
        if len(self.canonical_bytes) > 512*1024:
            raise ValueError('bounded canonical batch required')
        return self

    @property
    def content_sha256(self):
        return hashlib.sha256(self.canonical_bytes).hexdigest()


class ForecastStorageReceipt(StorageContract):
    version: Literal['1.0'] = '1.0'
    classification: Literal['DURABLE_STORAGE_ONLY_NOT_PRODUCER_APPROVAL'] = 'DURABLE_STORAGE_ONLY_NOT_PRODUCER_APPROVAL'
    receipt_id: UUID
    scope: ForecastStorageScope
    idempotency_id: Digest
    content_sha256: Digest
    transaction_timestamp: Timestamp
    row_sha256: tuple[Digest, ...] = Field(min_length=1, max_length=32)
    revision: One = 1
    producer_verified: Literal[False] = False
    write_authority_verified: Literal[False] = False
    production_permission: Literal[False] = False

    @model_validator(mode='after')
    def complete(self):
        if len(self.row_sha256) != len(self.scope.series_ids):
            raise ValueError('complete committed row-set required')
        return self


class StoredForecastBatch(StorageContract):
    batch: CanonicalForecastStorageBatch
    receipt: ForecastStorageReceipt

    @model_validator(mode='after')
    def exact(self):
        if (self.receipt.scope != self.batch.scope or self.receipt.idempotency_id != self.batch.idempotency_id
                or self.receipt.content_sha256 != self.batch.content_sha256
                or self.receipt.row_sha256 != tuple(r.row_sha256 for r in self.batch.rows)):
            raise ValueError('exact durable content binding required')
        return self
