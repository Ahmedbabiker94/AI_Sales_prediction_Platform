"""Explicit, read-only PostgreSQL extraction; no operational database imports."""
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
import ipaddress
import math
import re
from uuid import UUID

import pandas as pd
import psycopg2
from psycopg2 import sql
import pyarrow as pa

from src.data.adapters.base import SourceType
from src.data.adapters.file_common import positive_integer, records_frame
from src.data.adapters.network_common import (
    NetworkAdapter, NetworkAdapterError, NetworkConfiguration, nonblank, record_reference,
)
from src.data.adapters.selection import SourceConfiguration
from src.data.contracts import DataFormat


def _identifier(value):
    if type(value) is not str or len(value) > 63 or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value):
        raise NetworkAdapterError("unsupported SQL identifier")


# Fixed built-in PostgreSQL OIDs, checked against pg_catalog names/namespace.
_KEY_TYPES = {21: 'int2', 23: 'int4', 20: 'int8', 1700: 'numeric',
              2950: 'uuid', 25: 'text', 1043: 'varchar'}


def _key_identity_value(value, oid):
    if oid in (21, 23, 20):
        bits = {21: 16, 23: 32, 20: 64}[oid]
        if type(value) is int and -(1 << (bits - 1)) <= value < (1 << (bits - 1)):
            return value
    elif oid == 1700:
        if type(value) is Decimal and value.is_finite():
            return value
    elif oid == 2950:
        if type(value) is UUID:
            return value
        if type(value) is str:
            try:
                parsed = UUID(value)
            except ValueError:
                raise NetworkAdapterError("ordering key representation is incompatible with verified SQL type") from None
            if str(parsed) == value:
                # Normalize only identity input, not the extracted source value.
                return parsed
    elif oid in (25, 1043) and type(value) is str:
        return value
    raise NetworkAdapterError("ordering key representation is incompatible with verified SQL type")


@dataclass(frozen=True)
class PostgreSQLCredentials:
    username: str = field(repr=False)
    password: str = field(repr=False)


@dataclass(frozen=True)
class PostgreSQLConfiguration(NetworkConfiguration):
    host: str = field(repr=False, default='')
    database: str = field(repr=False, default='')
    credential_reference: str = ''
    schema: str = ''
    table: str = ''
    columns: tuple[str, ...] = ()
    allowed_relations: tuple[tuple[str, str], ...] = ()
    # Ordering is the configured unique source key, not guessed from source values.
    tenant_column: str | None = None
    filters: tuple[tuple[str, object], ...] = ()
    port: int = 5432
    connect_timeout: int = 10
    statement_timeout_ms: int = 30_000
    idle_transaction_timeout_ms: int = 30_000
    sslmode: str = 'verify-full'
    sslrootcert: str | None = field(default=None, repr=False)

    def __post_init__(self):
        super().__post_init__()
        for name in ('columns', 'allowed_relations', 'filters'):
            values = getattr(self, name)
            object.__setattr__(self, name, tuple(tuple(v) if name != 'columns' else v for v in values))

    def validate(self):
        super().validate()
        for value in (self.host, self.database, self.credential_reference):
            nonblank(value)
        SourceConfiguration(self.credential_reference)
        try:
            ipaddress.ip_address(self.host)
        except ValueError:
            if not re.fullmatch(r'[A-Za-z0-9.-]+', self.host):
                raise NetworkAdapterError("PostgreSQL host must be one explicit hostname/address, not a DSN or socket path")
        for value in (self.schema, self.table, *self.columns, *self.record_key_fields):
            _identifier(value)
        if not self.columns or len(set(self.columns)) != len(self.columns) or len(self.columns) > self.max_columns:
            raise NetworkAdapterError("explicit unique bounded columns are required")
        if not set(self.record_key_fields) <= set(self.columns):
            raise NetworkAdapterError("ordering keys must be extracted columns")
        for relation in self.allowed_relations:
            if len(relation) != 2:
                raise NetworkAdapterError("invalid relation allowlist")
            for value in relation:
                _identifier(value)
        if (self.schema, self.table) not in self.allowed_relations:
            raise NetworkAdapterError("source relation is not allowlisted")
        if self.tenant_column is not None:
            _identifier(self.tenant_column)
        for item in self.filters:
            if len(item) != 2:
                raise NetworkAdapterError("only explicit equality filters are supported")
            name, value = item
            _identifier(name)
            if name == self.tenant_column or name not in self.columns:
                raise NetworkAdapterError("filter conflicts with tenant scope or selected columns")
            if type(value) not in (str, int, bool, float) or (type(value) is float and not math.isfinite(value)):
                raise NetworkAdapterError("unsupported parameterized filter value")
        if len({name for name, _ in self.filters}) != len(self.filters):
            raise NetworkAdapterError("duplicate filter fields")
        for name in ('port', 'connect_timeout', 'statement_timeout_ms', 'idle_transaction_timeout_ms'):
            positive_integer(getattr(self, name), name)
        if self.port > 65535 or self.connect_timeout > 60 or self.statement_timeout_ms > 300_000 or self.idle_transaction_timeout_ms > 300_000:
            raise NetworkAdapterError("connection or transaction timeout exceeds allowed bound")
        if self.sslmode != 'verify-full':
            raise NetworkAdapterError("PostgreSQL TLS hostname verification is required")
        if self.sslrootcert is not None:
            nonblank(self.sslrootcert)


