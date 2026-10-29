"""Explicit disposable PostgreSQL connections and migration, no app DB bootstrap.

Only private Unix-socket test databases with an exact run identity are supported.
No environment DSN, credential fallback, schema creation on read, or model import.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import importlib
import os
from pathlib import Path
from uuid import UUID

from src.data.contracts.forecast_evidence_storage import ForecastStorageScope

MIGRATION_SHA256 = '77bbcbba6c0ccce15f4837a7877c54f790705360d99b7d17b4fb5d00f63b2d32'
MIGRATION = Path(__file__).with_name('migrations')/'001_canonical_forecast_evidence.sql'
ROLES = {'reader': 'r7_forecast_reader', 'writer': 'r7_forecast_writer', 'migrator': 'r7_forecast_migrator'}


class ForecastStorageError(ValueError):
    pass


class ForecastStorageRejected(ForecastStorageError):
    pass


class ForecastStorageConflict(ForecastStorageError):
    pass


class ForecastStorageUnavailable(ForecastStorageError):
    pass


class ForecastStorageRetryable(ForecastStorageUnavailable):
    """Known rolled-back serialization/deadlock; no automatic retry is performed."""


class AmbiguousForecastCommit(ForecastStorageUnavailable):
    """Commit acknowledgement unknown. Read-only lookup before any identical retry."""


def reject(kind=ForecastStorageRejected):
    error = kind('canonical forecast storage unavailable or rejected')
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


def driver():
    try:
        module = importlib.import_module('psycopg2')
        if module.__version__.split()[0] != '2.9.12':
            raise ValueError()
        return module
    except Exception:
        pass
    reject(ForecastStorageUnavailable)


@dataclass(frozen=True)
class OfflineForecastPostgres:
    database: str
    instance_id: UUID
    access: str
    password: str = field(repr=False)
    allow_disposable: bool = False
    socket_directory: str = '/run/r7pg'

    def __post_init__(self):
        if (type(self.instance_id) is not UUID or self.database != 'r7_05b3_'+self.instance_id.hex
                or self.access not in ROLES or type(self.password) is not str or not 16 <= len(self.password) <= 128
                or self.allow_disposable is not True or self.socket_directory != '/run/r7pg'):
            reject()

    @contextmanager
    def _open(self, scope=None):
        connection = None
        try:
            # libpq service/environment settings may add connection behavior.
            # This offline boundary accepts explicit test credentials only.
            if any(value for key, value in os.environ.items() if key.startswith('PG')):
                raise ValueError()
            if self.access != 'migrator' and type(scope) is not ForecastStorageScope:
                raise ValueError()
            options = '-c statement_timeout=5000 -c lock_timeout=2000 -c idle_in_transaction_session_timeout=5000'
            options += ' -c search_path=pg_catalog -c default_transaction_read_only='+('on' if self.access == 'reader' else 'off')
            if scope is not None:
                scope = ForecastStorageScope.model_validate_json(scope.canonical_json)
                options += (' -c r7_forecast.tenant='+str(scope.organization_id)
                    +' -c r7_forecast.dataset='+str(scope.dataset_id)
                    +' -c r7_forecast.series='+','.join(str(s) for s in scope.series_ids))
            connection = driver().connect(host=self.socket_directory, hostaddr='', port=5432,
                dbname=self.database, user=ROLES[self.access], password=self.password,
                passfile='/dev/null', sslmode='disable', connect_timeout=3,
                application_name='r7_forecast_storage_'+self.access, options=options)
            connection.set_session(readonly=self.access == 'reader',
                isolation_level='REPEATABLE READ' if self.access == 'reader' else 'READ COMMITTED', autocommit=False)
            self._guard(connection)
        except Exception:
            if connection is not None:
                connection.close()
            reject(ForecastStorageUnavailable)
        try:
            yield connection
        finally:
            try:
                connection.rollback()
            except Exception:
                pass
            connection.close()

    def _guard(self, connection):
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), current_user, shobj_description(oid,'pg_database'), "
                "current_setting('server_version_num')::integer, inet_server_addr() FROM pg_database WHERE datname=current_database()")
            row = cursor.fetchone()
            if (row is None or row[:3] != (self.database, ROLES[self.access], 'r7_05b3_disposable:'+str(self.instance_id))
                    or not 160000 <= row[3] < 170000 or row[4] is not None):
                raise ValueError()
            cursor.execute('SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname=current_user')
            role = cursor.fetchone()
            if role is None or any(role):
                raise ValueError()
            if self.access == 'migrator':
                return
            cursor.execute("SELECT has_database_privilege(current_user,current_database(),'CREATE,TEMP')")
            if cursor.fetchone() != (False,):
                raise ValueError()
            if self.access == 'reader':
                cursor.execute('SHOW transaction_read_only')
                if cursor.fetchone() != ('on',):
                    raise ValueError()
                cursor.execute("SELECT to_regnamespace('canonical_forecast')")
                if cursor.fetchone()[0] is not None:
                    cursor.execute("SELECT has_schema_privilege(current_user,'canonical_forecast','USAGE'), has_schema_privilege(current_user,'canonical_forecast','CREATE')")
                    if cursor.fetchone() != (True, False):
                        raise ValueError()
                    for table in ('schema_migrations', 'batches', 'evidence', 'current_records'):
                        name = 'canonical_forecast.'+table
                        cursor.execute("SELECT has_table_privilege(current_user,%s,'SELECT'), has_table_privilege(current_user,%s,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'), has_any_column_privilege(current_user,%s,'INSERT,UPDATE,REFERENCES')", (name, name, name))
                        if cursor.fetchone() != (True, False, False):
                            raise ValueError()


def verify_forecast_schema(cursor):
    cursor.execute('SELECT version, checksum FROM canonical_forecast.schema_migrations ORDER BY version')
    if cursor.fetchall() != [(1, MIGRATION_SHA256)]:
        raise ValueError('explicit canonical forecast migration required')


def apply_forecast_evidence_migration(target):
    """Explicit, checked, transactional migration only. Not called by any reader."""
    try:
        if type(target) is not OfflineForecastPostgres or target.access != 'migrator':
            raise ValueError()
        raw = MIGRATION.read_bytes()
        if hashlib.sha256(raw).hexdigest() != MIGRATION_SHA256:
            raise ValueError()
        with target._open() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(70503001)')
                cursor.execute("SELECT to_regnamespace('canonical_forecast')")
                exists = cursor.fetchone()[0] is not None
                if exists:
                    verify_forecast_schema(cursor)
                    return 'ALREADY_APPLIED'
                cursor.execute(raw.decode('utf-8'))
                cursor.execute('INSERT INTO canonical_forecast.schema_migrations (version,checksum) VALUES (1,%s)', (MIGRATION_SHA256,))
            connection.commit()
            return 'APPLIED'
    except Exception:
        pass
    reject(ForecastStorageUnavailable)
