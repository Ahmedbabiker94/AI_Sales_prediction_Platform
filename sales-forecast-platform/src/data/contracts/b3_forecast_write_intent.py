"""B3-backed proposed records and write intent, never a database commit receipt.

Public declarations and digests establish no authority. Only the original opaque
handle at the host-pinned live issuer establishes a process-local write intent.
"""
import math
import struct
from typing import Literal

from pydantic import Field, StrictFloat, StrictInt, model_validator
from src.data.contracts.backtesting import identity
from src.data.contracts.candidate_package import Digest
from src.data.contracts.model_representation import RepresentationContract, RepresentationPoint
from src.data.contracts.runtime_snapshot import _schema_from_json
from src.data.contracts.successful_b3_producer import SuccessfulB3ReceiptDeclaration


class ForecastWriteIntentLimits(RepresentationContract):
    max_grants: StrictInt = Field(default=32, ge=1, le=128)
    max_elapsed_seconds: StrictInt = Field(default=30, ge=1, le=60)
    max_record_bytes: StrictInt = Field(default=512*1024, ge=1, le=512*1024)


def forecast_logical_key(execution, scope):
    """Reuse R7-05A's canonical_candidate_h1_forecast_key_v1 EXACTLY.

    Publication, cutoff, native/input and execution evidence remain lineage;
    changing them cannot evade a future database's uniqueness constraint.
    """
    request = execution.request
    source = request.inputs.request.snapshot_request
    cfg = _schema_from_json(source.feature_schema_json).configuration
    return identity(['canonical_candidate_h1_forecast_key_v1',
        str(source.dataset.reference.organization_id), str(source.dataset.reference.dataset_id),
        str(scope.series_id), request.candidate_id, request.family, source.forecast_origin.isoformat(),
        scope.target_period.isoformat(), scope.target_period_end.isoformat(), scope.horizon_step,
        cfg.target_metric, cfg.execution.measurement.model_dump(mode='json', warnings='error'),
        cfg.execution.temporal.model_dump(mode='json', warnings='error'), 'identity_target_v1'])


class B3ForecastRow(RepresentationContract):
    scope: RepresentationPoint
    logical_key_id: Digest
    float32_le: str = Field(pattern=r'^[0-9a-f]{8}$')
    physical_dtype: Literal['float32'] = 'float32'

    @model_validator(mode='after')
    def finite(self):
        if not math.isfinite(self.prediction_value):
            raise ValueError('finite float32 bytes required')
        return self

    @property
    def prediction_value(self):
        return struct.unpack('<f', bytes.fromhex(self.float32_le))[0]


def forecast_row_set_identity(producer, rows):
    return identity(['b3_backed_canonical_forecast_rows_v1', producer.receipt_id,
        producer.result_set_sha256, [r.model_dump(mode='json', warnings='error') for r in rows]])


class B3ForecastRecordSet(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    classification: Literal['B3_BACKED_PROPOSED_H1_RECORDS_ONLY'] = 'B3_BACKED_PROPOSED_H1_RECORDS_ONLY'
    producer: SuccessfulB3ReceiptDeclaration
    idempotency_id: Digest
    rows: tuple[B3ForecastRow, ...] = Field(min_length=1, max_length=32)
    shape: tuple[StrictInt, ...] = Field(min_length=1, max_length=1)
    physical_dtype: Literal['float32'] = 'float32'
    byte_encoding: Literal['ieee754_binary32_little_endian_hex_v1'] = 'ieee754_binary32_little_endian_hex_v1'
    row_order: Literal['original_b3_sample_order_v1'] = 'original_b3_sample_order_v1'
    transformation: Literal['identity_target_v1'] = 'identity_target_v1'
    correction_policy: Literal['INITIAL_WRITE_ONLY'] = 'INITIAL_WRITE_ONLY'
    row_set_sha256: Digest
    writes_performed: Literal[False] = False
    persisted: Literal[False] = False

    @model_validator(mode='after')
    def bound(self):
        p = self.producer
        if self.shape != p.shape or len(self.rows) != len(p.rows):
            raise ValueError('complete original result shape required')
        keys = tuple(r.logical_key_id for r in self.rows)
        if len(set(keys)) != len(keys):
            raise ValueError('duplicate logical forecast key')
        for row, original in zip(self.rows, p.rows):
            if (row.scope != original.scope or row.float32_le != original.float32_le
                    or row.logical_key_id != forecast_logical_key(p.execution, row.scope)):
                raise ValueError('exact ordered original H1 rows required')
        if self.row_set_sha256 != forecast_row_set_identity(p, self.rows):
            raise ValueError('exact canonical row-set digest required')
        return self

    @property
    def record_set_id(self):
        return identity(['b3_backed_forecast_record_set_v1', self.model_dump(mode='json', warnings='error')])


class ForecastWriteGrantDeclaration(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    purpose: Literal['CONTROLLED_OFFLINE_H1_EVIDENCE_WRITE'] = 'CONTROLLED_OFFLINE_H1_EVIDENCE_WRITE'
    issuer_id: Digest
    ordinal: StrictInt = Field(ge=1, le=128)
    producer_receipt_id: Digest
    record_set_id: Digest
    row_set_sha256: Digest
    idempotency_id: Digest
    issued_at: StrictFloat
    expires_at: StrictFloat
    write_deadline: StrictFloat
    max_attempts: Literal[1] = 1
    cancellation_policy: Literal['host_bound_and_operation_cooperative_v1'] = 'host_bound_and_operation_cooperative_v1'
    correction_policy: Literal['INITIAL_WRITE_ONLY'] = 'INITIAL_WRITE_ONLY'

    @model_validator(mode='after')
    def bounded(self):
        if (not all(math.isfinite(v) for v in (self.issued_at, self.expires_at, self.write_deadline))
                or not self.issued_at < self.write_deadline <= self.expires_at <= self.issued_at+60):
            raise ValueError('bounded write-intent interval required')
        return self

    @property
    def grant_id(self):
        return identity(['forecast_write_intent_grant_v1', self.model_dump(mode='json', warnings='error')])


class ValidatedForecastWriteIntent(RepresentationContract):
    """Detached inspection/simulation output, never accepted as a capability."""
    version: Literal['1.0'] = '1.0'
    classification: Literal['PROCESS_LOCAL_WRITE_INTENT_NO_DATABASE_COMMIT'] = 'PROCESS_LOCAL_WRITE_INTENT_NO_DATABASE_COMMIT'
    state: Literal['ISSUED', 'CONSUMED_NO_WRITE']
    grant: ForecastWriteGrantDeclaration
    records: B3ForecastRecordSet
    writes_performed: Literal[False] = False
    persistence_completed: Literal[False] = False
    durable_receipt: None = None
    production_permission: Literal[False] = False

    @model_validator(mode='after')
    def bound(self):
        g, r = self.grant, self.records
        if (g.producer_receipt_id != r.producer.receipt_id or g.record_set_id != r.record_set_id
                or g.row_set_sha256 != r.row_set_sha256 or g.idempotency_id != r.idempotency_id
                or not r.producer.issued_at <= g.issued_at < g.expires_at <= r.producer.expires_at):
            raise ValueError('exact live producer and proposed record bindings required')
        return self


class ForecastWriteCapability:
    """Original issuance identity only; no token, serialization or write method."""
    __slots__ = ()

    def __reduce__(self):
        raise TypeError('forecast write capabilities are process-local')
