"""Single-host private staging. No publication or training-read interface.

Parquet cells contain a narrow tagged scalar encoding, not executable Python
objects. This preserves raw scalar kinds, Decimal tuples, float bits and null
sentinels; it deliberately rejects nested/object containers. All SQL stays here.
Supported execution: Linux/WSL on a local filesystem supporting flock/fsync.
Native Windows, OneDrive/synchronized folders and network filesystems are not
supported roots. The trusted application must enforce the storage location.
Storage scalar vocabulary is closed: standard numpy numeric scalars, native
scalars, finite Decimal, date, fixed-offset datetime and declared Timestamp zones.
StringDtype retains backend and missing-value parameters; datetime dtype retains
unit/zone; simple Range/Datetime/Categorical indexes retain declared parameters.
Unsupported containers, custom timezone implementations and indexes reject.
Read-only modes are accident protection, not a boundary against the same OS user.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
try:
    import fcntl
except ImportError:  # Native Windows is explicitly unsupported.
    fcntl = None
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import stat
import re
from urllib.parse import urlsplit
import struct
from uuid import UUID, uuid4

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src.data.contracts.dataset import DatasetManifest, StagingState, ConsistencyEvidence
from src.data.contracts import AssetMetadata, StructuredDataset, SourceMapping
from src.data.adapters.base import validate_record_ids
from src.providers.dataset_store_provider import DatasetStoreProvider, StagingError, _validate_lifecycle_owner


_NUMPY = tuple(getattr(np, n) for n in ('bool_', 'int8', 'int16', 'int32', 'int64',
    'uint8', 'uint16', 'uint32', 'uint64', 'float16', 'float32', 'float64'))


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode('utf-8')


def _text(value, budgets):
    if type(value) is not str:
        raise StagingError('unsupported text representation')
    try:
        size = len(value.encode('utf-8', errors='strict'))
    except UnicodeError:
        raise StagingError('invalid Unicode in staged content') from None
    if size > budgets.max_cell_bytes:
        raise StagingError('staging cell size limit exceeded')


def _metadata(value, budgets, depth=0):
    if depth > budgets.max_nesting_depth:
        raise StagingError('metadata nesting limit exceeded')
    kind = type(value)
    if kind is str:
        _text(value, budgets)
    elif kind in (dict, list, tuple):
        if kind is dict:
            for k, v in value.items():
                _text(k, budgets); _metadata(v, budgets, depth + 1)
        else:
            for item in value:
                _metadata(item, budgets, depth + 1)
    elif value is not None and kind not in (bool, int, float):
        raise StagingError('unsupported metadata representation')
    elif kind is float and not np.isfinite(value):
        raise StagingError('nonfinite metadata')


# This is a provenance vocabulary, not a filter on private business record values.
_SOURCE_METADATA = frozenset(('csv_encoding', 'csv_delimiter', 'csv_column_types',
    'source_sheet', 'source_consistency', 'source_order', 'source_page',
    'odata_schema_fingerprint', 'odata_version', 'source_reference', 'connection_reference'))
_CANONICAL_METADATA = _SOURCE_METADATA | frozenset(('mapping_id', 'mapping_version',
    'metric_meaning', 'sign_semantics', 'policy_metric', 'policy_context',
    'business_scope', 'bulk_observations'))
_SECRET_KEYS = frozenset(('authorization', 'headers', 'password', 'secret', 'token',
    'access_token', 'refresh_token', 'api_key', 'credential', 'credentials',
    'nextlink', 'odata.nextlink', 'continuation', 'continuation_url'))


def _provenance(value, budgets, depth=0):
    _metadata(value, budgets, depth)
    if type(value) is str:
        if '://' in value or urlsplit(value).scheme.casefold() in ('http','https','ftp','file','postgres','postgresql') or re.search(r'(?i)(?:^|\s)(?:bearer|basic)\s+\S+', value):
            raise StagingError('unsafe provenance value')
    elif type(value) is dict:
        for key, item in value.items():
            if key.strip().casefold().replace('-', '_') in _SECRET_KEYS:
                raise StagingError('unsafe provenance field')
            _provenance(item, budgets, depth + 1)
    elif type(value) in (tuple, list):
        for item in value: _provenance(item, budgets, depth + 1)


def _asset_provenance(asset, allowed, budgets):
    if not set(asset.metadata) <= allowed:
        raise StagingError('unsupported provenance metadata field')
    _provenance(asset.model_dump(mode='json'), budgets)
    for key, value in asset.metadata.items():
        if key == 'csv_column_types':
            if type(value) is not dict or len(value) > budgets.max_columns or any(
                    type(field) is not str or not field.strip() or kind not in ('text','integer','float','boolean')
                    for field, kind in value.items()):
                raise StagingError('unsupported nested provenance shape')
        elif key == 'source_page':
            if type(value) is not int or value <= 0:
                raise StagingError('invalid provenance page')
        elif key == 'bulk_observations':
            if value is not True: raise StagingError('invalid bulk provenance flag')
        elif type(value) is not str or not value.strip():
            raise StagingError('provenance identifiers require nonblank text')



def _timezone_spec(tz):
    if tz is None: return None
    if tz is timezone.utc: return {'utc': True}
    if type(tz) is timezone:
        offset = tz.utcoffset(None)
        return {'fixed': [offset.days, offset.seconds, offset.microseconds, tz.tzname(None)]}
    if type(tz) is ZoneInfo:
        return {'zoneinfo': tz.key}
    # Pandas commonly uses canonical pytz zones. Reject arbitrary tz subclasses.
    import pytz
    if tz is pytz.UTC: return {'pytz': 'UTC'}
    if type(tz).__module__ == 'pytz.tzfile' and type(tz) is type(pytz.timezone(tz.zone)):
        return {'pytz': tz.zone}
    raise StagingError('unsupported timezone representation')


def _restore_timezone(spec):
    if spec is None: return None
    if 'utc' in spec: return timezone.utc
    if 'fixed' in spec:
        days, seconds, micros, name = spec['fixed']
        return timezone(timedelta(days=days, seconds=seconds, microseconds=micros), name)
    if 'zoneinfo' in spec: return ZoneInfo(spec['zoneinfo'])
    if 'pytz' in spec:
        import pytz
        return pytz.timezone(spec['pytz'])
    raise StagingError('unsupported stored timezone')


def _scalar(value, budgets):
    kind = type(value)
    if value is None: payload = ['none']
    elif value is pd.NA: payload = ['pd.NA']
    elif value is pd.NaT: payload = ['pd.NaT']
    elif kind is str:
        _text(value, budgets); payload = ['str', value]
    elif kind is bool: payload = ['bool', value]
    elif kind is int: payload = ['int', str(value)]
    elif kind is float: payload = ['float', struct.pack('!d', value).hex()]
    elif kind is Decimal:
        if not value.is_finite():
            raise StagingError('nonfinite Decimal storage value')
        sign, digits, exponent = value.as_tuple()
        payload = ['decimal', sign, list(digits), exponent]
    elif kind is UUID: payload = ['uuid', str(value)]
    elif kind is date: payload = ['date', value.isoformat()]
    elif kind is datetime:
        # Named fixed-offset zones are preserved explicitly. General datetime
        # tz implementations are rejected; Timestamp supports declared zones.
        if value.tzinfo is not None and type(value.tzinfo) is not timezone:
            raise StagingError('unsupported datetime timezone representation')
        payload = ['datetime', value.isoformat(), value.fold, _timezone_spec(value.tzinfo)]
    elif kind is pd.Timestamp:
        if value.__dict__:
            raise StagingError('unsupported timestamp state')
        payload = ['timestamp', value.isoformat(), _timezone_spec(value.tz), value.unit, value.fold]
    elif any(kind is t for t in _NUMPY):
        payload = ['numpy', value.dtype.str, value.tobytes().hex()]
    else:
        raise StagingError('unsupported scalar or nested auxiliary storage value')
    encoded = _json(payload)
    if len(encoded) > budgets.max_cell_bytes:
        raise StagingError('staging cell size limit exceeded')
    return encoded


def _decode(value):
    p = json.loads(value)
    tag = p[0]
    if tag == 'none': return None
    if tag == 'pd.NA': return pd.NA
    if tag == 'pd.NaT': return pd.NaT
    if tag in ('str', 'bool'): return p[1]
    if tag == 'int': return int(p[1])
    if tag == 'float': return struct.unpack('!d', bytes.fromhex(p[1]))[0]
    if tag == 'decimal': return Decimal((p[1], tuple(p[2]), p[3]))
    if tag == 'uuid': return UUID(p[1])
    if tag == 'date': return date.fromisoformat(p[1])
    if tag == 'datetime': return datetime.fromisoformat(p[1]).replace(tzinfo=_restore_timezone(p[3]), fold=p[2])
    if tag == 'timestamp':
        v = pd.Timestamp(p[1])
        if p[2] is not None: v = v.tz_convert(_restore_timezone(p[2]))
        return v.as_unit(p[3]).replace(fold=p[4])
    if tag == 'numpy':
        dtype = np.dtype(p[1])
        if dtype.type not in _NUMPY:
            raise StagingError('invalid stored scalar type')
        return np.frombuffer(bytes.fromhex(p[2]), dtype=dtype, count=1)[0]
    raise StagingError('unknown stored scalar type')


def _dtype_spec(dtype, budgets):
    if type(dtype) is pd.StringDtype:
        return {'string': dtype.storage, 'na_value': _scalar(dtype.na_value, budgets).decode()}
    if type(dtype) is pd.DatetimeTZDtype:
        return {'datetime_tz': _timezone_spec(dtype.tz), 'unit': dtype.unit}
    if type(dtype) is pd.ArrowDtype:
        return {'arrow': pa.schema([('value', dtype.pyarrow_dtype)]).serialize().to_pybytes().hex()}
    if type(dtype) is pd.CategoricalDtype:
        return {'category': [_scalar(v, budgets).decode() for v in dtype.categories], 'ordered': dtype.ordered}
    # Only standard pandas/numpy dtype implementations, never custom extensions.
    if not type(dtype).__module__.startswith(('numpy', 'pandas.core.dtypes', 'pandas.core.arrays')):
        raise StagingError('unsupported staging dtype')
    return {'dtype': str(dtype)}


def _restore_dtype(spec):
    if 'string' in spec:
        return pd.StringDtype(storage=spec['string'], na_value=_decode(spec['na_value'].encode()))
    if 'datetime_tz' in spec:
        return pd.DatetimeTZDtype(unit=spec['unit'], tz=_restore_timezone(spec['datetime_tz']))
    if 'arrow' in spec:
        return pd.ArrowDtype(pa.ipc.read_schema(pa.BufferReader(bytes.fromhex(spec['arrow']))).field(0).type)
    if 'category' in spec:
        return pd.CategoricalDtype([_decode(v.encode()) for v in spec['category']], ordered=spec['ordered'])
    return spec['dtype']


def _table(frame, budgets):
    if not frame.columns.is_unique or frame.shape[1] > budgets.max_columns or isinstance(frame.index, pd.MultiIndex):
        raise StagingError('unsupported staging schema')
    for name in frame.columns: _text(name, budgets)
    if frame.index.name is not None: _text(frame.index.name, budgets)
    # Iterate bounded scalar cells only. No pickle, conversion hooks or repr.
    columns = {}
    total = 0
    for key, values in [(str(i), frame[name]) for i, name in enumerate(frame.columns)] + [('index', frame.index)]:
        cells = []
        for value in values.array:
            cell = _scalar(value, budgets)
            total += len(cell)
            if total > budgets.max_chunk_bytes:
                raise StagingError('decoded chunk budget exceeded')
            cells.append(cell)
        columns[key] = cells
    table = pa.table({k: pa.array(v, type=pa.binary()) for k, v in columns.items()})
    if type(frame.index) not in (pd.Index, pd.RangeIndex, pd.DatetimeIndex, pd.CategoricalIndex):
        raise StagingError('unsupported staging index representation')
    index_parameters = {}
    if type(frame.index) is pd.RangeIndex:
        index_parameters['range'] = [frame.index.start, frame.index.stop, frame.index.step]
    if type(frame.index) is pd.DatetimeIndex:
        index_parameters['frequency'] = frame.index.freqstr
    info = {'index_parameters': index_parameters, 'columns': list(frame.columns), 'dtypes': [_dtype_spec(t, budgets) for t in frame.dtypes],
            'index_name': frame.index.name, 'index_dtype': _dtype_spec(frame.index.dtype, budgets)}
    shape_bytes = _json(info)
    if len(shape_bytes) > budgets.max_metadata_bytes:
        raise StagingError('chunk schema metadata budget exceeded')
    table = table.replace_schema_metadata({b'staging_shape': shape_bytes, b'storage_version': b'1.1'})
    if table.nbytes > budgets.max_chunk_bytes:
        raise StagingError('decoded chunk budget exceeded')
    return table


def _frame(table):
    """Internal verification/test decoder; no dataset-reading public API."""
    if table.schema.metadata.get(b'storage_version') != b'1.1':
        raise StagingError('unsupported staging storage version')
    info = json.loads(table.schema.metadata[b'staging_shape'])
    index = pd.Index([_decode(v) for v in table['index'].to_pylist()], dtype=_restore_dtype(info['index_dtype']), name=info['index_name'])
    parameters = info.get('index_parameters', {})
    if 'range' in parameters:
        expected = pd.RangeIndex(*parameters['range'], name=info['index_name'])
        if not index.equals(expected): raise StagingError('stored range index mismatch')
        index = expected
    if 'frequency' in parameters:
        index = pd.DatetimeIndex(index, freq=parameters['frequency'], name=info['index_name'])
    frame = pd.DataFrame(index=index)
    for i, (name, dtype) in enumerate(zip(info['columns'], info['dtypes'])):
        frame[name] = pd.Series([_decode(v) for v in table[str(i)].to_pylist()], index=index, dtype=_restore_dtype(dtype))
    return frame


def _verify_original(original, reconstructed, budgets):
    try:
        pd.testing.assert_frame_equal(original, reconstructed, check_exact=True,
                                      check_index_type=True, check_column_type=True)
        for column in original.columns:
            dtype = original[column].dtype
            if isinstance(dtype, np.dtype) and dtype.kind in 'biuf':
                if original[column].to_numpy().tobytes() != reconstructed[column].to_numpy().tobytes():
                    raise StagingError('storage changes original numeric bits')
        for original_values, restored_values in [(original.index, reconstructed.index)] + [
                (original[c], reconstructed[c]) for c in original.columns]:
            for left, right in zip(original_values.array, restored_values.array):
                if _scalar(left, budgets) != _scalar(right, budgets):
                    raise StagingError('storage changes original scalar representation')
    except (AssertionError, ValueError, TypeError):
        raise StagingError('storage changes original input representation') from None


@dataclass
class _Attempt:
    id: UUID
    lock: object
    budgets: object
    base: dict
    source_shape: tuple | None = None
    canonical_shape: tuple | None = None
    source_signature: tuple | None = None
    owner: object = None
    mapping_json: str = ''
    released: bool = False


class LocalDatasetRepository(DatasetStoreProvider):
    def __init__(self, root):
        """Linux/WSL only; native Windows is unsupported. OneDrive and other
        synchronized folders are not approved roots, including WSL /mnt/c paths
        into such folders. Use the WSL Linux filesystem or a local Linux volume.
        Root is an application-authorized private directory, never a source URL.

        Limits are store-wide for retained payload/key/bookkeeping and per-attempt
        for rows/batches/metadata. Aborted content remains private and consumes
        quotas; recovery never deletes files. Deployments must rotate/archive the
        store deliberately when full. SQLite main pages are capped, rollback
        journal can temporarily approach another catalog's worth of bytes, and
        the page cache target is 1 MiB. This is not a process-RSS or filesystem
        quota. Only one local writer is allowed, across processes; NFS/distributed
        filesystems and concurrent training reads are not supported here.
        """
        if fcntl is None or os.name != 'posix':
            raise StagingError('local staging requires Linux/WSL on an unsynchronized local filesystem')
        self.root = Path(root).absolute()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink() or self.root.stat().st_mode & 0o077:
            raise StagingError('staging root must be private and nonsymlink')
        self.db_path = self.root / 'catalog.sqlite'
        self.lock_path = self.root / 'writer.lock'

    @contextmanager
    def _connection(self, budgets):
        if self.db_path.is_symlink() or (self.db_path.exists() and not self.db_path.is_file()):
            raise StagingError('unsafe staging catalog')
        conn = sqlite3.connect(self.db_path, timeout=0)
        try:
            conn.execute('PRAGMA journal_mode=DELETE')
            conn.execute('PRAGMA synchronous=FULL')
            conn.execute('PRAGMA cache_size=-1024')
            conn.execute('PRAGMA temp_store=FILE')
            conn.execute('PRAGMA mmap_size=0')
            conn.execute(f'PRAGMA journal_size_limit={budgets.max_index_bytes}')
            pages = max(1, budgets.max_index_bytes // conn.execute('PRAGMA page_size').fetchone()[0])
            if conn.execute('PRAGMA page_count').fetchone()[0] > pages:
                raise StagingError('catalog budget exhausted')
            conn.execute(f'PRAGMA max_page_count={pages}')
            conn.executescript('CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, state TEXT NOT NULL, base TEXT NOT NULL, manifest TEXT);'
                'CREATE TABLE IF NOT EXISTS keys (attempt TEXT, kind TEXT, identity BLOB, PRIMARY KEY(attempt,kind,identity));'
                'CREATE TABLE IF NOT EXISTS chunks (attempt TEXT, ordinal INTEGER, rows INTEGER, raw_hash TEXT, canonical_hash TEXT, raw_bytes INTEGER, canonical_bytes INTEGER, envelope TEXT, PRIMARY KEY(attempt,ordinal));'
                'CREATE TABLE IF NOT EXISTS publications (version TEXT PRIMARY KEY, attempt TEXT UNIQUE, admission TEXT NOT NULL, digest TEXT NOT NULL);')
            with conn:
                yield conn
        finally:
            conn.close()

    def _begin(self, *, dataset_id, asset, mapping, budgets, consistency, owner=None):
        _validate_lifecycle_owner(owner, self)
        # OS lock covers independent repository instances/processes and recovery.
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise StagingError('unsafe staging lock')
        lock = os.fdopen(fd, 'rb')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            base = {'organization_id': str(asset.organization_id), 'dataset_id': str(dataset_id),
                'mapping_id': mapping.mapping_id, 'mapping_version': mapping.version,
                'mapping_digest': hashlib.sha256(_json(mapping.model_dump(mode='json'))).hexdigest(),
                'schema_version': '1.1', 'source_id': str(asset.source_id),
                'ingestion_batch_id': str(asset.ingestion_batch_id), 'consistency': asdict(consistency)}
            _provenance(base, budgets)
            _provenance(mapping.model_dump(mode='json'), budgets)
            if len(_json(base)) > budgets.max_metadata_bytes: raise StagingError('metadata budget exceeded')
            with self._connection(budgets) as conn:
                if conn.execute("SELECT COUNT(*) FROM attempts WHERE state NOT IN ('staging','sealed','aborted','finalized','rejected')").fetchone()[0]:
                    raise StagingError('unknown persisted staging state')
                # Exclusive writer means old STAGING attempts have no live owner.
                conn.execute("UPDATE attempts SET state='aborted' WHERE state='staging'")
                if conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0] >= budgets.max_attempts:
                    raise StagingError('attempt bookkeeping budget exceeded')
                totals = conn.execute('SELECT COALESCE(SUM(raw_bytes),0),COALESCE(SUM(canonical_bytes),0) FROM chunks').fetchone()
                if totals[0] >= budgets.max_raw_bytes or totals[1] >= budgets.max_canonical_bytes:
                    raise StagingError('store payload budget exhausted')
                attempt_id = uuid4()
                conn.execute('INSERT INTO attempts VALUES (?,?,?,NULL)', (str(attempt_id), 'staging', _json(base).decode()))
            (self.root / str(attempt_id)).mkdir(mode=0o700)
            return _Attempt(attempt_id, lock, budgets, base, owner=owner, mapping_json=mapping.model_dump_json())
        except BaseException:
            lock.close()
            raise

    def _assert_active(self, attempt, conn):
        if type(attempt) is not _Attempt or attempt.released:
            raise StagingError('invalid private staging writer')
        row = conn.execute('SELECT state FROM attempts WHERE id=?', (str(attempt.id),)).fetchone()
        if row != ('staging',): raise StagingError('invalid staging state transition')

    def _disk_totals(self):
        totals = [0, 0]
        for directory in self.root.iterdir():
            if directory.name in ('catalog.sqlite', 'catalog.sqlite-journal', 'writer.lock'):
                continue
            try:
                UUID(directory.name)
            except ValueError:
                raise StagingError('unexpected object in private staging root') from None
            if directory.is_symlink() or not directory.is_dir():
                raise StagingError('unsafe staging directory')
            for path in directory.iterdir():
                if path.is_symlink() or not path.is_file():
                    raise StagingError('unsafe staged payload')
                kind = 0 if path.name.endswith('-raw.parquet') else 1
                totals[kind] += path.stat().st_size
        return totals

    def _append(self, attempt, raw, canonical):
        b = attempt.budgets
        signature = (raw.asset.organization_id, raw.asset.source_id, raw.asset.ingestion_batch_id,
                     raw.source_dataset, raw.adapter_name, raw.adapter_version, raw.source_format)
        if str(signature[0]) != attempt.base['organization_id'] or str(signature[1]) != attempt.base['source_id'] or str(signature[2]) != attempt.base['ingestion_batch_id']:
            raise StagingError('source scope drift')
        if attempt.source_signature is not None and signature != attempt.source_signature:
            raise StagingError('source identity drift')
        _asset_provenance(raw.asset, _SOURCE_METADATA, b)
        _asset_provenance(canonical.dataset.asset, _CANONICAL_METADATA, b)
        if len(raw.records) != len(canonical.records) or raw.records.index.tolist() != canonical.records.index.tolist():
            raise StagingError('canonical row alignment mismatch')
        envelope = {'attempt_id': str(attempt.id), 'raw_asset': raw.asset.model_dump(mode='json'),
                    'canonical_dataset': canonical.dataset.model_dump(mode='json'),
                    'record_ids': list(raw.record_ids), 'source_dataset': raw.source_dataset,
                    'adapter_name': raw.adapter_name, 'adapter_version': raw.adapter_version,
                    'source_format': raw.source_format.value, 'batch_id': raw.batch_id}
        _provenance(envelope, b)
        envelope_bytes = _json(envelope)
        if len(envelope_bytes) > b.max_metadata_bytes:
            raise StagingError('metadata budget exceeded')
        raw_table, canonical_table = _table(raw.records, b), _table(canonical.records, b)
        def logical_schema(frame):
            families = []
            for dtype in frame.dtypes:
                if pd.api.types.is_bool_dtype(dtype): family = 'boolean'
                elif pd.api.types.is_numeric_dtype(dtype): family = 'numeric'
                else: family = _json(_dtype_spec(dtype, b)).decode()
                families.append(family)
            return tuple(frame.columns), tuple(families)
        shapes = [logical_schema(raw.records), logical_schema(canonical.records)]
        envelope_digest = hashlib.sha256(envelope_bytes).hexdigest().encode()
        raw_table = raw_table.replace_schema_metadata({**raw_table.schema.metadata, b'envelope_sha256': envelope_digest})
        canonical_table = canonical_table.replace_schema_metadata({**canonical_table.schema.metadata, b'envelope_sha256': envelope_digest})
        # Source numeric representations may differ by batch (e.g. Excel int/float).
        # Preserve exact per-chunk dtypes; require stable column roles/type families.
        for prior, shape in zip((attempt.source_shape, attempt.canonical_shape), shapes):
            if prior is not None and prior != shape: raise StagingError('staging schema drift')
        payloads = []
        for table in (raw_table, canonical_table):
            sink = pa.BufferOutputStream(); pq.write_table(table, sink, compression='NONE')
            payload = sink.getvalue().to_pybytes()
            if len(payload) > b.max_chunk_bytes: raise StagingError('encoded chunk budget exceeded')
            original = raw.records if len(payloads) == 0 else canonical.records
            _verify_original(original, _frame(pq.read_table(pa.BufferReader(payload))), b)
            payloads.append(payload)
        with self._connection(b) as conn:
            self._assert_active(attempt, conn)
            n, rows, metadata_size = conn.execute('SELECT COUNT(*),COALESCE(SUM(rows),0),COALESCE(SUM(length(CAST(envelope AS BLOB))),0) FROM chunks WHERE attempt=?', (str(attempt.id),)).fetchone()
            if n >= b.max_batches or rows + len(raw.records) > b.max_rows or metadata_size + len(envelope_bytes) > b.max_metadata_bytes:
                raise StagingError('staging count or metadata budget exceeded')
            totals = self._disk_totals()
            if totals[0]+len(payloads[0]) > b.max_raw_bytes or totals[1]+len(payloads[1]) > b.max_canonical_bytes:
                raise StagingError('store payload byte budget exceeded')
            key_count = conn.execute('SELECT COUNT(*) FROM keys').fetchone()[0]
            if key_count + 2*len(raw.records) > b.max_unique_keys: raise StagingError('key budget exceeded')
            # Structured typed fields; metric is fixed for this one-mapping attempt.
            source_keys = [_json(['source', str(raw.asset.source_id), str(raw.asset.ingestion_batch_id), raw.source_dataset, v]) for v in raw.record_ids]
            canonical_keys = [_json(['canonical', str(s), p.isoformat()]) for s,p in zip(canonical.records.series_id,canonical.records.period_start)]
            try:
                conn.executemany('INSERT INTO keys VALUES (?,?,?)', [(str(attempt.id),kind,k) for kind,ks in [('source',source_keys),('canonical',canonical_keys)] for k in ks])
            except sqlite3.IntegrityError:
                raise StagingError('duplicate staged source or canonical key') from None
            directory = self.root / str(attempt.id)
            for kind, payload in zip(('raw','canonical'), payloads):
                path = directory / f'{n}-{kind}.parquet'
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd,'wb') as stream:
                    stream.write(payload); stream.flush(); os.fsync(stream.fileno())
                path.chmod(0o400)
            conn.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?)', (str(attempt.id),n,len(raw.records),
                hashlib.sha256(payloads[0]).hexdigest(),hashlib.sha256(payloads[1]).hexdigest(),
                len(payloads[0]),len(payloads[1]),envelope_bytes.decode()))
        attempt.source_signature = signature
        attempt.source_shape, attempt.canonical_shape = shapes

    def _validate_envelope(self, attempt, serialized, frames, rows):
        """Revalidate contracts and compare lineage/records to the stored raw batch.

        A checksum binds the envelope to each payload. This is storage integrity,
        not authenticity against an administrator able to rewrite all artifacts.
        """
        from src.data.adapters.base import SourceBatch
        from src.data.adapters.json_adapter import _unique_object, _invalid_constant
        from src.data.processing.canonical_mapper import CanonicalMapper
        try:
            envelope = json.loads(serialized, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
            required = {'attempt_id', 'raw_asset', 'canonical_dataset', 'record_ids',
                        'source_dataset', 'adapter_name', 'adapter_version', 'source_format', 'batch_id'}
            if type(envelope) is not dict or set(envelope) != required or envelope['attempt_id'] != str(attempt.id):
                raise StagingError('staged lineage attempt mismatch')
            _provenance(envelope, attempt.budgets)
            raw_asset = AssetMetadata.model_validate(envelope['raw_asset'])
            dataset = StructuredDataset.model_validate(envelope['canonical_dataset'])
            _asset_provenance(raw_asset, _SOURCE_METADATA, attempt.budgets)
            _asset_provenance(dataset.asset, _CANONICAL_METADATA, attempt.budgets)
            for asset in (raw_asset, dataset.asset):
                if (str(asset.organization_id) != attempt.base['organization_id']
                        or str(asset.source_id) != attempt.base['source_id']
                        or str(asset.ingestion_batch_id) != attempt.base['ingestion_batch_id']):
                    raise StagingError('staged lineage scope mismatch')
            mapping = SourceMapping.model_validate_json(attempt.mapping_json)
            if (mapping.mapping_id != attempt.base['mapping_id'] or mapping.version != attempt.base['mapping_version']
                    or hashlib.sha256(_json(mapping.model_dump(mode='json'))).hexdigest() != attempt.base['mapping_digest']):
                raise StagingError('staged mapping identity mismatch')
            ids = envelope['record_ids']
            validate_record_ids(ids)
            if len(ids) != rows or len(set(ids)) != rows:
                raise StagingError('staged source record alignment mismatch')
            raw = SourceBatch(frames['raw'], tuple(ids), raw_asset, envelope['source_dataset'],
                              dataset.source_format.__class__(envelope['source_format']),
                              envelope['adapter_name'], envelope['adapter_version'], envelope['batch_id'])
            expected = CanonicalMapper(mapping).map_batch(raw)
            if expected.dataset.model_dump(mode='json') != dataset.model_dump(mode='json'):
                raise StagingError('staged canonical lineage mismatch')
            _verify_original(expected.records, frames['canonical'], attempt.budgets)
        except Exception:
            raise StagingError('staged lineage envelope validation failed') from None

    def _verified_chunk(self, attempt, chunk, checkpoint):
        ordinal,rows,rh,ch,rb,cb,envelope = chunk
        checkpoint()
        frames = {}
        for kind,digest,size in [('raw',rh,rb),('canonical',ch,cb)]:
            path = self.root / str(attempt.id) / f'{ordinal}-{kind}.parquet'
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd,'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or os.fstat(stream.fileno()).st_size != size:
                    raise StagingError('staged chunk size mismatch')
                payload=stream.read(attempt.budgets.max_chunk_bytes+1)
            if hashlib.sha256(payload).hexdigest()!=digest: raise StagingError('staged chunk checksum mismatch')
            table=pq.read_table(pa.BufferReader(payload))
            if table.num_rows!=rows: raise StagingError('staged row count mismatch')
            if table.schema.metadata.get(b'envelope_sha256') != hashlib.sha256(envelope.encode('utf-8')).hexdigest().encode():
                raise StagingError('staged lineage envelope checksum mismatch')
            restored=_frame(table)
            frames[kind] = restored
            verified = _table(restored,attempt.budgets)
            verified = verified.replace_schema_metadata({**verified.schema.metadata, b'envelope_sha256': table.schema.metadata[b'envelope_sha256']})
            if not verified.equals(table,check_metadata=True):
                raise StagingError('storage round-trip changes values')
        self._validate_envelope(attempt, envelope, frames, rows)
        return frames, payload

    def _seal(self, attempt, *, owner=None, checkpoint=lambda: None):
        if type(attempt) is not _Attempt or owner is not attempt.owner:
            raise StagingError('completion owner mismatch')
        _validate_lifecycle_owner(owner, self)
        with self._connection(attempt.budgets) as conn:
            self._assert_active(attempt, conn)
            owner._claim_completion(attempt)
            chunks = conn.execute('SELECT ordinal,rows,raw_hash,canonical_hash,raw_bytes,canonical_bytes,envelope FROM chunks WHERE attempt=? ORDER BY ordinal',(str(attempt.id),)).fetchall()
            if not chunks: raise StagingError('empty dataset cannot seal')
            for ordinal,rows,rh,ch,rb,cb,envelope in chunks:
                self._verified_chunk(attempt, (ordinal,rows,rh,ch,rb,cb,envelope), checkpoint)
            rows=sum(c[1] for c in chunks)
            if conn.execute('SELECT COUNT(*) FROM keys WHERE attempt=?',(str(attempt.id),)).fetchone()[0] != 2*rows:
                raise StagingError('staged key count mismatch')
            data={**attempt.base,'attempt_id':str(attempt.id),'state':'sealed','rows':rows,'batches':len(chunks),
                'raw_bytes':sum(c[4] for c in chunks),'canonical_bytes':sum(c[5] for c in chunks),
                'envelopes':[c[6] for c in chunks], 'chunks':[[c[0],c[2],c[3],c[1]] for c in chunks]}
            data['version_id']=hashlib.sha256(_json(data)).hexdigest()
            manifest = self._manifest(data)
            manifest_bytes = _json(data)
            if len(manifest_bytes) > attempt.budgets.max_metadata_bytes:
                raise StagingError('sealed manifest metadata budget exceeded')
            directory_fd = os.open(self.root / str(attempt.id), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            checkpoint()
            conn.execute("UPDATE attempts SET state='sealed',manifest=? WHERE id=?",(manifest_bytes.decode(),str(attempt.id)))
            (self.root / str(attempt.id)).chmod(0o500)
        return manifest

    @staticmethod
    def _manifest(data):
        d=dict(data)
        for name in ('organization_id','dataset_id','attempt_id','source_id','ingestion_batch_id'):d[name]=UUID(d[name])
        d['state']=StagingState(d['state']);d['consistency']=ConsistencyEvidence(**d['consistency'])
        d['envelopes']=tuple(d['envelopes']);d['chunks']=tuple(tuple(c) for c in d['chunks'])
        return DatasetManifest(**d)

    def _abort(self, attempt):
        with self._connection(attempt.budgets) as conn:
            conn.execute("UPDATE attempts SET state='aborted' WHERE id=? AND state='staging'",(str(attempt.id),))
        # Retain private diagnostics/payload for bounded forensic inspection. No
        # deletion API can accidentally traverse unrelated production artifacts.

    def _release(self, attempt):
        if not attempt.released:
            attempt.released=True
            attempt.lock.close()

    @contextmanager
    def _publication_session(self, budgets):
        """Single-host exclusive lock also protects verification and read snapshots.

        All cooperative staging/finalization/read operations use the same lock.
        An administrator with arbitrary filesystem writes remains outside trust.
        """
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        stream = os.fdopen(fd,'rb')
        try:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode): raise StagingError('unsafe publication lock')
            fcntl.flock(stream,fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        except StagingError:
            raise
        except OSError:
            raise StagingError('dataset store operation failed') from None
        finally:
            stream.close()

    def _candidate(self, reference, budgets):
        """Resolve exact opaque scope; verify immutable SEALED manifest binding."""
        from src.data.contracts.dataset import StoredDatasetReference
        if type(reference) is not StoredDatasetReference: raise StagingError('invalid dataset reference')
        with self._connection(budgets) as conn:
            row=conn.execute('SELECT state,base,manifest FROM attempts WHERE id=?',(str(reference.attempt_id),)).fetchone()
            if row is None or row[0] not in ('sealed','finalized'): raise StagingError('dataset is not an eligible sealed candidate')
            if row[2] is None or len(row[2].encode()) > budgets.max_metadata_bytes: raise StagingError('missing or oversized capture manifest')
            try:
                from src.data.adapters.json_adapter import _unique_object
                data=json.loads(row[2],object_pairs_hook=_unique_object)
                version=data.pop('version_id')
                if hashlib.sha256(_json(data)).hexdigest()!=version: raise ValueError()
                data['version_id']=version
                manifest=self._manifest(data)
                if (manifest.state is not StagingState.SEALED or manifest.organization_id!=reference.organization_id
                        or manifest.dataset_id!=reference.dataset_id or manifest.attempt_id!=reference.attempt_id or version!=reference.version_id):
                    raise ValueError()
                base=json.loads(row[1],object_pairs_hook=_unique_object)
                if any(data.get(k)!=v for k,v in base.items()): raise ValueError()
                _provenance(data,budgets)
                chunks=conn.execute('SELECT ordinal,rows,raw_hash,canonical_hash,raw_bytes,canonical_bytes,envelope FROM chunks WHERE attempt=? ORDER BY ordinal',(str(reference.attempt_id),)).fetchall()
                if (len(chunks)!=manifest.batches or [c[0] for c in chunks]!=list(range(manifest.batches))
                        or sum(c[1] for c in chunks)!=manifest.rows or sum(c[4] for c in chunks)!=manifest.raw_bytes
                        or sum(c[5] for c in chunks)!=manifest.canonical_bytes
                        or tuple(c[6] for c in chunks)!=manifest.envelopes
                        or tuple((c[0],c[2],c[3],c[1]) for c in chunks)!=manifest.chunks):raise ValueError()
                if manifest.rows > budgets.max_rows or manifest.batches > budgets.max_batches or manifest.raw_bytes > budgets.max_raw_bytes or manifest.canonical_bytes > budgets.max_canonical_bytes:
                    raise ValueError()
                if conn.execute('SELECT COUNT(*) FROM keys WHERE attempt=?',(str(reference.attempt_id),)).fetchone()[0]!=2*manifest.rows:raise ValueError()
            except Exception:
                raise StagingError('capture manifest or content binding failed') from None
        return SimpleNamespace(reference=reference,manifest=manifest,base=base,chunks=chunks,budgets=budgets,
                               state=row[0],mapping_json='',id=reference.attempt_id)

    def _verified_batches(self, candidate, checkpoint):
        if not candidate.mapping_json: raise StagingError('verified mapping configuration required')
        for chunk in candidate.chunks:
            frames,_=self._verified_chunk(candidate,chunk,checkpoint)
            yield frames['canonical']

    @contextmanager
    def _quality_key_index(self, budgets):
        """Bounded SQLite TEMP B-tree, never a whole-stream Python key set."""
        with self._connection(budgets) as conn:
            pages=max(1,budgets.max_index_bytes//conn.execute('PRAGMA page_size').fetchone()[0])
            conn.execute(f'PRAGMA temp.max_page_count={pages}')
            conn.execute('CREATE TEMP TABLE dq_keys(kind TEXT, identity TEXT, occurrences INTEGER, PRIMARY KEY(kind,identity))')
            unique_count=0
            def record(kind,identity):
                nonlocal unique_count
                row=conn.execute('SELECT occurrences FROM dq_keys WHERE kind=? AND identity=?',(kind,identity)).fetchone()
                if row:
                    conn.execute('UPDATE dq_keys SET occurrences=occurrences+1 WHERE kind=? AND identity=?',(kind,identity))
                    return 2 if row[0]==1 else 1
                if unique_count>=budgets.max_unique_keys:
                    raise StagingError('dataset quality key budget exceeded')
                conn.execute('INSERT INTO dq_keys VALUES (?,?,1)',(kind,identity))
                unique_count+=1
                return 0
            yield record

    def _publication(self, reference, budgets):
        with self._connection(budgets) as conn:
            row=conn.execute('SELECT admission,digest FROM publications WHERE version=? AND attempt=?',
                             (reference.version_id,str(reference.attempt_id))).fetchone()
            if row is None:return None
            if len(row[0].encode()) > budgets.max_metadata_bytes or hashlib.sha256(row[0].encode()).hexdigest()!=row[1]:
                raise StagingError('publication evidence integrity failed')
            return row[0]

    def _publish(self, candidate, admission, owner, checkpoint):
        from src.providers.dataset_store_provider import _validate_finalization_owner
        _validate_finalization_owner(owner,self)
        if type(admission) is not str or len(admission.encode())>candidate.budgets.max_metadata_bytes:
            raise StagingError('invalid bounded admission evidence')
        _provenance(json.loads(admission),candidate.budgets)
        digest=hashlib.sha256(admission.encode()).hexdigest()
        fresh=self._candidate(candidate.reference,candidate.budgets)
        if fresh.state!='sealed':raise StagingError('invalid publication state transition')
        fresh.mapping_json=candidate.mapping_json
        for _ in self._verified_batches(fresh,checkpoint):pass
        owner._claim_publication(candidate.reference,digest)
        checkpoint()
        with self._connection(candidate.budgets) as conn:
            row=conn.execute('SELECT state,manifest FROM attempts WHERE id=?',(str(candidate.id),)).fetchone()
            if row is None or row[0]!='sealed' or json.loads(row[1]).get('version_id')!=candidate.reference.version_id:
                raise StagingError('invalid publication state transition')
            if conn.execute('SELECT 1 FROM publications WHERE version=?',(candidate.reference.version_id,)).fetchone():
                raise StagingError('immutable publication already exists')
            checkpoint()
            conn.execute('INSERT INTO publications VALUES (?,?,?,?)',(candidate.reference.version_id,str(candidate.id),admission,digest))
            conn.execute("UPDATE attempts SET state='finalized' WHERE id=? AND state='sealed'",(str(candidate.id),))
        return candidate.reference

    def _reject(self, reference, budgets, owner):
        from src.providers.dataset_store_provider import _validate_finalization_owner
        _validate_finalization_owner(owner,self)
        owner._claim_rejection(reference)
        with self._connection(budgets) as conn:
            row=conn.execute('SELECT state,manifest FROM attempts WHERE id=?',(str(reference.attempt_id),)).fetchone()
            if row is None or row[0]!='sealed':return
            # Only a matching capture reference may change a candidate's status.
            data=json.loads(row[1])
            if data.get('version_id')!=reference.version_id or data.get('organization_id')!=str(reference.organization_id) or data.get('dataset_id')!=str(reference.dataset_id):
                raise StagingError('rejection scope mismatch')
            conn.execute("UPDATE attempts SET state='rejected' WHERE id=? AND state='sealed'",(str(reference.attempt_id),))

    @contextmanager
    def _read_snapshot(self, candidate, checkpoint):
        """Verify ALL chunks before visibility; copy bounded canonical bytes privately.

        Original files may be altered after verification without changing this
        consumer's snapshot. One reader at a time shares the writer lock. Abrupt
        process death can leave a quota-consuming private UUID snapshot directory;
        retention/rotation remains an explicit operational responsibility.
        """
        directory=self.root/str(uuid4());directory.mkdir(mode=0o700)
        paths=[];total=0
        try:
            existing=self._disk_totals()[1]
            if existing+candidate.manifest.canonical_bytes > 2*candidate.budgets.max_canonical_bytes:
                raise StagingError('retained read snapshot budget exhausted')
            for chunk in candidate.chunks:
                frames,payload=self._verified_chunk(candidate,chunk,checkpoint)
                total+=len(payload)
                if total>candidate.budgets.max_canonical_bytes:raise StagingError('read snapshot budget exceeded')
                path=directory/f'{chunk[0]}-canonical.parquet'
                fd=os.open(path,os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'wb') as stream:
                    stream.write(payload);stream.flush();os.fsync(stream.fileno())
                path.chmod(0o400);paths.append(path)
            checkpoint()
            def batches():
                for path in paths:
                    checkpoint()
                    # Only this repository-owned snapshot, never an external path.
                    with open(path,'rb') as stream:body=stream.read(candidate.budgets.max_chunk_bytes+1)
                    yield _frame(pq.read_table(pa.BufferReader(body)))
            yield batches
        finally:
            # Delete only the just-created private snapshot, never dataset chunks.
            for path in paths:
                path.unlink(missing_ok=True)
            # An unregistered partial file on failure is still repository-owned.
            for path in directory.iterdir():
                if path.is_file() and not path.is_symlink():path.unlink()
            directory.rmdir()
