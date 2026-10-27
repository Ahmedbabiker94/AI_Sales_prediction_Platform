"""B2 read-only view of the accepted local catalog/payload format.

No writer initialization, SQL temporary index, disk spool or publication API.
Cooperative writers must use the existing publication lock. Host filesystem
administrators are outside trust. Missing catalogs/locks and recovery files reject.
"""
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
import fcntl
import hashlib
import json
import os
import sqlite3
import stat
from time import monotonic
import pyarrow as pa
import pyarrow.parquet as pq
from src.data.contracts.asset import StructuredDataset
from src.data.contracts.dataset import StoredDatasetReference
from src.providers.dataset_store_provider import StagingError
from src.repositories.local_dataset_repository import LocalDatasetRepository, _frame, _table


class _ReadQueries:
    """Exact existing reader queries. Sort bounded chunks in RAM, not SQLite temp."""
    CHUNKS = 'SELECT ordinal,rows,raw_hash,canonical_hash,raw_bytes,canonical_bytes,envelope FROM chunks WHERE attempt=? ORDER BY ordinal'
    READS = {
        'SELECT state,base,manifest FROM attempts WHERE id=?',
        'SELECT COUNT(*) FROM keys WHERE attempt=?',
        'SELECT admission,digest FROM publications WHERE version=? AND attempt=?',
    }
    def __init__(self, connection, budgets):
        self.connection = connection
        self.budgets = budgets

    def execute(self, sql, parameters=()):
        if sql == self.CHUNKS:
            cursor = self.connection.execute(sql.removesuffix(' ORDER BY ordinal')+' LIMIT ?', (*parameters, self.budgets.max_batches+1))
            rows = cursor.fetchall()
            if len(rows) > self.budgets.max_batches:
                raise StagingError('read-only batch limit')
            return SimpleNamespace(fetchall=lambda: sorted(rows, key=lambda r:r[0]))
        if sql not in self.READS:
            raise StagingError('unsupported read-only query')
        return self.connection.execute(sql, parameters)


