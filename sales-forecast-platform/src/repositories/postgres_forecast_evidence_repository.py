"""Internal offline storage primitive. No producer or write-authority decision.

Only the protected host may hold these database credentials. There is deliberately
no route, job registration, application bootstrap import or public write endpoint.
"""
from dataclasses import dataclass
from uuid import UUID, uuid4

from src.data.contracts.forecast_evidence_storage import (
    CanonicalForecastStorageBatch, ForecastStorageScope, ForecastStorageReceipt, StoredForecastBatch,
)
from src.database.offline_forecast_postgres import (
    OfflineForecastPostgres, ForecastStorageError, ForecastStorageRejected,
    ForecastStorageConflict, ForecastStorageUnavailable, ForecastStorageRetryable,
    AmbiguousForecastCommit, reject, verify_forecast_schema,
)


@dataclass(frozen=True)
class _PostgresForecastEvidenceRepository:
    reader: OfflineForecastPostgres
    writer: OfflineForecastPostgres
    scope: ForecastStorageScope

    def __post_init__(self):
        if (type(self.reader) is not OfflineForecastPostgres or self.reader.access != 'reader'
                or type(self.writer) is not OfflineForecastPostgres or self.writer.access != 'writer'
                or self.reader.instance_id != self.writer.instance_id or self.reader.database != self.writer.database
                or type(self.scope) is not ForecastStorageScope):
            reject()
        ForecastStorageScope.model_validate_json(self.scope.canonical_json)

    def _batch(self, batch):
        try:
            if type(batch) is not CanonicalForecastStorageBatch:
                raise ValueError()
            batch = CanonicalForecastStorageBatch.model_validate_json(batch.canonical_json)
            if batch.scope != self.scope:
                raise ValueError()
            return batch
        except Exception:
            pass
        reject()

    def _find(self, cursor, idempotency_id):
        cursor.execute('SELECT receipt_id::text, content_sha256, record_set_canonical, '
            'transaction_timestamp, series_ids::text[] FROM canonical_forecast.batches '
            'WHERE organization_id=%s AND dataset_id=%s AND idempotency_id=%s',
            (str(self.scope.organization_id), str(self.scope.dataset_id), idempotency_id))
        header = cursor.fetchone()
        if header is None:
            return None
        batch = self._batch(CanonicalForecastStorageBatch.model_validate_json(header[2]))
        if (batch.canonical_json != header[2] or batch.content_sha256 != header[1]
                or batch.idempotency_id != idempotency_id
                or tuple(header[4]) != tuple(str(s) for s in self.scope.series_ids)):
            raise ValueError()
        cursor.execute('SELECT e.ordinal, e.logical_key_id, e.key_canonical, e.row_canonical, '
            'e.row_sha256, e.float32_le, c.current_revision, c.row_sha256 '
            'FROM canonical_forecast.evidence e JOIN canonical_forecast.current_records c '
            'ON (c.organization_id,c.dataset_id,c.logical_key_id)=(e.organization_id,e.dataset_id,e.logical_key_id) '
            'WHERE e.organization_id=%s AND e.dataset_id=%s AND e.idempotency_id=%s ORDER BY e.ordinal',
            (str(self.scope.organization_id), str(self.scope.dataset_id), idempotency_id))
        stored = cursor.fetchall()
        if len(stored) != len(batch.rows):
            raise ValueError()
        for ordinal, (row, actual) in enumerate(zip(batch.rows, stored)):
            expected = (ordinal, row.key.logical_key_id, row.key.key_canonical, row.canonical_json,
                row.row_sha256, bytes.fromhex(row.float32_le), 1, row.row_sha256)
            actual = actual[:5]+(bytes(actual[5]),)+actual[6:]
            if actual != expected:
                raise ValueError()
        receipt = ForecastStorageReceipt(receipt_id=UUID(header[0]), scope=self.scope,
            idempotency_id=idempotency_id, content_sha256=batch.content_sha256,
            transaction_timestamp=header[3], row_sha256=tuple(r.row_sha256 for r in batch.rows))
        return StoredForecastBatch(batch=batch, receipt=receipt)

    def _lookup(self, idempotency_id, *, expected=None):
        """Separate SELECT-only role and READ ONLY transaction; never migrates."""
        try:
            if type(idempotency_id) is not str or len(idempotency_id) != 64 or any(c not in '0123456789abcdef' for c in idempotency_id):
                reject()
            expected = None if expected is None else self._batch(expected)
            with self.reader._open(self.scope) as connection:
                with connection.cursor() as cursor:
                    verify_forecast_schema(cursor)
                    found = self._find(cursor, idempotency_id)
                    if found is not None and expected is not None and found.batch.canonical_bytes != expected.canonical_bytes:
                        reject(ForecastStorageConflict)
                    return found
        except ForecastStorageError:
            raise
        except Exception:
            pass
        reject(ForecastStorageUnavailable)

    def _insert_header(self, cursor, batch):
        cursor.execute('INSERT INTO canonical_forecast.batches '
            '(organization_id,dataset_id,idempotency_id,receipt_id,content_sha256,record_set_canonical,series_ids,row_count) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s::uuid[],%s) '
            'ON CONFLICT (organization_id,dataset_id,idempotency_id) DO NOTHING RETURNING receipt_id',
            (str(self.scope.organization_id), str(self.scope.dataset_id), batch.idempotency_id, str(uuid4()),
             batch.content_sha256, batch.canonical_json, [str(s) for s in self.scope.series_ids], len(batch.rows)))
        return cursor.fetchone() is not None

    def _insert_row(self, cursor, batch, ordinal):
        row = batch.rows[ordinal]
        key = row.key
        cursor.execute('INSERT INTO canonical_forecast.evidence '
            '(organization_id,dataset_id,logical_key_id,revision,expected_current_version,idempotency_id,ordinal,'
            'series_id,candidate_id,family,forecast_origin,target_period,target_period_end,capture_cutoff,'
            'key_canonical,row_canonical,row_sha256,float32_le) '
            'VALUES (%s,%s,%s,1,0,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (str(key.organization_id), str(key.dataset_id), key.logical_key_id, batch.idempotency_id, ordinal,
             str(key.series_id), key.candidate_id, key.family, key.forecast_origin, key.target_period,
             key.target_period_end, row.lineage.capture_cutoff, key.key_canonical, row.canonical_json,
             row.row_sha256, bytes.fromhex(row.float32_le)))
        # Compare-and-swap from ABSENT (version 0) only. UNIQUE rejects every
        # existing pointer. There is no UPDATE, correction or promotion operation.
        cursor.execute('INSERT INTO canonical_forecast.current_records '
            '(organization_id,dataset_id,logical_key_id,current_revision,idempotency_id,series_id,row_sha256) '
            'VALUES (%s,%s,%s,1,%s,%s,%s)', (str(key.organization_id), str(key.dataset_id), key.logical_key_id,
            batch.idempotency_id, str(key.series_id), row.row_sha256))

    def _before_commit(self, connection):
        """Internal fault-injection checkpoint, never supplied by a write caller."""

    def _append_initial(self, batch):
        """Commit synthetic/validated content only. B4 authority is NOT implemented.

        No automatic retries. An unknown commit acknowledgement requires read-only
        exact-content lookup before a retry of the entire identical batch/id.
        """
        committing = False
        try:
            batch = self._batch(batch)
            with self.writer._open(self.scope) as connection:
                with connection.cursor() as cursor:
                    verify_forecast_schema(cursor)
                    inserted = self._insert_header(cursor, batch)
                    if inserted:
                        for ordinal in range(len(batch.rows)):
                            self._insert_row(cursor, batch, ordinal)
                    found = self._find(cursor, batch.idempotency_id)
                    if found is None or found.batch.canonical_bytes != batch.canonical_bytes:
                        reject(ForecastStorageConflict)
                self._before_commit(connection)
                committing = True
                connection.commit()
                committing = False
                return found
        except ForecastStorageError:
            raise
        except Exception as error:
            code = getattr(error, 'pgcode', None)
            if code in ('40001', '40P01'):
                kind = ForecastStorageRetryable
            elif code == '23505':
                kind = ForecastStorageConflict
            elif committing and code is None:
                kind = AmbiguousForecastCommit
            elif code in ('23514', '23503', '42501', '22023', '22P02'):
                kind = ForecastStorageRejected
            else:
                kind = ForecastStorageUnavailable
        reject(kind)
