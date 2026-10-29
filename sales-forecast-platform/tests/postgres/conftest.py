"""Synthetic content only. Never successful-producer or write-authority evidence."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.abc
import os
import struct
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid5

import pytest


class NoOperationalImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (fullname.split('.')[0] in {'xgboost', 'lightgbm', 'torch', 'mlflow', 'celery'}
                or fullname in {'src.database.db', 'src.services.successful_b3_producer_service',
                    'src.services.forecast_write_intent_authority', 'src.services.controlled_h1_execution_service',
                    'src.services.controlled_execution_authority'}):
            raise AssertionError('operational import forbidden in PostgreSQL foundation tests: '+fullname)


sys.meta_path.insert(0, NoOperationalImports())
from src.data.contracts.forecast_evidence_storage import (
    ForecastStorageScope, ForecastLogicalKey, ForecastStorageLineage, ForecastEvidenceRow,
    CanonicalForecastStorageBatch,
)
from src.data.contracts.dataset import operational_json
from src.data.contracts.features import TemporalFeatureRules
from src.data.contracts.mapping import _DeclaredMeasurement
from src.database.offline_forecast_postgres import (
    OfflineForecastPostgres, apply_forecast_evidence_migration, driver, ForecastStorageUnavailable,
)
from src.repositories.postgres_forecast_evidence_repository import _PostgresForecastEvidenceRepository
from src.providers.forecast_evidence_storage_provider import _ForecastEvidenceStorageProvider

INSTANCE = UUID(os.environ['R7_PG_INSTANCE'])
DATABASE = 'r7_05b3_'+INSTANCE.hex
if os.environ.get('R7_PG_DATABASE') != DATABASE:
    raise RuntimeError('explicit disposable identity required')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def target(access):
    return OfflineForecastPostgres(database=DATABASE, instance_id=INSTANCE, access=access,
        password=os.environ['R7_PG_'+access.upper()+'_PASSWORD'], allow_disposable=True)


@contextmanager
def admin():
    connection = driver().connect(host='/run/r7pg', hostaddr='', port=5432, dbname=DATABASE,
        user='r7_test_owner', password=os.environ['R7_PG_ADMIN_PASSWORD'],
        passfile='/dev/null', sslmode='disable', connect_timeout=3,
        options='-c statement_timeout=5000 -c lock_timeout=2000')
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def make_batch(name=None, *, family='xgboost', count=4, values=None):
    name = str(uuid4()) if name is None else name
    org, dataset = uuid5(INSTANCE, name+'org'), uuid5(INSTANCE, name+'dataset')
    series = tuple(sorted(uuid5(INSTANCE, name+'series'+str(i)) for i in range(count)))
    scope = ForecastStorageScope(organization_id=org, dataset_id=dataset, series_ids=series)
    origin = datetime(2026, 1, 5, tzinfo=timezone.utc)
    measurement = _DeclaredMeasurement(metric='sales', unit='count', currency=None)
    temporal = TemporalFeatureRules(frequency='daily', calendar='gregorian', timezone='UTC', period_anchor='start',
        week_start=None, lag_basis='period', gap_policy='reject', minimum_history=2, cold_start='reject',
        recursive_strategy='provided_predictions')
    lineage = ForecastStorageLineage(**{field: digest(name+field) for field in
        ('dataset_version', 'publication_id', 'capture_reference', 'artifact_id', 'artifact_sha256',
         'executable_input_id', 'snapshot_id', 'representation_id', 'frozen_state_id', 'owner_reference',
         'execution_id', 'producer_issuance_id')}, capture_cutoff=origin,
        context_json=operational_json({'synthetic_content_only': True, 'source_adapter_independent': True}))
    values = values or [498.6732482910156, 649.3267211914062, 0., -0.]
    rows = []
    # Original producer order need not equal sorted scope order; preserve it.
    for i, series_id in enumerate(reversed(series)):
        key = ForecastLogicalKey(organization_id=org, dataset_id=dataset, series_id=series_id,
            candidate_id=digest(name+'candidate'), family=family, forecast_origin=origin, target_period=origin,
            target_period_end=origin+timedelta(days=1), target_metric='sales',
            measurement_json=operational_json(measurement.model_dump(mode='json')),
            temporal_json=operational_json(temporal.model_dump(mode='json')))
        rows.append(ForecastEvidenceRow(key=key, lineage=lineage, float32_le=struct.pack('<f', values[i % len(values)]).hex()))
    return CanonicalForecastStorageBatch(scope=scope, idempotency_id=digest(name+'idempotency'), rows=tuple(rows))


def repository(batch):
    return _PostgresForecastEvidenceRepository(target('reader'), target('writer'), batch.scope)


def provider(batch):
    return _ForecastEvidenceStorageProvider(repository(batch))


@pytest.fixture(scope='session')
def migrated():
    batch = make_batch('pre-migration-probe')
    with admin() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT to_regnamespace('canonical_forecast')")
        assert cursor.fetchone() == (None,)
    with pytest.raises(ForecastStorageUnavailable):
        repository(batch)._lookup(batch.idempotency_id)
    with admin() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT to_regnamespace('canonical_forecast')")
        assert cursor.fetchone() == (None,)
    assert apply_forecast_evidence_migration(target('migrator')) == 'APPLIED'
    assert apply_forecast_evidence_migration(target('migrator')) == 'ALREADY_APPLIED'
    return SimpleNamespace(database=DATABASE, instance=INSTANCE)
