"""Fresh SELECT-only disposable connections; no writer, migration or app bootstrap.

Reuse the accepted connection identity/role guard. Own cleanup from setup through
cursor use, including BaseException during setup. No production DSN fallback.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
import os
from src.data.contracts.forecast_evidence_storage import ForecastStorageScope
from src.database.offline_forecast_postgres import (
    OfflineForecastPostgres, driver, reject,
)


@dataclass(frozen=True)
class ForecastReadConnectionFactory:
    _target: OfflineForecastPostgres = field(repr=False)

    def __post_init__(self):
        if type(self._target) is not OfflineForecastPostgres or self._target.access != 'reader':
            reject()
        # Revalidate even if a frozen declaration was bypassed before construction.
        OfflineForecastPostgres(**vars(self._target))

    @contextmanager
    def _open(self, scope):
        connection = None
        try:
            if type(scope) is not ForecastStorageScope:
                reject()
            scope = ForecastStorageScope.model_validate_json(scope.canonical_json)
            target = self._target
            if target.access != 'reader' or any(v for k, v in os.environ.items() if k.startswith('PG')):
                reject()
            options = ('-c statement_timeout=5000 -c lock_timeout=2000 '
                '-c idle_in_transaction_session_timeout=5000 -c search_path=pg_catalog '
                '-c default_transaction_read_only=on -c r7_forecast.tenant='+str(scope.organization_id)
                +' -c r7_forecast.dataset='+str(scope.dataset_id)
                +' -c r7_forecast.series='+','.join(str(s) for s in scope.series_ids))
            connection = driver().connect(host=target.socket_directory, hostaddr='', port=5432,
                dbname=target.database, user='r7_forecast_reader', password=target.password,
                passfile='/dev/null', sslmode='disable', connect_timeout=3,
                application_name='r7_forecast_consumer_read', options=options)
            connection.set_session(readonly=True, isolation_level='REPEATABLE READ', autocommit=False)
            target._guard(connection)
            yield connection
        finally:
            if connection is not None:
                try:
                    connection.rollback()
                finally:
                    connection.close()
