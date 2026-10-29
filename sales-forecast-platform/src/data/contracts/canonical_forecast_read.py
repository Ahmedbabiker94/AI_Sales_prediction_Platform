"""Stored evidence content, not authentication, execution or write permission."""
import math
import struct
from typing import Literal
from pydantic import model_validator
from src.data.contracts.forecast_evidence_storage import (
    StorageContract, ForecastStorageScope, StoredForecastBatch, Digest,
)


class ExactForecastRead(StorageContract):
    version: Literal['1.0'] = '1.0'
    purpose: Literal['CANONICAL_EXACT_BATCH_READ'] = 'CANONICAL_EXACT_BATCH_READ'
    scope: ForecastStorageScope
    idempotency_id: Digest
    expected_content_sha256: Digest


class CanonicalReadRecordSet(StorageContract):
    version: Literal['1.0'] = '1.0'
    classification: Literal['VERIFIED_STORAGE_CONTENT_ONLY'] = 'VERIFIED_STORAGE_CONTENT_ONLY'
    stored: StoredForecastBatch

    @property
    def display_values(self):
        """Widen binary32 for display only; original bytes/order remain authoritative."""
        values = tuple(struct.unpack('<f', bytes.fromhex(r.float32_le))[0]
                       for r in self.stored.batch.rows)
        if not all(math.isfinite(v) for v in values):
            raise ValueError('invalid stored numeric encoding')
        return values


class CanonicalReadOutcome(StorageContract):
    version: Literal['1.0'] = '1.0'
    status: Literal['VERIFIED_RECORD_SET', 'DENIED', 'NOT_FOUND', 'UNAVAILABLE']
    records: CanonicalReadRecordSet | None = None

    @model_validator(mode='after')
    def coherent(self):
        if (self.status == 'VERIFIED_RECORD_SET') != (self.records is not None):
            raise ValueError('verified content required only for successful read')
        return self
