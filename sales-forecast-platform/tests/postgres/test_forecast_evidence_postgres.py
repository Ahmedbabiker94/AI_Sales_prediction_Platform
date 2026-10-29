"""Real isolated PostgreSQL constraints, transactions, races and read-only checks."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import struct
from threading import Barrier
from uuid import uuid4

import pytest
from conftest import admin, target, make_batch, repository, provider, digest, DATABASE
from src.data.contracts.forecast_evidence_storage import (
    CanonicalForecastStorageBatch, ForecastEvidenceRow, ForecastLogicalKey, StoredForecastBatch,
    ForecastStorageScope, ForecastStorageReceipt,
)
from src.data.contracts.dataset import operational_json
from src.database.offline_forecast_postgres import (
    OfflineForecastPostgres, ForecastStorageError, ForecastStorageRejected, ForecastStorageConflict,
    ForecastStorageUnavailable, ForecastStorageRetryable, AmbiguousForecastCommit,
    apply_forecast_evidence_migration, MIGRATION_SHA256, MIGRATION, driver,
)
from src.repositories.postgres_forecast_evidence_repository import _PostgresForecastEvidenceRepository


pytestmark = pytest.mark.usefixtures('migrated')


def clone(value, **changes):
    return type(value).model_validate_json(value.model_copy(update=changes).canonical_json)


def raw_counts(batch):
    with admin() as connection, connection.cursor() as cursor:
        out = []
        for table in ('batches', 'evidence', 'current_records'):
            cursor.execute('SELECT count(*) FROM canonical_forecast.'+table+' WHERE organization_id=%s AND dataset_id=%s',
                (str(batch.scope.organization_id), str(batch.scope.dataset_id)))
            out.append(cursor.fetchone()[0])
        return tuple(out)


def test_explicit_migration_tables_indexes_checks_and_roles(migrated):
    assert hashlib.sha256(MIGRATION.read_bytes()).hexdigest() == MIGRATION_SHA256
    with admin() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='canonical_forecast' ORDER BY tablename")
        assert [r[0] for r in cursor.fetchall()] == ['batches', 'current_records', 'evidence', 'schema_migrations']
        cursor.execute("SELECT count(*) FROM pg_indexes WHERE schemaname='canonical_forecast'")
        assert cursor.fetchone()[0] >= 8
        cursor.execute("SELECT condeferrable,condeferred FROM pg_constraint WHERE contype='t' AND connamespace='canonical_forecast'::regnamespace")
        assert cursor.fetchall() == [(True, True)]*3
        cursor.execute("SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname LIKE 'r7_forecast_%'")
        assert all(not any(r[1:]) for r in cursor.fetchall())


@pytest.mark.parametrize('family', ['xgboost', 'lightgbm', 'lstm', 'gru', 'transformer'])
def test_initial_commit_readback_exact_replay_and_non_authoritative_receipt(family):
    batch = make_batch(family=family)
    store = provider(batch)
    assert store._lookup(batch.idempotency_id) is None
    committed = store._append_initial(batch)
    assert committed.batch.canonical_bytes == batch.canonical_bytes
    assert committed.receipt.classification == 'DURABLE_STORAGE_ONLY_NOT_PRODUCER_APPROVAL'
    assert not committed.receipt.producer_verified and not committed.receipt.write_authority_verified
    assert not committed.receipt.production_permission
    assert store._lookup(batch.idempotency_id, expected=batch) == committed
    assert store._append_initial(batch).receipt == committed.receipt
    assert raw_counts(batch) == (1, 4, 4)


def test_float32_bytes_signed_zero_subnormal_and_max_finite():
    bits = ('2d56f943', 'e9542244', '00000080', '01000000', 'ffff7f7f')
    values = [struct.unpack('<f', bytes.fromhex(x))[0] for x in bits]
    batch = make_batch(count=5, values=values)
    repository(batch)._append_initial(batch)
    with admin() as connection, connection.cursor() as cursor:
        cursor.execute('SELECT encode(float32_le,\'hex\'), row_sha256, row_canonical FROM canonical_forecast.evidence '
            'WHERE organization_id=%s ORDER BY ordinal', (str(batch.scope.organization_id),))
        rows = cursor.fetchall()
        assert [r[0] for r in rows] == list(bits)
        assert [r[1] for r in rows] == [hashlib.sha256(r[2].encode()).hexdigest() for r in rows]
    assert struct.pack('<f', -0.).hex() == rows[2][0]


@pytest.mark.parametrize('field', ['publication_id', 'artifact_sha256', 'snapshot_id', 'owner_reference', 'execution_id'])
def test_conflicting_idempotency_content_rejects_without_mutation(field):
    batch = make_batch()
    store = repository(batch)
    original = store._append_initial(batch)
    changed = clone(batch, rows=tuple(clone(row, lineage=clone(row.lineage, **{field: 'f'*64})) for row in batch.rows))
    assert [r.key.logical_key_id for r in changed.rows] == [r.key.logical_key_id for r in batch.rows]
    with pytest.raises(ForecastStorageConflict): store._append_initial(changed)
    with pytest.raises(ForecastStorageConflict): store._lookup(batch.idempotency_id, expected=changed)
    assert store._lookup(batch.idempotency_id) == original
    assert raw_counts(batch) == (1, 4, 4)


def test_different_idempotency_same_logical_keys_conflicts():
    batch = make_batch(); store = repository(batch)
    store._append_initial(batch)
    other = clone(batch, idempotency_id='f'*64)
    with pytest.raises(ForecastStorageConflict): store._append_initial(other)
    assert store._lookup(other.idempotency_id) is None and raw_counts(batch) == (1, 4, 4)


@pytest.mark.parametrize('same_id', [True, False])
def test_concurrent_first_writers_real_unique_constraints(same_id):
    batch = make_batch()
    other = batch if same_id else clone(batch, idempotency_id=digest('concurrent-other'))
    barrier = Barrier(2)
    def write(value):
        barrier.wait(timeout=3)
        try:
            return repository(value)._append_initial(value)
        except ForecastStorageConflict:
            return 'CONFLICT'
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(write, (batch, other)))
    if same_id:
        assert first.receipt == second.receipt
    else:
        assert sum(x == 'CONFLICT' for x in (first, second)) == 1
    assert raw_counts(batch) == (1, 4, 4)


@pytest.mark.parametrize('checkpoint', ['second_row', 'before_commit'])
def test_injected_failure_rolls_back_entire_batch(monkeypatch, checkpoint):
    batch = make_batch(); store = repository(batch)
    if checkpoint == 'second_row':
        original = _PostgresForecastEvidenceRepository._insert_row
        def insert(self, cursor, records, ordinal):
            if ordinal == 1: raise RuntimeError('injected')
            original(self, cursor, records, ordinal)
        monkeypatch.setattr(_PostgresForecastEvidenceRepository, '_insert_row', insert)
    else:
        def fail(self, connection): raise RuntimeError('injected')
        monkeypatch.setattr(_PostgresForecastEvidenceRepository, '_before_commit', fail)
    with pytest.raises(ForecastStorageUnavailable): store._append_initial(batch)
    assert raw_counts(batch) == (0, 0, 0) and store._lookup(batch.idempotency_id) is None


def test_database_independently_rejects_partial_batch_at_commit():
    batch = make_batch(); store = repository(batch)
    with target('writer')._open(batch.scope) as connection:
        with connection.cursor() as cursor:
            assert store._insert_header(cursor, batch)
            store._insert_row(cursor, batch, 0)
        with pytest.raises(driver().IntegrityError) as caught: connection.commit()
        assert caught.value.pgcode == '23514'
    assert raw_counts(batch) == (0, 0, 0)


def test_ambiguous_commit_ack_lookup_then_identical_retry(monkeypatch):
    batch = make_batch(); store = repository(batch)
    original = OfflineForecastPostgres._open
    class AckLost:
        def __init__(self, connection): self.connection = connection
        def cursor(self): return self.connection.cursor()
        def commit(self):
            self.connection.commit()
            raise driver().OperationalError('synthetic acknowledgement loss AFTER real commit')
    @contextmanager
    def open_with_ack_loss(self, scope=None):
        with original(self, scope) as connection:
            yield AckLost(connection) if self.access == 'writer' else connection
    with monkeypatch.context() as local:
        local.setattr(OfflineForecastPostgres, '_open', open_with_ack_loss)
        with pytest.raises(AmbiguousForecastCommit): store._append_initial(batch)
    found = store._lookup(batch.idempotency_id, expected=batch)
    assert found is not None and raw_counts(batch) == (1, 4, 4)
    assert store._append_initial(batch).receipt == found.receipt


def test_serialization_error_never_automatically_retries(monkeypatch):
    batch = make_batch(); calls = []
    class Aborted(Exception): pgcode = '40001'
    def fail(self, connection):
        calls.append(True)
        raise Aborted()
    with monkeypatch.context() as local:
        local.setattr(_PostgresForecastEvidenceRepository, '_before_commit', fail)
        with pytest.raises(ForecastStorageRetryable): repository(batch)._append_initial(batch)
    assert calls == [True] and raw_counts(batch) == (0, 0, 0)
    assert repository(batch)._append_initial(batch) is not None


@pytest.mark.parametrize('operation', ['UPDATE', 'DELETE', 'TRUNCATE'])
@pytest.mark.parametrize('table', ['batches', 'evidence', 'current_records', 'schema_migrations'])
def test_database_immutable_even_for_test_database_admin(operation, table):
    sql = (('UPDATE canonical_forecast.'+table+' SET '+('checksum=checksum' if table == 'schema_migrations' else 'dataset_id=dataset_id'))
        if operation == 'UPDATE' else operation+(' FROM ' if operation == 'DELETE' else ' ')+ 'canonical_forecast.'+table)
    if operation == 'TRUNCATE': sql += ' CASCADE'
    with admin() as connection, connection.cursor() as cursor:
        with pytest.raises(driver().IntegrityError) as caught: cursor.execute(sql)
        assert caught.value.pgcode == '23514'


def test_readonly_verification_no_writes_and_no_role_privilege_escape():
    batch = make_batch(); store = repository(batch); store._append_initial(batch)
    before = raw_counts(batch)
    for _ in range(3): assert store._lookup(batch.idempotency_id, expected=batch) is not None
    with target('reader')._open(batch.scope) as connection, connection.cursor() as cursor:
        cursor.execute('SHOW transaction_read_only'); assert cursor.fetchone() == ('on',)
        with pytest.raises(driver().Error): cursor.execute('CREATE TEMP TABLE must_not_exist(x int)')
    # Even if a host turns READ ONLY off, the reader role has no DML/DDL/TEMP.
    with target('reader')._open(batch.scope) as connection:
        connection.rollback(); connection.set_session(readonly=False)
        with connection.cursor() as cursor:
            with pytest.raises(driver().Error): cursor.execute('INSERT INTO canonical_forecast.schema_migrations VALUES (1,%s,now())', ('0'*64,))
    assert raw_counts(batch) == before


@pytest.mark.parametrize('scope_change', ['organization_id', 'dataset_id', 'series_ids'])
def test_cross_scope_lookup_and_write_fail_closed(scope_change):
    batch = make_batch(); store = repository(batch); store._append_initial(batch)
    changed = clone(batch.scope, **{scope_change: (uuid4(),) if scope_change == 'series_ids' else uuid4()})
    foreign = _PostgresForecastEvidenceRepository(target('reader'), target('writer'), changed)
    assert foreign._lookup(batch.idempotency_id) is None
    with pytest.raises(ForecastStorageRejected): foreign._append_initial(batch)
    with target('writer')._open(changed) as connection, connection.cursor() as cursor:
        with pytest.raises(driver().Error): store._insert_header(cursor, clone(batch, idempotency_id='e'*64))
    assert raw_counts(batch) == (1, 4, 4)


def test_database_cas_and_correction_constraints_without_application_assertions():
    batch = make_batch(); store = repository(batch); saved = store._append_initial(batch)
    key = batch.rows[0].key
    with admin() as connection, connection.cursor() as cursor:
        with pytest.raises(driver().Error):
            cursor.execute('INSERT INTO canonical_forecast.current_records '
                '(organization_id,dataset_id,logical_key_id,current_revision,idempotency_id,series_id,row_sha256) '
                'VALUES (%s,%s,%s,2,%s,%s,%s)', (str(key.organization_id), str(key.dataset_id), key.logical_key_id,
                batch.idempotency_id, str(key.series_id), batch.rows[0].row_sha256))
    assert store._lookup(batch.idempotency_id) == saved


@pytest.mark.parametrize('bits', ['0000807f', '000080ff', '0100807f'])
def test_database_rejects_nonfinite_bytes_independently(bits):
    batch = make_batch()
    bad = batch.model_copy(update={'rows': (batch.rows[0].model_copy(update={'float32_le': bits}),)+batch.rows[1:]})
    store = repository(batch)
    with target('writer')._open(batch.scope) as connection, connection.cursor() as cursor:
        store._insert_header(cursor, bad)
        with pytest.raises(driver().Error): store._insert_row(cursor, bad, 0)
    assert raw_counts(batch) == (0, 0, 0)


def test_readonly_missing_database_does_not_create_one():
    batch = make_batch(); missing_id = uuid4()
    target_missing = replace(target('reader'), instance_id=missing_id, database='r7_05b3_'+missing_id.hex)
    with pytest.raises(ForecastStorageUnavailable):
        with target_missing._open(batch.scope): pass
    with admin() as connection, connection.cursor() as cursor:
        cursor.execute('SELECT datname FROM pg_database WHERE datname=%s', (target_missing.database,))
        assert cursor.fetchone() is None


@pytest.mark.parametrize('field,value', [('horizon_step', 2), ('horizon_step', True), ('family', ''), ('candidate_id', 'bad')])
def test_invalid_logical_identity(field, value):
    key = make_batch().rows[0].key
    with pytest.raises(ValueError): clone(key, **{field: value})


@pytest.mark.parametrize('field,value', [('revision', 2), ('revision', True), ('expected_current_version', 1),
    ('expected_current_version', False), ('physical_dtype', 'float64'), ('float32_le', '0000807f'), ('float32_le', 'xyz')])
def test_invalid_revision_cas_and_dtype(field, value):
    with pytest.raises(ValueError): clone(make_batch().rows[0], **{field: value})


def test_duplicate_keys_and_incomplete_scope_rejected_before_database():
    batch = make_batch()
    for rows in ((batch.rows[0],)*4, batch.rows[:-1]):
        with pytest.raises(ValueError): clone(batch, rows=rows)
    assert raw_counts(batch) == (0, 0, 0)


def test_deterministic_encoding_original_order_and_accepted_key_semantics():
    batch = make_batch(); parsed = CanonicalForecastStorageBatch.model_validate_json(batch.canonical_json)
    assert parsed.canonical_bytes == batch.canonical_bytes
    row = batch.rows[0]; key = row.key
    accepted_preimage = ['canonical_candidate_h1_forecast_key_v1', str(key.organization_id), str(key.dataset_id),
        str(key.series_id), key.candidate_id, key.family, key.forecast_origin.isoformat(), key.target_period.isoformat(),
        key.target_period_end.isoformat(), 1, key.target_metric, json.loads(key.measurement_json), json.loads(key.temporal_json), 'identity_target_v1']
    assert key.logical_key_id == hashlib.sha256(operational_json(accepted_preimage).encode()).hexdigest()
    changed = clone(batch, rows=tuple(reversed(batch.rows)))
    assert changed.content_sha256 != batch.content_sha256
    assert set(r.key.logical_key_id for r in changed.rows) == set(r.key.logical_key_id for r in batch.rows)
    repository(batch)._append_initial(batch)
    with pytest.raises(ForecastStorageConflict): repository(batch)._append_initial(changed)


def test_closed_contract_and_forged_authority_cannot_upgrade_storage():
    batch = make_batch()
    for field in ('producer_verified', 'write_authority', 'receipt', 'execution_grant'):
        data = batch.model_dump(mode='json'); data[field] = {'forged': True}
        with pytest.raises(ValueError): CanonicalForecastStorageBatch.model_validate_json(json.dumps(data))
    for value in (object(), batch.model_dump(mode='json')):
        with pytest.raises(ForecastStorageRejected): repository(batch)._append_initial(value)
    saved = repository(batch)._append_initial(batch)
    for field in ('producer_verified', 'write_authority_verified', 'production_permission'):
        with pytest.raises(ValueError): clone(saved.receipt, **{field: True})


def test_constructor_and_inherited_database_url_cannot_connect_or_create(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://production.invalid/do-not-connect')
    batch = make_batch()
    def forbidden(*args, **kwargs): raise AssertionError('constructor connected')
    with monkeypatch.context() as local:
        local.setattr(OfflineForecastPostgres, '_open', forbidden)
        repository(batch); provider(batch)
        with pytest.raises(ForecastStorageRejected): replace(target('reader'), allow_disposable=False)
        with pytest.raises(ForecastStorageRejected): replace(target('reader'), socket_directory='/production')
    assert repository(batch)._lookup(batch.idempotency_id) is None


def test_checksum_mismatch_never_applies_migration(monkeypatch):
    with monkeypatch.context() as local:
        local.setattr('src.database.offline_forecast_postgres.MIGRATION_SHA256', 'f'*64)
        with pytest.raises(ForecastStorageUnavailable): apply_forecast_evidence_migration(target('migrator'))


def test_no_business_service_sql_or_operational_activation():
    provider_source = Path('src/providers/forecast_evidence_storage_provider.py').read_text()
    for token in ('cursor', '.execute(', 'psycopg', 'create_all', 'mlflow', 'xgboost'):
        assert token not in provider_source
    import sys
    assert not any(name in sys.modules for name in ('xgboost', 'torch', 'mlflow', 'src.database.db'))


def test_restart_seed_durable_batch():
    batch = make_batch('restart-seed')
    assert repository(batch)._append_initial(batch).batch == batch


@pytest.mark.parametrize('column,value', [('revision', 2), ('expected_current_version', 1)])
def test_database_rejects_noninitial_row_revision_and_cas(column, value):
    batch = make_batch(); repository(batch)._append_initial(batch)
    columns = ('organization_id,dataset_id,logical_key_id,revision,expected_current_version,idempotency_id,ordinal,'
        'series_id,candidate_id,family,forecast_origin,target_period,target_period_end,capture_cutoff,'
        'key_canonical,row_canonical,row_sha256,float32_le').split(',')
    expressions = [str(value) if name == column else name for name in columns]
    with admin() as connection, connection.cursor() as cursor:
        with pytest.raises(driver().IntegrityError) as caught:
            cursor.execute('INSERT INTO canonical_forecast.evidence ('+','.join(columns)+') SELECT '
                +','.join(expressions)+' FROM canonical_forecast.evidence WHERE organization_id=%s',
                (str(batch.scope.organization_id),))
        assert caught.value.pgcode == '23514'


def test_database_hash_constraint_and_complete_pointer_check():
    batch = make_batch(); store = repository(batch)
    with target('writer')._open(batch.scope) as connection, connection.cursor() as cursor:
        with pytest.raises(driver().IntegrityError) as caught:
            cursor.execute('INSERT INTO canonical_forecast.batches '
                '(organization_id,dataset_id,idempotency_id,receipt_id,content_sha256,record_set_canonical,series_ids,row_count) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s::uuid[],%s)', (str(batch.scope.organization_id), str(batch.scope.dataset_id),
                batch.idempotency_id, str(uuid4()), '0'*64, batch.canonical_json, [str(s) for s in batch.scope.series_ids], len(batch.rows)))
        assert caught.value.pgcode == '23514'
    assert raw_counts(batch) == (0, 0, 0)
    with target('writer')._open(batch.scope) as connection, connection.cursor() as cursor:
        store._insert_header(cursor, batch)
        # A complete header and only three out of four row/pointer pairs cannot commit.
        for ordinal in range(3): store._insert_row(cursor, batch, ordinal)
        with pytest.raises(driver().IntegrityError): connection.commit()
    assert raw_counts(batch) == (0, 0, 0)


def test_statement_timeout_aborts_without_writes():
    batch = make_batch()
    with target('writer')._open(batch.scope) as connection, connection.cursor() as cursor:
        cursor.execute('SET LOCAL statement_timeout=20')
        with pytest.raises(driver().errors.QueryCanceled): cursor.execute('SELECT pg_sleep(.2)')
    assert raw_counts(batch) == (0, 0, 0)


def test_libpq_environment_credentials_rejected_without_connection(monkeypatch):
    batch = make_batch()
    monkeypatch.setenv('PGSERVICE', 'untrusted-production-service')
    called = []
    def forbidden(*args, **kwargs): called.append(True); raise AssertionError()
    monkeypatch.setattr(driver(), 'connect', forbidden)
    with pytest.raises(ForecastStorageUnavailable):
        with target('reader')._open(batch.scope): pass
    assert not called


@pytest.mark.parametrize('change', ['target_period', 'target_period_end', 'forecast_origin'])
def test_wrong_h1_geometry_rejected(change):
    from datetime import timedelta
    key = make_batch().rows[0].key
    with pytest.raises(ValueError): clone(key, **{change: getattr(key, change)+timedelta(hours=1)})


def test_duplicate_json_fields_and_noncanonical_semantics_reject():
    batch = make_batch()
    with pytest.raises(ValueError):
        CanonicalForecastStorageBatch.model_validate_json(batch.canonical_json[:-1]+',"version":"1.0"}')
    key = batch.rows[0].key
    with pytest.raises(ValueError): clone(key, measurement_json=' '+key.measurement_json)
    with pytest.raises(ValueError): clone(key, measurement_json=key.measurement_json[:-1]+',"unit":"x"}')


def test_series_partition_and_candidate_partitions_do_not_collide():
    batch = make_batch(); store = repository(batch); store._append_initial(batch)
    other = clone(batch, idempotency_id='1'*64,
        rows=tuple(clone(row, key=clone(row.key, candidate_id='2'*64)) for row in batch.rows))
    assert store._append_initial(other).receipt != store._lookup(batch.idempotency_id).receipt
    assert raw_counts(batch) == (2, 8, 8)
