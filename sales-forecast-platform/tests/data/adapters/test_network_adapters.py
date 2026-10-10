"""Controlled PostgreSQL doubles, mock HTTP and isolated loopback HTTP tests."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, Inexact, Rounded, localcontext
import io
import json
import socket
import ssl
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pandas as pd
from psycopg2 import sql
import pytest

from src.data.adapters.base import SourceAdapter, SourceType
from src.data.adapters.network_common import NetworkAdapterError, record_reference
from src.data.adapters.network_registry import network_connector_registry
from src.data.adapters.postgres_adapter import PostgreSQLAdapter, PostgreSQLConfiguration, PostgreSQLCredentials
from src.data.adapters.rest_adapter import (
    RESTAdapter, RESTConfiguration, RESTCredentials, RESTNetworkPolicy, RESTResponse,
    PinnedHTTPTransport, _PinnedHTTPSConnection, _DeadlineReader,
    resolve_addresses,
)
from src.data.adapters.selection import SourceConfiguration, SourceSelectionError, select_connector
from src.data.contracts import AssetMetadata, SourceMapping, Observation
from src.data.processing.canonical_mapper import CanonicalMapper


NAMES = ('id', 'site', 'start', 'end', 'amount', 'flag')
ROWS = [(1, '001', '2026-01-01', '2026-01-08', -12.5, True),
        (2, '001', '2026-01-08', '2026-01-15', 0., False),
        (3, '001', '2026-01-15', '2026-01-22', 42., True)]
ORG = UUID(int=20)


@pytest.fixture
def asset():
    return AssetMetadata(asset_id=UUID(int=10), organization_id=ORG, source_id=UUID(int=30),
                         ingestion_batch_id=UUID(int=40), created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))


def pg_config(**updates):
    config = PostgreSQLConfiguration(source_reference='database-import', source_dataset='sales-import',
        organization_id=ORG, record_key_fields=('id',), host='approved.example.test', database='analytics',
        credential_reference='vault-reader', schema='business', table='observations', columns=NAMES,
        allowed_relations=(('business', 'observations'),), tenant_column='organization_id')
    return replace(config, **updates)


def rest_config(**updates):
    config = RESTConfiguration(source_reference='api-import', source_dataset='sales-import',
        organization_id=ORG, record_key_fields=('id',), endpoint='https://api.example.test/records',
        allowed_origins=('https://api.example.test',), records_key='records', pagination='page',
        page_size=2, tenant_parameter='organization_id')
    return replace(config, **updates)


def mapping():
    return SourceMapping(mapping_id='network-test', version='1.0', organization_id=ORG, business_scope='retail',
        field_mappings={'location': 'site', 'start': 'start', 'end': 'end', 'amount': 'amount', 'flag': 'flag'},
        identity_dimensions=('location',), target_metric='reported_sales', target_value_field='amount',
        metric_meaning='signed reported sales', sign_semantics='source signed values', policy_metric='sales',
        policy_context='signed_reported_training', period_start_field='start', period_end_field='end',
        period_interpretation='explicit_bounds', frequency='weekly', timezone='UTC', unit_required=True,
        currency_required=True, required_auxiliary_fields=('flag',),
        measurement={'metric': 'reported_sales', 'unit': 'amount', 'currency': 'SAR'})


def render(query):
    """Inspect psycopg2 composition without opening a database connection."""
    if type(query) is sql.SQL:
        return query.string
    if type(query) is sql.Identifier:
        return '.'.join('"' + item.replace('"', '""') + '"' for item in query.strings)
    if type(query) is sql.Composed:
        return ''.join(render(item) for item in query.seq)
    return query


class Cursor:
    def __init__(self, connection, name=None):
        self.connection, self.name, self.closed, self.position = connection, name, False, 0
        self.description = [SimpleNamespace(name=name, type_code=connection.column_types[name]) for name in NAMES]
        self.answer = None
        self.itersize = None

    def execute(self, query, values=()):
        self.connection.queries.append((render(query), values))
        if self.connection.fail_execute:
            raise TimeoutError('PASSWORD_SECRET')
        text = render(query)
        if text == 'SHOW transaction_read_only':
            self.answer = ('on' if self.connection.readonly else 'off',)
        elif 'FROM pg_roles' in text:
            self.answer = self.connection.role
        elif 'has_schema_privilege' in text:
            self.answer = self.connection.schema_permissions
        elif 'FROM pg_class' in text:
            self.answer = (100, self.connection.relation_kind)
        elif 'has_table_privilege' in text:
            self.answer = self.connection.table_permissions
        elif 'FROM pg_index' in text:
            self.answer = (self.connection.unique_index,)
        elif 'FROM pg_catalog.pg_attribute' in text:
            if self.connection.key_catalog is not None:
                self.answer = self.connection.key_catalog
            else:
                names = {21:'int2', 23:'int4', 20:'int8', 1700:'numeric', 2950:'uuid', 25:'text', 1043:'varchar'}
                self.answer = [(name, self.connection.column_types[name],
                    names.get(self.connection.column_types[name], 'custom'), 'pg_catalog',
                    *self.connection.collations.get(name, (None,) * 5)) for name in values[2]]

    def fetchone(self):
        return self.answer

    def fetchall(self):
        return self.answer

    def fetchmany(self, count):
        assert self.name == 'source_extract' and count <= self.itersize
        rows = self.connection.rows[self.position:self.position + count]
        self.position += len(rows)
        return rows

    def close(self):
        self.closed = True


class Connection:
    def __init__(self, rows=ROWS, **updates):
        self.rows = list(rows)
        self.queries, self.cursors, self.sessions = [], [], []
        self.closed, self.rollbacks = False, 0
        self.readonly = True
        self.role = (False,) * 5
        self.schema_permissions = (True, False)
        self.table_permissions = (True, False)
        self.relation_kind, self.unique_index, self.fail_execute = 'r', True, False
        self.column_types = {'id':23, 'site':25, 'start':25, 'end':25, 'amount':701, 'flag':16}
        self.key_catalog = None
        self.collations = {'site':('C', 'pg_catalog', 'c', True, 'C')}
        for name, value in updates.items():
            if name == 'column_types':
                self.column_types.update(value)
            else:
                setattr(self, name, value)

    def set_session(self, **options):
        self.sessions.append(options)

    def cursor(self, name=None, withhold=False):
        assert withhold is False
        cursor = Cursor(self, name); self.cursors.append(cursor)
        return cursor

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def pg_adapter(asset, *, config=None, connection=None, authorize=None):
    connection = connection or Connection()
    calls = []
    def connect(**options):
        calls.append(options)
        return connection
    adapter = PostgreSQLAdapter(config or pg_config(), asset, authorize_scope=authorize or (lambda *_: True),
        resolve_credentials=lambda ref: PostgreSQLCredentials('reader', 'PASSWORD_SECRET'), connect=connect)
    return adapter, connection, calls


def response(rows, *, more=False, cursor=None, mode='page', status=200, extra_headers=()):
    records = [dict(zip(NAMES, row)) for row in rows]
    payload = {'records': records}
    if mode == 'page': payload['has_more'] = more
    elif mode == 'cursor': payload['next_cursor'] = cursor
    return RESTResponse(status, (('Content-Type', 'application/json'), *extra_headers), json.dumps(payload).encode())


class Transport:
    def __init__(self, responses):
        self.responses, self.calls, self.closed = list(responses), [], False

    def request(self, url, **options):
        assert not self.closed
        self.calls.append((url, options))
        item = self.responses.pop(0)
        if isinstance(item, Exception): raise item
        return item

    def close(self):
        self.closed = True


def rest_adapter(asset, responses, *, config=None, resolver=None, **kwargs):
    transport = Transport(responses)
    adapter = RESTAdapter(config or rest_config(), asset, authorize_scope=lambda *_: True,
        transport_factory=lambda: transport, resolver=resolver or (lambda *_: ('93.184.216.34',)), **kwargs)
    return adapter, transport


def test_postgres_readonly_bounded_extraction_and_parameterized_tenant(asset):
    config = pg_config(filters=(('site', "'; DROP TABLE anything; --"),))
    adapter, connection, calls = pg_adapter(asset, config=config)
    with adapter:
        batches = list(adapter.extract(batch_size=2))
    assert [len(batch.records) for batch in batches] == [2, 1]
    assert sum((batch.records.amount.tolist() for batch in batches), []) == [-12.5, 0., 42.]
    assert connection.sessions == [dict(readonly=True, isolation_level='REPEATABLE READ', autocommit=False)]
    assert connection.closed and connection.rollbacks == 1 and all(c.closed for c in connection.cursors)
    query, values = next(item for item in connection.queries if 'FROM "business"."observations"' in item[0])
    assert query.endswith('ORDER BY "id"') and 'DROP TABLE' not in query
    assert '"organization_id" = %s' in query and values == (str(ORG), "'; DROP TABLE anything; --")
    assert calls[0]['connect_timeout'] == 10 and calls[0]['sslmode'] == 'verify-full'
    assert calls[0]['options'] == '-c default_transaction_read_only=on'
    assert 'PASSWORD_SECRET' not in repr(batches) and 'PASSWORD_SECRET' not in repr(PostgreSQLCredentials('reader', 'PASSWORD_SECRET'))


@pytest.mark.parametrize('operation', ['test_access', 'discover_schema'])
def test_postgres_access_and_schema(asset, operation):
    adapter, connection, _ = pg_adapter(asset)
    result = getattr(adapter, operation)()
    if operation == 'discover_schema': assert list(result) == list(NAMES)
    assert connection.closed and connection.rollbacks == 1 and all(c.closed for c in connection.cursors)


@pytest.mark.parametrize('updates', [
    {'readonly': False}, {'role': (True, False, False, False, False)}, {'schema_permissions': (True, True)},
    {'table_permissions': (True, True)}, {'table_permissions': (False, False)}, {'unique_index': False},
    {'relation_kind': 'f'}, {'fail_execute': True},
])
def test_postgres_fail_closed_permissions_and_statement_failures(asset, updates):
    adapter, connection, _ = pg_adapter(asset, connection=Connection(**updates))
    with pytest.raises(NetworkAdapterError) as failure: list(adapter.extract())
    assert 'PASSWORD_SECRET' not in str(failure.value)
    assert connection.closed and connection.rollbacks == 1 and all(c.closed for c in connection.cursors)
    assert not adapter._active and not adapter._resources


@pytest.mark.parametrize('updates', [
    {'table': 'observations; DELETE FROM x'}, {'columns': ('id', 'amount;drop')},
    {'allowed_relations': ()}, {'record_key_fields': ('missing',)}, {'sslmode': 'disable'},
    {'filters': (('organization_id', 'other-tenant'),)}, {'connect_timeout': 0}, {'connect_timeout': 61},
    {'host': 'postgres://secret@example'}, {'port': True}, {'record_key_fields': ('id', 'id')},
])
def test_postgres_unsafe_configuration_never_connects(asset, updates):
    adapter, _, calls = pg_adapter(asset, config=pg_config(**updates))
    with pytest.raises(ValueError): list(adapter.extract())
    assert not calls


def test_postgres_connection_errors_are_sanitized(asset, caplog):
    def fail(**options): raise RuntimeError('PASSWORD_SECRET database endpoint')
    adapter = PostgreSQLAdapter(pg_config(), asset, authorize_scope=lambda *_: True,
        resolve_credentials=lambda _: PostgreSQLCredentials('reader', 'PASSWORD_SECRET'), connect=fail)
    with pytest.raises(NetworkAdapterError) as failure: adapter.test_access()
    assert 'PASSWORD_SECRET' not in str(failure.value) + caplog.text and not adapter._resources


@pytest.mark.parametrize('rows', [[], [ROWS[0], ROWS[0]], [ROWS[0], (None, *ROWS[1][1:])]])
def test_postgres_empty_duplicate_or_missing_ordering_key_rejected(asset, rows):
    adapter, connection, _ = pg_adapter(asset, connection=Connection(rows))
    with pytest.raises(NetworkAdapterError): list(adapter.extract(batch_size=1))
    assert connection.closed and not adapter._active


def test_postgres_generator_cancellation_and_row_limit(asset):
    adapter, connection, _ = pg_adapter(asset)
    stream = adapter.extract(batch_size=1); next(stream); stream.close()
    assert connection.closed and not adapter._resources and not adapter._active
    adapter, connection, _ = pg_adapter(asset, config=pg_config(max_rows=2))
    with pytest.raises(NetworkAdapterError): list(adapter.extract(batch_size=1))
    assert connection.closed


def test_postgres_approved_view_and_numeric_precision(asset):
    rows = [tuple(Decimal('9007199254740993.1234') if i == 4 else v for i, v in enumerate(ROWS[0]))]
    adapter, _, _ = pg_adapter(asset, connection=Connection(rows, relation_kind='v', column_types={'amount':1700}))
    with adapter: batch = next(adapter.extract())
    mapped = CanonicalMapper(mapping()).map_batch(batch)
    assert mapped.records.value.iloc[0] == Decimal('9007199254740993.1234')
    row = mapped.records.to_dict('records')[0]
    assert Observation.model_validate({k:v for k,v in row.items() if k in Observation.model_fields}).value == row['value']


@pytest.mark.parametrize('left,right', [
    ('1.0', '1.00'), ('0.0', '-0.00'), ('100.000', '100'),
    ('-123456789012345678901234567890.1234000', '-123456789012345678901234567890.1234'),
    ('1E+100000', '10E+99999'),
])
def test_decimal_key_identity_equivalence_without_rounding(left, right):
    keys = [Decimal(left), Decimal(right)]
    original = [key.as_tuple() for key in keys]
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = context.traps[Rounded] = True
        assert record_reference([keys[0]]) == record_reference([keys[1]])
    assert [key.as_tuple() for key in keys] == original


def test_decimal_key_identity_preserves_precision_and_type_boundaries():
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = context.traps[Rounded] = True
        values = [Decimal('123456789012345678901234567890.12345678901234567890'),
                  Decimal('123456789012345678901234567890.12345678901234567891')]
        assert record_reference([values[0]]) != record_reference([values[1]])
        assert len({record_reference([key]) for key in (1, Decimal('1'), '1')}) == 3
        assert record_reference([Decimal('1'), '23']) != record_reference([Decimal('12'), '3'])
    for unsupported in (1.0, True):
        with pytest.raises(NetworkAdapterError):
            record_reference([unsupported])


@pytest.mark.parametrize('batch_size', [1, 2])
@pytest.mark.parametrize('relation_kind', ['v', 'r'])
@pytest.mark.parametrize('left,right', [('1.0', '1.00'), ('0.0', '-0.00'), ('100.000', '100')])
def test_postgres_decimal_key_duplicates_reject_within_and_across_batches(asset, batch_size, relation_kind, left, right):
    rows = [(Decimal(left), *ROWS[0][1:]), (Decimal(right), *ROWS[1][1:])]
    original = [row[0].as_tuple() for row in rows]
    adapter, connection, _ = pg_adapter(asset, connection=Connection(rows, relation_kind=relation_kind, column_types={'id':1700}))
    stream = adapter.extract(batch_size=batch_size)
    if batch_size == 1:
        first = next(stream)
        assert first.records.id.iloc[0].as_tuple() == original[0]
        assert first.records.amount.iloc[0] == ROWS[0][4]
        assert not hasattr(first, 'admitted')
    with pytest.raises(NetworkAdapterError, match='ordering key is not unique'):
        next(stream)
    assert [row[0].as_tuple() for row in rows] == original
    assert connection.closed and connection.rollbacks == 1
    assert all(cursor.closed for cursor in connection.cursors)
    assert not adapter._active and not adapter._resources


@pytest.mark.parametrize('key', ['NaN', 'sNaN', 'Infinity', '-Infinity'])
def test_postgres_nonfinite_decimal_keys_reject_safely(asset, key):
    value = Decimal(key)
    with pytest.raises(NetworkAdapterError, match='unsupported or missing'):
        record_reference([value])
    adapter, connection, _ = pg_adapter(asset, connection=Connection([(value, *ROWS[0][1:])], relation_kind='v', column_types={'id':1700}))
    with pytest.raises(NetworkAdapterError, match='incompatible'):
        list(adapter.extract())
    assert connection.closed and connection.rollbacks == 1 and not adapter._resources


def test_postgres_distinct_decimal_keys_and_signed_targets_remain_exact(asset):
    keys = [Decimal('12345678901234567890.12345678901234567890'),
            Decimal('12345678901234567890.12345678901234567891')]
    targets = [Decimal('-9007199254740993.1234'), Decimal('0.0000')]
    rows = [(keys[i], *ROWS[i][1:4], targets[i], ROWS[i][5]) for i in range(2)]
    original = [(row[0].as_tuple(), row[4].as_tuple()) for row in rows]
    adapter, connection, _ = pg_adapter(asset, connection=Connection(rows, relation_kind='v', column_types={'id':1700, 'amount':1700}))
    batches = list(adapter.extract(batch_size=1))
    assert len({batch.record_ids[0] for batch in batches}) == 2
    assert [(batch.records.id.iloc[0].as_tuple(), batch.records.amount.iloc[0].as_tuple())
            for batch in batches] == original
    mapped = list(CanonicalMapper(mapping()).map_batches(batches))
    assert [batch.records.value.iloc[0] for batch in mapped] == targets
    assert [(row[0].as_tuple(), row[4].as_tuple()) for row in rows] == original
    assert connection.closed


def test_scope_denial_and_organization_mismatch_are_before_credentials(asset):
    adapter, _, calls = pg_adapter(asset, authorize=lambda *_: False)
    with pytest.raises(NetworkAdapterError): adapter.test_access()
    assert not calls
    adapter, _, calls = pg_adapter(asset, config=pg_config(organization_id=UUID(int=999)))
    with pytest.raises(NetworkAdapterError): adapter.test_access()
    assert not calls


@pytest.mark.parametrize('relation_kind', ['r', 'v'])
@pytest.mark.parametrize('oid,key', [
    (21, -32768), (23, 2147483647), (20, 9223372036854775807),
    (1700, Decimal('12345678901234567890.12345678901234567890')),
    (2950, UUID(int=123)), (2950, str(UUID(int=123))),
    (25, 'Leading-001'), (1043, 'Leading-001'),
])
def test_postgres_key_semantics_supported_types_at_boundary(asset, relation_kind, oid, key):
    rows = [(key, *ROWS[0][1:])]
    connection = Connection(rows, relation_kind=relation_kind, column_types={'id':oid},
                            collations={'id':('C', 'pg_catalog', 'c', True, 'C')})
    adapter, _, _ = pg_adapter(asset, connection=connection)
    batches = list(adapter.extract(batch_size=1))
    assert len(batches) == 1 and batches[0].records.id.iloc[0] == key
    assert rows[0][0] is key and batches[0].records.amount.iloc[0] == -12.5
    assert connection.closed and connection.rollbacks == 1
    catalog_query, values = next((q,v) for q,v in connection.queries if 'FROM pg_catalog.pg_attribute' in q)
    assert 'a.atttypid' in catalog_query and 'co.collisdeterministic' in catalog_query
    assert values == ('business', 'observations', ['id'])


@pytest.mark.parametrize('oid,key', [
    (1700, 1), (1700, 1.0), (23, Decimal('1')), (23, True), (21, 32768),
    (25, 1), (2950, 'PASSWORD_SECRET'), (2950, '0000000000000000000000000000007b'),
])
def test_postgres_key_semantics_incompatible_representations_reject_before_yield(asset, oid, key):
    connection = Connection([(key, *ROWS[0][1:])], relation_kind='v', column_types={'id':oid},
                            collations={'id':('C', 'pg_catalog', 'c', True, 'C')})
    adapter, _, _ = pg_adapter(asset, connection=connection)
    with pytest.raises(NetworkAdapterError, match='incompatible') as failure:
        next(adapter.extract())
    assert 'PASSWORD_SECRET' not in str(failure.value)
    assert connection.closed and connection.rollbacks == 1 and all(c.closed for c in connection.cursors)
    assert not adapter._active and not adapter._resources


@pytest.mark.parametrize('metadata', [
    [('id', 999999, 'custom', 'public', *(None,) * 5)],
    [('id', 999998, 'citext', 'public', 'C', 'pg_catalog', 'c', True, 'C')],
    [('id', 999997, 'integer_domain', 'public', *(None,) * 5)],
    [('id', 1082, 'date', 'pg_catalog', *(None,) * 5)],
    [('id', 25, 'citext', 'public', 'C', 'pg_catalog', 'c', True, 'C')],
    [('id', 23, 'int4', 'public', *(None,) * 5)],
    [], [('other', 23, 'int4', 'pg_catalog', *(None,) * 5)],
    [('id', 23, 'int4', 'pg_catalog', *(None,) * 5)] * 2,
])
@pytest.mark.parametrize('operation', ['extract', 'test_access', 'discover_schema'])
def test_postgres_key_semantics_unsupported_catalog_metadata_is_rejected(asset, metadata, operation):
    adapter, connection, _ = pg_adapter(asset, connection=Connection(relation_kind='v', key_catalog=metadata))
    with pytest.raises(NetworkAdapterError):
        if operation == 'extract':
            next(adapter.extract())
        else:
            getattr(adapter, operation)()
    assert connection.closed and connection.rollbacks == 1 and all(c.closed for c in connection.cursors)
    assert not any(c.name for c in connection.cursors)


@pytest.mark.parametrize('collation', [
    (None, None, None, None, None), ('default', 'pg_catalog', 'd', True, None),
    ('C', 'public', 'c', True, 'C'), ('C', 'pg_catalog', 'i', True, 'C'),
    ('C', 'pg_catalog', 'c', False, 'C'), ('en_US', 'pg_catalog', 'c', True, 'en_US'),
])
def test_postgres_key_semantics_unverified_text_collation_rejected_before_yield(asset, collation):
    rows = [('A', *ROWS[0][1:]), ('a', *ROWS[1][1:])]
    adapter, connection, _ = pg_adapter(asset, connection=Connection(rows, relation_kind='v',
        column_types={'id':25}, collations={'id':collation}))
    with pytest.raises(NetworkAdapterError, match='collation'):
        next(adapter.extract())
    assert connection.closed and connection.rollbacks == 1 and not adapter._resources


def test_postgres_key_semantics_cursor_metadata_must_match_catalog(asset, monkeypatch):
    original = Cursor.fetchmany
    def fetch_with_wrong_type(cursor, count):
        rows = original(cursor, count)
        cursor.description[0].type_code = 25
        return rows
    monkeypatch.setattr(Cursor, 'fetchmany', fetch_with_wrong_type)
    adapter, connection, _ = pg_adapter(asset)
    with pytest.raises(NetworkAdapterError, match='result metadata'):
        next(adapter.extract())
    assert connection.closed and connection.rollbacks == 1 and not adapter._resources


def test_postgres_key_semantics_posix_text_keys_preserve_case_distinctions(asset):
    rows = [('A', *ROWS[0][1:]), ('a', *ROWS[1][1:])]
    adapter, connection, _ = pg_adapter(asset, connection=Connection(rows, relation_kind='v',
        column_types={'id':25}, collations={'id':('POSIX', 'pg_catalog', 'c', True, 'POSIX')}))
    batches = list(adapter.extract(batch_size=1))
    assert [batch.records.id.iloc[0] for batch in batches] == ['A', 'a']
    assert len({batch.record_ids[0] for batch in batches}) == 2
    assert connection.closed


def test_postgres_key_semantics_later_batch_mismatch_rejects_and_cleans_up(asset):
    rows = [(Decimal('1'), *ROWS[0][1:]), (1, *ROWS[1][1:])]
    adapter, connection, _ = pg_adapter(asset, connection=Connection(rows, relation_kind='v', column_types={'id':1700}))
    stream = adapter.extract(batch_size=1)
    assert next(stream).records.id.iloc[0] == Decimal('1')
    with pytest.raises(NetworkAdapterError, match='incompatible'):
        next(stream)
    assert connection.closed and connection.rollbacks == 1 and not adapter._resources


@pytest.mark.parametrize('batch_size', [1, 2])
def test_postgres_key_semantics_uuid_representations_share_identity(asset, batch_size):
    key = UUID(int=123)
    rows = [(key, *ROWS[0][1:]), (str(key), *ROWS[1][1:])]
    adapter, connection, _ = pg_adapter(asset, connection=Connection(rows, relation_kind='v', column_types={'id':2950}))
    with pytest.raises(NetworkAdapterError, match='not unique'):
        list(adapter.extract(batch_size=batch_size))
    assert rows[0][0] is key and type(rows[1][0]) is str
    assert connection.closed and connection.rollbacks == 1


def test_rest_page_extraction_authentication_lineage_and_bounds(asset, caplog):
    cfg = rest_config(authentication='bearer', credential_reference='vault-api')
    adapter, transport = rest_adapter(asset, [response(ROWS[:2], more=True), response(ROWS[2:])], config=cfg,
        resolve_credentials=lambda _: RESTCredentials(token='TOKEN_SECRET'))
    with adapter: batches = list(adapter.extract(batch_size=1))
    assert [b.records.amount.iloc[0] for b in batches] == [-12.5, 0., 42.]
    assert [b.asset.metadata['source_page'] for b in batches] == [1, 1, 2]
    assert transport.closed and not adapter._resources and not adapter._active
    assert len(set(sum((list(b.record_ids) for b in batches), []))) == 3
    for url, options in transport.calls:
        assert options['address'] == '93.184.216.34'
        assert options['headers']['Authorization'] == 'Bearer TOKEN_SECRET'
        assert parse_qs(urlsplit(url).query)['organization_id'] == [str(ORG)]
    assert 'TOKEN_SECRET' not in repr(batches) + caplog.text + repr(RESTCredentials(token='TOKEN_SECRET'))


@pytest.mark.parametrize('address', ['127.0.0.1', '::1', '10.0.0.1', '169.254.169.254', '0.0.0.0',
    '224.0.0.1', 'fe80::1', 'fd00::1', '::ffff:127.0.0.1', '192.0.2.1', '8.8.8.8%zone', '168.63.129.16'])
def test_rest_prohibited_destinations_never_receive_credentials(asset, address):
    resolved = []
    adapter, transport = rest_adapter(asset, [response(ROWS[:1])], resolver=lambda *_: (address,),
        config=rest_config(authentication='bearer', credential_reference='vault-api'),
        resolve_credentials=lambda ref: resolved.append(ref))
    with pytest.raises(NetworkAdapterError): list(adapter.extract())
    assert not transport.calls and not resolved and transport.closed


def test_rest_dns_change_fails_before_second_request(asset):
    addresses = iter([('93.184.216.34',), ('127.0.0.1',)])
    adapter, transport = rest_adapter(asset, [response(ROWS[:1], more=True), response(ROWS[1:])],
        resolver=lambda *_: next(addresses))
    stream = adapter.extract(); next(stream)
    with pytest.raises(NetworkAdapterError): next(stream)
    assert len(transport.calls) == 1 and transport.closed and not adapter._resources


def test_rest_mixed_public_private_dns_answers_fail_closed(asset):
    adapter, transport = rest_adapter(asset, [], resolver=lambda *_: ('93.184.216.34', '10.0.0.1'))
    with pytest.raises(NetworkAdapterError): adapter.test_access()
    assert not transport.calls and transport.closed


@pytest.mark.parametrize('updates', [
    {'endpoint': 'http://api.example.test/records', 'allowed_origins': ('http://api.example.test',)},
    {'endpoint': 'https://user:SECRET@api.example.test/records'}, {'allowed_origins': ()},
    {'endpoint': 'https://api.example.test/records?token=SECRET'}, {'pagination': 'next_url'},
    {'page_parameter': 'limit'}, {'max_retries': True}, {'read_timeout': 0},
    {'endpoint': 'https://api.example.test:99999/records'}, {'endpoint': 'https://api.example.test/records#'},
    {'page_parameter': None},
])
def test_rest_invalid_configuration_is_inert(asset, updates):
    adapter, transport = rest_adapter(asset, [], config=rest_config(**updates))
    with pytest.raises(ValueError): adapter.validate_configuration()
    assert not transport.calls


@pytest.mark.parametrize('status', [301, 302, 307, 308])
def test_rest_redirects_never_follow_location(asset, status):
    adapter, transport = rest_adapter(asset, [RESTResponse(status, (('Location', 'http://169.254.169.254/SECRET'),), b'')])
    with pytest.raises(NetworkAdapterError) as failure: list(adapter.extract())
    assert len(transport.calls) == 1 and 'SECRET' not in str(failure.value) and transport.closed


def test_rest_retry_after_and_retry_budget(asset):
    sleeps = []
    adapter, transport = rest_adapter(asset, [response([], status=429, extra_headers=(('Retry-After','2'),)),
        response([], status=503), response(ROWS[:1])], sleep=sleeps.append)
    assert len(list(adapter.extract())) == 1 and sleeps == [2, 2] and len(transport.calls) == 3
    adapter, transport = rest_adapter(asset, [response([], status=429, extra_headers=(('Retry-After','999'),))], sleep=sleeps.append)
    with pytest.raises(NetworkAdapterError): list(adapter.extract())
    assert len(transport.calls) == 1 and transport.closed


def test_rest_timeouts_retry_only_bounded_safe_gets(asset):
    sleeps = []
    adapter, transport = rest_adapter(asset, [TimeoutError('SECRET'), ConnectionError('SECRET'), response(ROWS[:1])], sleep=sleeps.append)
    assert len(list(adapter.extract())) == 1 and sleeps == [1, 2]
    adapter, transport = rest_adapter(asset, [TimeoutError('SECRET')] * 3, sleep=lambda _: None)
    with pytest.raises(NetworkAdapterError) as failure: list(adapter.extract())
    assert len(transport.calls) == 3 and 'SECRET' not in str(failure.value) and transport.closed


@pytest.mark.parametrize('body', [b'not json SECRET', b'[{"x":NaN}]', b'[{"x":1,"x":2}]',
    b'{"records":[],"has_more":false}', b'{"records":[1],"has_more":false}',
    b'{"records":[{"id":1}],"has_more":"false"}', b'{"records":[{"id":1}],"has_more":false,"extra":1}'])
def test_rest_malformed_shapes_are_sanitized_and_cleaned(asset, body):
    adapter, transport = rest_adapter(asset, [RESTResponse(200, (('Content-Type','application/json'),), body)])
    with pytest.raises(NetworkAdapterError) as failure: list(adapter.extract())
    assert 'SECRET' not in str(failure.value) and transport.closed and not adapter._active


def test_rest_cursor_cycles_and_duplicate_records(asset):
    cfg = rest_config(pagination='cursor')
    adapter, transport = rest_adapter(asset, [response(ROWS[:1], cursor='token', mode='cursor'),
        response(ROWS[1:2], cursor='token', mode='cursor')], config=cfg)
    with pytest.raises(NetworkAdapterError, match='cyclic'): list(adapter.extract())
    assert transport.closed
    adapter, transport = rest_adapter(asset, [response(ROWS[:1], more=True), response(ROWS[:1])])
    with pytest.raises(NetworkAdapterError, match='duplicate'): list(adapter.extract())
    assert transport.closed


def test_rest_valid_cursor_encoding_basic_auth_and_schema_discovery(asset):
    cfg = rest_config(pagination='cursor', authentication='basic', credential_reference='vault-api')
    adapter, transport = rest_adapter(asset, [response(ROWS[:1], cursor='a&b=/next', mode='cursor'),
        response(ROWS[1:2], mode='cursor')], config=cfg,
        resolve_credentials=lambda _: RESTCredentials(username='reader', password='SECRET'))
    assert len(list(adapter.extract())) == 2
    assert parse_qs(urlsplit(transport.calls[1][0]).query)['cursor'] == ['a&b=/next']
    assert transport.calls[0][1]['headers']['Authorization'].startswith('Basic ')
    adapter, transport = rest_adapter(asset, [response(ROWS[:1])])
    assert list(adapter.discover_schema()) == list(NAMES) and transport.closed


@pytest.mark.parametrize('updates', [{'max_response_bytes': 1}, {'max_request_bytes': 1}, {'max_rows': 1, 'page_size': 1}, {'max_pages': 1}])
def test_rest_limits_fail_closed(asset, updates):
    adapter, transport = rest_adapter(asset, [response(ROWS[:2], more=True)], config=rest_config(**updates))
    with pytest.raises(NetworkAdapterError): list(adapter.extract())
    assert transport.closed and not adapter._active


def test_rest_generator_cancellation_closes_transport(asset):
    adapter, transport = rest_adapter(asset, [response(ROWS[:2], more=True)])
    stream = adapter.extract(batch_size=1); next(stream); stream.close()
    assert transport.closed and not adapter._active and not adapter._resources
    adapter.close(); adapter.close()
    with pytest.raises(NetworkAdapterError): adapter.test_access()


def test_network_selection_is_inert_and_requires_both_authorizations(asset):
    calls = []
    registry = network_connector_registry(asset, authorize_scope=lambda *_: calls.append('scope'),
        postgres=pg_config(), rest=rest_config(), resolve_postgres_credentials=lambda _: calls.append('credentials'),
        postgres_connect=lambda **_: calls.append('connect'), rest_resolver=lambda *_: calls.append('dns'))
    for kind, reference in [(SourceType.POSTGRESQL, 'database-import'), (SourceType.REST, 'api-import')]:
        with pytest.raises(SourceSelectionError): select_connector(SourceConfiguration(reference,kind), registry)
        adapter = select_connector(SourceConfiguration(reference,kind), registry, authorize_network=lambda _: True)
        assert isinstance(adapter,SourceAdapter) and calls == []
        adapter.close()
    with pytest.raises(NetworkAdapterError):
        select_connector(SourceConfiguration('other',SourceType.REST),registry,authorize_network=lambda _:True)


def test_postgres_and_rest_share_unchanged_canonical_mapper(asset):
    postgres, _, _ = pg_adapter(asset)
    rest, _ = rest_adapter(asset, [response(ROWS[:2],more=True), response(ROWS[2:])])
    canonical = []
    for adapter in (postgres,rest):
        with adapter: batches=list(CanonicalMapper(mapping()).map_batches(adapter.extract(batch_size=1)))
        records=pd.concat([b.records for b in batches],ignore_index=True)
        canonical.append(records[['series_id','period_start','period_end','metric','value','unit','currency','flag']].astype(object))
        assert records.value.tolist()==[-12.5,0.,42.]
        assert all(b.dataset.quality is None and not hasattr(b,'admitted') for b in batches)
        assert all(b.dataset.lineage[0].mapping_version=='1.0' for b in batches)
    pd.testing.assert_frame_equal(*canonical)


def test_https_transport_pins_address_and_keeps_original_sni(monkeypatch):
    calls=[]
    class FakeSocket:
        def settimeout(self,value):calls.append(('timeout',value))
        def connect(self,value):calls.append(('connect',value))
        def close(self):calls.append(('close',))
    connection=_PinnedHTTPSConnection('api.example.test',443,'93.184.216.34',timeout=3)
    assert connection._context.check_hostname and connection._context.verify_mode==ssl.CERT_REQUIRED
    raw=FakeSocket();monkeypatch.setattr(socket,'socket',lambda *_:raw)
    connection._context=SimpleNamespace(wrap_socket=lambda sock,server_hostname:calls.append(('sni',server_hostname)) or sock)
    connection.connect();connection.close()
    assert ('connect',('93.184.216.34',443)) in calls and ('sni','api.example.test') in calls


def test_real_loopback_http_transport_is_controlled_and_cleans_up(asset):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            page=int(parse_qs(urlsplit(self.path).query)['page'][0])
            item=response(ROWS[:2] if page==1 else ROWS[2:],more=page==1)
            self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(item.body)));self.end_headers();self.wfile.write(item.body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin='http://127.0.0.1:'+str(server.server_port)
    transport=PinnedHTTPTransport()
    adapter=RESTAdapter(rest_config(endpoint=origin+'/records',allowed_origins=(origin,)),asset,
        authorize_scope=lambda *_:True,transport_factory=lambda:transport,
        policy=RESTNetworkPolicy(allow_loopback_for_tests=True,allow_http_for_tests=True))
    try:
        with adapter:batches=list(adapter.extract(batch_size=1))
        assert [b.records.amount.iloc[0] for b in batches]==[-12.5,0.,42.]
        assert transport.closed and transport.connection is None
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


def test_header_deadline_rejects_slow_trickle(monkeypatch):
    times=iter([0,0.25,2])
    monkeypatch.setattr('src.data.adapters.rest_adapter.time.monotonic',lambda:next(times))
    sock=SimpleNamespace(gettimeout=lambda:1,settimeout=lambda _:None)
    reader=_DeadlineReader(io.BytesIO(b'HTTP/1.1 200 OK\r\n'),sock)
    with pytest.raises(TimeoutError):reader.readline()


def test_postgres_references_stable_across_batch_sizes(asset):
    ids=[]
    for size in (1,3):
        adapter, connection, _=pg_adapter(asset)
        with adapter:batches=list(adapter.extract(batch_size=size))
        ids.append(sum((list(b.record_ids) for b in batches),[]))
        timeout_query=next(values for query,values in connection.queries if 'statement_timeout' in query)
        assert timeout_query==('30000','30000')
        assert any('has_any_column_privilege' in query for query,_ in connection.queries)
    assert ids[0]==ids[1]


def test_postgres_unique_key_catalog_match_does_not_depend_on_database_collation(asset):
    adapter,connection,_=pg_adapter(asset,config=pg_config(record_key_fields=('site','id')))
    with adapter:list(adapter.extract())
    query,values=next((q,v) for q,v in connection.queries if 'FROM pg_index' in q)
    assert '@> %s::text[]' in query and 'ORDER BY a.attname' not in query
    assert values==(100,2,['site','id'])


def test_postgres_fetch_timeout_and_cleanup_failure_are_sanitized(asset,monkeypatch):
    adapter,connection,_=pg_adapter(asset)
    monkeypatch.setattr(Cursor,'fetchmany',lambda *_:(_ for _ in ()).throw(TimeoutError('PASSWORD_SECRET')))
    with pytest.raises(NetworkAdapterError) as failure:list(adapter.extract())
    assert 'PASSWORD_SECRET' not in str(failure.value) and connection.closed and connection.rollbacks==1
    monkeypatch.undo()
    adapter,connection,_=pg_adapter(asset)
    original_close=connection.close
    def failed_close():
        original_close();raise RuntimeError('PASSWORD_SECRET')
    connection.close=failed_close
    with pytest.raises(NetworkAdapterError,match='cleanup'):list(adapter.extract())
    assert connection.closed and not adapter._resources and not adapter._active


@pytest.mark.parametrize('updates', [
    {'token':'bad\r\nInjected: secret'}, {'token':''}, {'token':123},
])
def test_rest_invalid_auth_never_reaches_transport(asset,updates):
    adapter,transport=rest_adapter(asset,[],config=rest_config(authentication='bearer',credential_reference='vault-api'),
        resolve_credentials=lambda _:RESTCredentials(**updates))
    with pytest.raises(NetworkAdapterError):adapter.test_access()
    assert not transport.calls and transport.closed


def test_rest_scope_denial_is_before_dns_transport_or_credentials(asset):
    calls=[]
    adapter=RESTAdapter(rest_config(authentication='bearer',credential_reference='vault-api'),asset,
        authorize_scope=lambda *_:False,resolve_credentials=lambda _:calls.append('secret'),
        transport_factory=lambda:calls.append('transport'),resolver=lambda *_:calls.append('dns'))
    with pytest.raises(NetworkAdapterError):adapter.test_access()
    assert not calls


@pytest.mark.parametrize('headers', [
    (('Content-Type','text/plain'),),
    (('Content-Type','application/json'),('Content-Encoding','gzip')),
])
def test_rest_unsupported_content_headers_fail_closed(asset,headers):
    adapter,transport=rest_adapter(asset,[RESTResponse(200,headers,response(ROWS[:1]).body)])
    with pytest.raises(NetworkAdapterError):list(adapter.extract())
    assert transport.closed and len(transport.calls)==1


def test_rest_http_date_retry_after_and_total_budget(asset,monkeypatch):
    monkeypatch.setattr('src.data.adapters.rest_adapter.datetime',SimpleNamespace(
        now=lambda _:datetime(2026,1,1,tzinfo=timezone.utc)))
    sleeps=[]
    adapter,transport=rest_adapter(asset,[response([],status=429,extra_headers=(('Retry-After','Thu, 01 Jan 2026 00:00:02 GMT'),)),
        response(ROWS[:1])],sleep=sleeps.append)
    assert len(list(adapter.extract()))==1 and sleeps==[2]
    adapter,transport=rest_adapter(asset,[response([],status=429,extra_headers=(('Retry-After','2'),))],
        config=rest_config(max_total_retry_delay=1),sleep=sleeps.append)
    with pytest.raises(NetworkAdapterError,match='budget'):list(adapter.extract())
    assert len(transport.calls)==1 and sleeps==[2]


@pytest.mark.parametrize('body', [
    b'{"records":[{"id":1,"nested":[Infinity]}],"has_more":false}',
    b'{"records":[{"id":1,"nested":[[[[1]]]]}],"has_more":false}',
])
def test_rest_nested_values_respect_numeric_and_depth_limits(asset,body):
    adapter,transport=rest_adapter(asset,[RESTResponse(200,(('Content-Type','application/json'),),body)],
        config=rest_config(max_depth=3))
    with pytest.raises(NetworkAdapterError):list(adapter.extract())
    assert transport.closed


def test_rest_schema_change_and_consumer_exception_close_sessions(asset):
    changed={'records':[{'id':2,'different':True}],'has_more':False}
    adapter,transport=rest_adapter(asset,[response(ROWS[:1],more=True),
        RESTResponse(200,(('Content-Type','application/json'),),json.dumps(changed).encode())])
    with pytest.raises(NetworkAdapterError,match='schema changed'):list(adapter.extract())
    assert transport.closed
    adapter,transport=rest_adapter(asset,[response(ROWS[:2],more=True)])
    stream=adapter.extract(batch_size=1)
    with pytest.raises(RuntimeError):
        with adapter:
            next(stream);raise RuntimeError('consumer failed')
    stream.close()
    assert transport.closed and not adapter._resources and not adapter._active


def test_rest_test_policy_never_permits_public_plain_http(asset):
    cfg=rest_config(endpoint='http://api.example.test/records',allowed_origins=('http://api.example.test',))
    adapter,transport=rest_adapter(asset,[],config=cfg,
        policy=RESTNetworkPolicy(allow_loopback_for_tests=True,allow_http_for_tests=True))
    with pytest.raises(NetworkAdapterError):adapter.test_access()
    assert not transport.calls and transport.closed


def test_rest_dns_and_secret_resolver_errors_are_sanitized(asset):
    def failure(*args):raise RuntimeError('TOKEN_SECRET')
    adapter,transport=rest_adapter(asset,[],resolver=failure)
    with pytest.raises(NetworkAdapterError) as error:adapter.test_access()
    assert 'TOKEN_SECRET' not in str(error.value) and not transport.calls and transport.closed

    adapter,transport=rest_adapter(asset,[],config=rest_config(authentication='bearer',credential_reference='vault-api'),
        resolve_credentials=failure)
    with pytest.raises(NetworkAdapterError) as error:adapter.test_access()
    assert 'TOKEN_SECRET' not in str(error.value) and not transport.calls and transport.closed


def test_default_dns_wait_and_worker_capacity_are_bounded(monkeypatch):
    semaphore=threading.BoundedSemaphore(1)
    monkeypatch.setattr('src.data.adapters.rest_adapter._dns_slots',semaphore)
    release=threading.Event();calls=[]
    def blocked_lookup(*args,**kwargs):
        calls.append(1);release.wait(timeout=2)
        return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]
    monkeypatch.setattr(socket,'getaddrinfo',blocked_lookup)
    try:
        with pytest.raises(NetworkAdapterError,match='timed out'):
            resolve_addresses('api.example.test',443,timeout=0.01)
        with pytest.raises(NetworkAdapterError,match='capacity'):
            resolve_addresses('api.example.test',443,timeout=0.01)
        assert calls==[1]
    finally:
        release.set()
        assert semaphore.acquire(timeout=2)
        semaphore.release()


@pytest.mark.parametrize('fault',['redirect','oversized','truncated','header'])
def test_actual_loopback_transport_rejects_unsafe_http_responses(asset,fault):
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            body=response(ROWS[:1]).body
            self.send_response(302 if fault=='redirect' else 200)
            self.send_header('Content-Type','application/json')
            if fault=='redirect':self.send_header('Location','http://169.254.169.254/SECRET')
            if fault=='header':self.send_header('X-Secret','SECRET'+'x'*9000)
            self.send_header('Content-Length',str(len(body)+100 if fault=='truncated' else len(body)))
            self.end_headers()
            try:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin='http://127.0.0.1:'+str(server.server_port)
    transport=PinnedHTTPTransport()
    cfg=rest_config(endpoint=origin+'/records',allowed_origins=(origin,),max_response_bytes=1 if fault=='oversized' else 4096)
    adapter=RESTAdapter(cfg,asset,authorize_scope=lambda *_:True,transport_factory=lambda:transport,
        policy=RESTNetworkPolicy(allow_loopback_for_tests=True,allow_http_for_tests=True))
    try:
        with pytest.raises(NetworkAdapterError) as failure:list(adapter.extract())
        assert 'SECRET' not in str(failure.value)
        assert len(calls)==1 and transport.closed and transport.connection is None and not adapter._resources
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