class PostgreSQLAdapter(NetworkAdapter):
    configuration_type = PostgreSQLConfiguration
    source_type = SourceType.POSTGRESQL
    source_format = DataFormat.DATABASE

    def __init__(self, configuration, asset, *, authorize_scope, resolve_credentials, connect=None):
        super().__init__(configuration, asset, authorize_scope=authorize_scope)
        self.resolve_credentials = resolve_credentials
        # Injection is a trusted testing boundary, never source configuration.
        self.connect = connect or psycopg2.connect

    def validate_configuration(self):
        super().validate_configuration()
        if not callable(self.resolve_credentials) or not callable(self.connect):
            raise NetworkAdapterError("explicit credential resolver and connection factory are required")

    @contextmanager
    def _transaction(self):
        self._authorize()
        cfg = self.configuration
        try:
            credentials = self.resolve_credentials(cfg.credential_reference)
            if type(credentials) is not PostgreSQLCredentials:
                raise NetworkAdapterError("invalid PostgreSQL credentials")
            if type(credentials.username) is not str or not credentials.username.strip() or type(credentials.password) is not str or not credentials.password:
                raise NetworkAdapterError("explicit database credentials are required")
            options = dict(host=cfg.host, port=cfg.port, dbname=cfg.database,
                           user=credentials.username, password=credentials.password,
                           connect_timeout=cfg.connect_timeout, sslmode=cfg.sslmode,
                           options='-c default_transaction_read_only=on')
            if cfg.sslrootcert is not None:
                options['sslrootcert'] = cfg.sslrootcert
            connection = self.connect(**options)
            with self._owned(connection):
                try:
                    connection.set_session(readonly=True, isolation_level='REPEATABLE READ', autocommit=False)
                    with self._owned(connection.cursor()) as cursor:
                        cursor.execute("SELECT set_config('statement_timeout', %s, true), "
                                       "set_config('idle_in_transaction_session_timeout', %s, true)",
                                       (str(cfg.statement_timeout_ms), str(cfg.idle_transaction_timeout_ms)))
                        cursor.execute('SHOW transaction_read_only')
                        if cursor.fetchone() != ('on',):
                            raise NetworkAdapterError("read-only transaction enforcement failed")
                        # Do not trust a declarative 'read-only credentials' flag.
                        cursor.execute('SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls '
                                       'FROM pg_roles WHERE rolname = current_user')
                        role = cursor.fetchone()
                        if role is None or any(role):
                            raise NetworkAdapterError("a nonprivileged read-only database role is required")
                        cursor.execute("SELECT has_schema_privilege(current_user, %s, 'USAGE'), "
                                       "has_schema_privilege(current_user, %s, 'CREATE')", (cfg.schema, cfg.schema))
                        if cursor.fetchone() != (True, False):
                            raise NetworkAdapterError("source schema permissions are not read-only")
                        cursor.execute('SELECT c.oid, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace '
                                       'WHERE n.nspname=%s AND c.relname=%s', (cfg.schema, cfg.table))
                        relation = cursor.fetchone()
                        if relation is None or relation[1] not in ('r', 'p', 'v', 'm'):
                            raise NetworkAdapterError("configured table or approved view is unavailable")
                        cursor.execute("SELECT has_table_privilege(current_user, %s, 'SELECT'), "
                                       "(has_table_privilege(current_user, %s, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
                                       "OR has_any_column_privilege(current_user, %s, 'INSERT,UPDATE,REFERENCES'))",
                                       (relation[0], relation[0], relation[0]))
                        if cursor.fetchone() != (True, False):
                            raise NetworkAdapterError("source relation permissions are not read-only")
                        if relation[1] != 'v':
                            cursor.execute(
                                'SELECT EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid=%s '
                                'AND i.indisunique AND i.indisvalid AND i.indpred IS NULL AND i.indexprs IS NULL '
                                'AND i.indnkeyatts=%s AND '
                                '(SELECT array_agg(a.attname::text) '
                                'FROM unnest(i.indkey::smallint[]) WITH ORDINALITY k(attnum, position) '
                                'JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum '
                                'WHERE k.position <= i.indnkeyatts) @> %s::text[])',
                                (relation[0], len(cfg.record_key_fields), list(cfg.record_key_fields)))
                            if cursor.fetchone() != (True,):
                                raise NetworkAdapterError("table ordering requires a verified unique index")
                    yield connection
                finally:
                    connection.rollback()  # Never commit extraction transactions.
        except NetworkAdapterError:
            raise
        except Exception:
            raise NetworkAdapterError("PostgreSQL connection or read-only extraction failed") from None

    def _query(self, *, schema_only=False):
        cfg = self.configuration
        conditions, values = [], []
        if cfg.tenant_column:
            conditions.append(sql.SQL('{} = %s').format(sql.Identifier(cfg.tenant_column)))
            values.append(str(cfg.organization_id))
        for name, value in cfg.filters:
            conditions.append(sql.SQL('{} = %s').format(sql.Identifier(name)))
            values.append(value)
        query = sql.SQL('SELECT {} FROM {}.{}').format(
            sql.SQL(', ').join(map(sql.Identifier, cfg.columns)), sql.Identifier(cfg.schema), sql.Identifier(cfg.table))
        if schema_only:
            query += sql.SQL(' WHERE FALSE')
        elif conditions:
            query += sql.SQL(' WHERE ') + sql.SQL(' AND ').join(conditions)
        query += sql.SQL(' ORDER BY ') + sql.SQL(', ').join(map(sql.Identifier, cfg.record_key_fields))
        return query, tuple(values) if not schema_only else ()

    def _ordering_key_types(self, connection):
        cfg = self.configuration
        with self._owned(connection.cursor()) as cursor:
            cursor.execute(
                'SELECT a.attname, a.atttypid, t.typname, tn.nspname, '
                'co.collname, cn.nspname, co.collprovider, co.collisdeterministic, co.collcollate '
                'FROM pg_catalog.pg_attribute a '
                'JOIN pg_catalog.pg_class c ON c.oid=a.attrelid '
                'JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace '
                'JOIN pg_catalog.pg_type t ON t.oid=a.atttypid '
                'JOIN pg_catalog.pg_namespace tn ON tn.oid=t.typnamespace '
                'LEFT JOIN pg_catalog.pg_collation co ON co.oid=a.attcollation '
                'LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid=co.collnamespace '
                'WHERE n.nspname=%s AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped '
                'AND a.attname::text=ANY(%s::text[])',
                (cfg.schema, cfg.table, list(cfg.record_key_fields)))
            rows = cursor.fetchall()
        if len(rows) != len(cfg.record_key_fields) or {row[0] for row in rows} != set(cfg.record_key_fields):
            raise NetworkAdapterError("ordering key catalog metadata is unavailable or inconsistent")
        verified = {}
        for name, oid, type_name, namespace, collname, collnamespace, provider, deterministic, locale in rows:
            if type(oid) is not int or oid not in _KEY_TYPES or namespace != 'pg_catalog' or type_name != _KEY_TYPES[oid]:
                raise NetworkAdapterError("unsupported ordering key SQL type")
            if oid in (25, 1043) and not (
                    collname in ('C', 'POSIX') and collnamespace == 'pg_catalog'
                    and provider == 'c' and deterministic is True and locale in ('C', 'POSIX')):
                raise NetworkAdapterError("ordering key text collation is unsupported")
            verified[name] = oid
        return tuple(verified[name] for name in cfg.record_key_fields)

    def test_access(self):
        with self._transaction() as connection:
            self._ordering_key_types(connection)

    def discover_schema(self):
        with self._transaction() as connection, self._owned(connection.cursor()) as cursor:
            self._ordering_key_types(connection)
            cursor.execute(*self._query(schema_only=True))
            return {item.name: str(item.type_code) for item in cursor.description}

    def extract(self, *, batch_size=10000):
        with self._extraction(batch_size), self._transaction() as connection:
            key_types = self._ordering_key_types(connection)
            with self._owned(connection.cursor(name='source_extract', withhold=False)) as cursor:
                cursor.itersize = batch_size
                cursor.execute(*self._query())
                offset, seen = 0, set()
                indexes = [self.configuration.columns.index(key) for key in self.configuration.record_key_fields]
                while True:
                    self._authorize()
                    rows = cursor.fetchmany(batch_size)
                    if not rows:
                        break
                    if len(rows) > batch_size or offset + len(rows) > self.configuration.max_rows:
                        raise NetworkAdapterError("PostgreSQL extraction row limit exceeded")
                    description = cursor.description
                    if (description is None or tuple(item.name for item in description) != self.configuration.columns
                            or any(description[i].type_code != oid for i, oid in zip(indexes, key_types))):
                        raise NetworkAdapterError("ordering key result metadata differs from verified SQL type")
                    ids = [record_reference([_key_identity_value(row[i], oid)
                                             for i, oid in zip(indexes, key_types)]) for row in rows]
                    if len(set(ids)) != len(ids) or seen.intersection(ids):
                        raise NetworkAdapterError("configured ordering key is not unique")
                    seen.update(ids)
                    frame = records_frame(rows, self.configuration.columns)
                    # Preserve PostgreSQL NUMERIC precision in a mapper-compatible
                    # Arrow dtype; never cast Decimal targets through float.
                    for name in frame:
                        present = [v for v in frame[name] if v is not None]
                        if present and all(type(v) is Decimal and v.is_finite() for v in present):
                            array = pa.array(frame[name].tolist())
                            frame[name] = pd.Series(array, dtype=pd.ArrowDtype(array.type))
                    yield self._batch(frame, self.configuration.columns, ids, offset,
                                      metadata={'source_consistency': 'repeatable_read', 'source_order': 'configured_unique_key'})
                    offset += len(rows)
                if not offset:
                    raise NetworkAdapterError("PostgreSQL source contains no records")
