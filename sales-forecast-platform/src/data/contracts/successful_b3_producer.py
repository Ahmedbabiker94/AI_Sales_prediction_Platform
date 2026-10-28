"""B3-backed successful-result declarations, never executable or write authority.

Only a producer's retained original handle establishes local origin. These
public values can be reconstructed and their digests authenticate no caller.
"""
import math
import struct
from typing import Literal

from pydantic import Field, StrictFloat, StrictInt, model_validator
from src.data.contracts.backtesting import identity
from src.data.contracts.candidate_package import Digest
from src.data.contracts.controlled_h1_execution import ControlledH1ExecutionResult
from src.data.contracts.model_representation import RepresentationContract, RepresentationPoint
from src.data.contracts.runtime_snapshot import _schema_from_json
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline


class ProducerLimits(RepresentationContract):
    max_receipts: StrictInt = Field(default=32, ge=1, le=128)
    max_elapsed_seconds: StrictInt = Field(default=60, ge=1, le=60)
    max_receipt_bytes: StrictInt = Field(default=512*1024, ge=1, le=512*1024)


class ProducerRow(RepresentationContract):
    scope: RepresentationPoint
    float32_le: str = Field(pattern=r'^[0-9a-f]{8}$')


class SuccessfulB3ReceiptDeclaration(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    classification: Literal['SUCCESSFUL_B3_CONTROLLED_OFFLINE_H1_ONLY'] = 'SUCCESSFUL_B3_CONTROLLED_OFFLINE_H1_ONLY'
    producer_id: Digest
    ordinal: StrictInt = Field(ge=1, le=128)
    issued_at: StrictFloat
    expires_at: StrictFloat
    execution: ControlledH1ExecutionResult
    rows: tuple[ProducerRow, ...] = Field(min_length=1, max_length=32)
    shape: tuple[StrictInt, ...] = Field(min_length=1, max_length=1)
    physical_dtype: Literal['float32'] = 'float32'
    result_set_sha256: Digest
    execution_permission: Literal[False] = False
    write_permission: Literal[False] = False
    persistable: Literal[False] = False

    @model_validator(mode='after')
    def bound(self):
        e = self.execution
        if (not all(math.isfinite(v) for v in (self.issued_at, self.expires_at))
                or not 0 < self.expires_at-self.issued_at <= 60
                or not e.grant.issued_at <= self.issued_at < min(e.grant.expires_at, e.grant.execution_deadline)
                or self.shape != (len(self.rows),) or len(self.rows) != len(e.predictions)):
            raise ValueError('bounded successful result required')
        source = e.request.inputs.request.snapshot_request
        engine = CanonicalFeaturePipeline(_schema_from_json(source.feature_schema_json).configuration)
        origin = engine._time(source.forecast_origin)
        if sorted(r.scope.series_id for r in self.rows) != sorted(source.dataset.series_ids):
            raise ValueError('complete original series required')
        for row, value in zip(self.rows, e.predictions):
            p = row.scope
            if (row.float32_le != struct.pack('<f', value).hex()
                    or p.organization_id != source.dataset.reference.organization_id
                    or p.dataset_version != source.dataset.reference.version_id
                    or p.forecast_origin != origin or p.target_period != origin
                    or p.target_period_end != engine._advance(origin, 1) or p.horizon_step != 1):
                raise ValueError('exact ordered H1 rows required')
        expected = result_set_identity(e, self.rows)
        if self.result_set_sha256 != expected:
            raise ValueError('exact result-set encoding required')
        return self

    @property
    def receipt_id(self):
        return identity(['successful_b3_receipt_v1', self.model_dump(mode='json', warnings='error')])


def result_set_identity(execution, rows):
    return identity(['successful_b3_result_set_v1', execution.result_id,
                     [r.model_dump(mode='json', warnings='error') for r in rows]])


class SuccessfulB3Receipt:
    """Opaque original issuance handle; possession of a declaration is insufficient."""
    __slots__ = ()

    def __reduce__(self):
        raise TypeError('producer receipts are process-local')