class ReadOnlyDatasetRepository(LocalDatasetRepository):
    def __init__(self, root, *, reference, series_ids):
        self._fd = None
        if type(reference) is not StoredDatasetReference or type(series_ids) is not tuple or not series_ids:
            raise StagingError('read-only scope required')
        if type(root) is not str or not root.startswith('/') or any(p in ('', '.', '..') for p in root.split('/')[1:]):
            raise StagingError('read-only root required')
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in root.split('/')[1:]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd); fd = child
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise StagingError('private read-only root required')
            self._fd = fd
            self._reference = reference
            self._series = tuple(series_ids)
            self._busy = Lock()
            self._active = None
        except Exception:
            os.close(fd)
            raise StagingError('read-only root unavailable') from None

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def _recovery_absent(self):
        for name in ('catalog.sqlite-journal', 'catalog.sqlite-wal', 'catalog.sqlite-shm'):
            try: os.stat(name, dir_fd=self._fd, follow_symlinks=False)
            except FileNotFoundError: continue
            raise StagingError('read-only recovery unavailable')

    @contextmanager
    def _publication_session(self, budgets):
        if self._fd is None or not self._busy.acquire(blocking=False):
            raise StagingError('read-only view unavailable')
        lock = db = None; connection = None
        try:
            lock = os.open('writer.lock', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self._fd)
            if not stat.S_ISREG(os.fstat(lock).st_mode): raise StagingError('invalid existing lock')
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self._recovery_absent()
            db = os.open('catalog.sqlite', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self._fd)
            info = os.fstat(db)
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= budgets.max_index_bytes:
                raise StagingError('read-only catalog unavailable')
            connection = sqlite3.connect(f'file:/proc/self/fd/{db}?mode=ro', uri=True, timeout=0, isolation_level=None)
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, budgets.max_metadata_bytes)
            allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
            def authorizer(action, one, two, database, trigger):
                if action == sqlite3.SQLITE_TRANSACTION and one == 'BEGIN': return sqlite3.SQLITE_OK
                return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY
            connection.set_authorizer(authorizer)
            started = monotonic()
            connection.set_progress_handler(lambda: int(monotonic()-started >= budgets.max_elapsed_seconds), 1000)
            connection.execute('BEGIN')  # read-only snapshot; close without COMMIT
            self._active = _ReadQueries(connection, budgets)
            yield
            self._recovery_absent()
        except Exception:
            raise StagingError('read-only catalog access failed') from None
        finally:
            self._active = None
            if connection is not None: connection.close()
            if db is not None: os.close(db)
            if lock is not None: os.close(lock)
            self._busy.release()

    @contextmanager
    def _connection(self, budgets):
        if self._active is None: raise StagingError('read-only session required')
        yield self._active

    def _candidate(self, reference, budgets):
        if reference != self._reference: raise StagingError('read-only dataset scope mismatch')
        candidate = super()._candidate(reference, budgets)
        series = set()
        for encoded in candidate.manifest.envelopes:
            dataset = StructuredDataset.model_validate(json.loads(encoded)['canonical_dataset'])
            if dataset.asset.organization_id != reference.organization_id: raise StagingError('read-only tenant mismatch')
            series.update(s.series_id for s in dataset.series)
        if series != set(self._series): raise StagingError('complete authorized series scope required')
        return candidate

    def _verified_chunk(self, candidate, chunk, checkpoint):
        # Same accepted codec/envelope verification; only fd-relative reads differ.
        ordinal, rows, rh, ch, rb, cb, envelope = chunk
        checkpoint(); frames = {}
        directory = os.open(str(candidate.id), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._fd)
        try:
            for kind, digest, size in (('raw',rh,rb), ('canonical',ch,cb)):
                fd = os.open(f'{ordinal}-{kind}.parquet', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
                with os.fdopen(fd, 'rb') as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size != size: raise StagingError('read-only chunk size mismatch')
                    payload = stream.read(candidate.budgets.max_chunk_bytes+1)
                if hashlib.sha256(payload).hexdigest() != digest: raise StagingError('read-only chunk digest mismatch')
                table = pq.read_table(pa.BufferReader(payload))
                if table.num_rows != rows or table.schema.metadata.get(b'envelope_sha256') != hashlib.sha256(envelope.encode()).hexdigest().encode():
                    raise StagingError('read-only chunk lineage mismatch')
                restored = _frame(table)
                verified = _table(restored, candidate.budgets)
                verified = verified.replace_schema_metadata({**verified.schema.metadata, b'envelope_sha256':table.schema.metadata[b'envelope_sha256']})
                if not verified.equals(table, check_metadata=True): raise StagingError('read-only round-trip mismatch')
                frames[kind] = restored
            self._validate_envelope(candidate, envelope, frames, rows)
            return frames, payload
        finally: os.close(directory)

    @contextmanager
    def _read_snapshot(self, candidate, checkpoint):
        payloads = []; size = 0
        for chunk in candidate.chunks:
            _, payload = self._verified_chunk(candidate, chunk, checkpoint)
            size += len(payload)
            if size > min(candidate.budgets.max_canonical_bytes, 16*1024*1024): raise StagingError('read-only memory budget exceeded')
            payloads.append(payload)
        checkpoint()
        def batches():
            for body in payloads:
                checkpoint()
                yield _frame(pq.read_table(pa.BufferReader(body)))
        yield batches

    @contextmanager
    def _quality_key_index(self, budgets):
        counts = {}; size = 0
        def record(kind, identity):
            nonlocal size
            key = (kind, identity)
            old = counts.get(key, 0)
            if not old:
                size += len(kind.encode())+len(identity.encode())+256
                if len(counts) >= budgets.max_unique_keys or size > min(budgets.max_index_bytes, 8*1024*1024):
                    raise StagingError('read-only quality index limit')
            counts[key] = old+1
            return 0 if old == 0 else 2 if old == 1 else 1
        yield record

    def _publication(self, reference, budgets):
        if reference != self._reference: raise StagingError('read-only publication scope mismatch')
        return super()._publication(reference, budgets)

    def _write_unavailable(self, *args, **kwargs):
        raise StagingError('read-only repository has no write operations')
    _begin = _append = _seal = _abort = _release = _publish = _reject = _write_unavailable
