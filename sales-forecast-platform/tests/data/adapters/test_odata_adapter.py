"""Synthetic OData 4.0 protocol fixtures; no vendor or customer endpoints."""
from dataclasses import replace
from datetime import datetime, timezone, date
from decimal import Decimal, localcontext, Inexact, Rounded
import hashlib
import json
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode, urlsplit, parse_qs
from uuid import UUID

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.data.adapters.base import SourceAdapter, SourceType
from src.data.adapters.network_common import NetworkAdapterError
from src.data.adapters.network_registry import network_connector_registry
from src.data.adapters.odata_adapter import ODataAdapter, ODataConfiguration
from src.data.adapters.parquet_adapter import ParquetAdapter, ParquetConfiguration
from src.data.adapters.rest_adapter import RESTAdapter, RESTConfiguration, RESTResponse, RESTCredentials, RESTNetworkPolicy
from src.data.adapters.selection import SourceConfiguration, SourceSelectionError, select_connector
from src.data.contracts import AssetMetadata, SourceMapping
from src.data.processing.canonical_mapper import CanonicalMapper


ROOT = 'https://odata.example.test/org-a/'
FIELDS = ('Id', 'Location', 'Start', 'End', 'Amount', 'Flag', 'Optional', 'Calendar')
ORG = UUID(int=200)
METADATA = b'''<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0">
 <edmx:DataServices><Schema xmlns="http://docs.oasis-open.org/odata/ns/edm" Namespace="Example">
  <EntityType Name="Record"><Key><PropertyRef Name="Id"/></Key>
   <Property Name="Id" Type="Edm.Int64" Nullable="false"/>
   <Property Name="Location" Type="Edm.String" Nullable="false" MaxLength="64"/>
   <Property Name="Start" Type="Edm.DateTimeOffset" Nullable="false" Precision="7"/>
   <Property Name="End" Type="Edm.DateTimeOffset" Nullable="false" Precision="7"/>
   <Property Name="Amount" Type="Edm.Decimal" Nullable="false" Precision="24" Scale="4"/>
   <Property Name="Flag" Type="Edm.Boolean" Nullable="false"/>
   <Property Name="Optional" Type="Edm.String" Nullable="true"/>
   <Property Name="Calendar" Type="Edm.Date" Nullable="true"/>
  </EntityType>
  <EntityContainer Name="Service"><EntitySet Name="Records" EntityType="Example.Record"/></EntityContainer>
 </Schema></edmx:DataServices>
</edmx:Edmx>'''
ROWS = [dict(zip(FIELDS, values)) for values in (
    ('9007199254740993', '001', '2026-01-01T00:00:00Z', '2026-01-08T00:00:00Z', '-9007199254740993.1234', True, None, '2026-01-01'),
    ('9007199254740994', '001', '2026-01-08T00:00:00Z', '2026-01-15T00:00:00Z', '0.0000', False, 'NA', None),
)]


@pytest.fixture
def asset():
    return AssetMetadata(asset_id=UUID(int=100), organization_id=ORG, source_id=UUID(int=300),
        ingestion_batch_id=UUID(int=400), created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))


def config(**updates):
    cfg = ODataConfiguration(source_reference='enterprise-import', source_dataset='sales-import',
        organization_id=ORG, record_key_fields=('Id',), service_root=ROOT,
        allowed_origins=('https://odata.example.test',), entity_set='Records',
        projected_fields=FIELDS, order_by=('Id',), page_size=2,
        credential_reference='vault-reader', authorization_context='approved-company-a',
        scope_parameters=(('company', 'A'),))
    return replace(cfg, **updates)


def response(body, *, metadata=False, status=200, extra_headers=(), ieee=True):
    return RESTResponse(status, (('Content-Type', 'application/xml' if metadata else
        'application/json;IEEE754Compatible=' + ('true' if ieee else 'false')),
        ('OData-Version', '4.0'), *extra_headers), body)


def page(rows=ROWS, *, link=None, root=ROOT, extra=None, ieee=True):
    value = {'@odata.context': root + '$metadata#Records', 'value': rows}
    if link is not None:
        value['@odata.nextLink'] = link
    value.update(extra or {})
    return response(json.dumps(value).encode(), ieee=ieee)


class Transport:
    def __init__(self, responses):
        self.responses, self.calls, self.closed = list(responses), [], False
    def request(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    def close(self):
        self.closed = True


def adapter(asset, responses, *, cfg=None, resolver=None, authorize=None, purposes=None, provider=None, **kwargs):
    cfg = cfg or config()
    transport = Transport(responses)
    secrets = []
    def credentials(reference, **context):
        secrets.append((reference, context))
        return RESTCredentials(token='TOKEN_SECRET')
    source = ODataAdapter(cfg, asset, authorize_scope=authorize or (lambda *_: True),
        authorize_request=purposes or (lambda *_: True), resolve_credentials=provider or credentials,
        resolver=resolver or (lambda *_: ('93.184.216.34',)), transport_factory=lambda: transport, **kwargs)
    return source, transport, secrets


def continuation(cfg=None, token='second', *, absolute=False, changes=None):
    cfg = cfg or config()
    fixed = dict(cfg.scope_parameters)
    fixed.update({'$select':','.join(cfg.projected_fields), '$orderby':','.join(k + ' asc' for k in cfg.order_by),
                  '$top':str(cfg.page_size), '$skiptoken':token})
    fixed.update(changes or {})
    return (cfg.service_root if absolute else '') + cfg.entity_set + '?' + urlencode(fixed)


def mapping():
    return SourceMapping(mapping_id='enterprise-test', version='1.0', organization_id=ORG, business_scope='retail',
        field_mappings={'location':'Location', 'start':'Start', 'end':'End', 'amount':'Amount', 'flag':'Flag',
                        'optional':'Optional', 'calendar':'Calendar'},
        identity_dimensions=('location',), target_metric='reported_sales', target_value_field='amount',
        metric_meaning='signed reported sales', sign_semantics='source signed reported values',
        policy_metric='sales', policy_context='signed_reported_training', period_start_field='start', period_end_field='end',
        period_interpretation='explicit_bounds', frequency='weekly', timezone='UTC', unit_required=True,
        currency_required=True, required_auxiliary_fields=('flag',),
        measurement={'metric':'reported_sales', 'unit':'amount', 'currency':'SAR'})


def test_selection_requires_network_authorization_and_is_inert(asset):
    calls = []
    registry = network_connector_registry(asset, authorize_scope=lambda *_: calls.append('scope'), odata=config(),
        authorize_odata_request=lambda *_: calls.append('purpose'), resolve_odata_credentials=lambda *a,**k: calls.append('secret'),
        odata_resolver=lambda *_: calls.append('dns'), odata_transport_factory=lambda: calls.append('transport'))
    selected = SourceConfiguration('enterprise-import', SourceType.ODATA)
    with pytest.raises(SourceSelectionError):
        select_connector(selected, registry)
    with select_connector(selected, registry, authorize_network=lambda _: True) as source:
        assert isinstance(source, SourceAdapter) and source.identify_source_type() is SourceType.ODATA
    assert calls == []
    with pytest.raises(NetworkAdapterError):
        select_connector(SourceConfiguration('other', SourceType.ODATA), registry, authorize_network=lambda _: True)


def test_metadata_discovery_and_fingerprint(asset):
    cfg = config(expected_metadata_fingerprint=hashlib.sha256(METADATA).hexdigest())
    source, transport, secrets = adapter(asset, [response(METADATA, metadata=True)], cfg=cfg)
    assert source.discover_schema() == dict(zip(FIELDS, ('Edm.Int64','Edm.String','Edm.DateTimeOffset',
        'Edm.DateTimeOffset','Edm.Decimal','Edm.Boolean','Edm.String','Edm.Date')))
    assert transport.closed and not source._resources
    assert secrets[0][1] == dict(organization_id=ORG, authorization_context=cfg.authorization_context, purpose='metadata')


def test_precise_scalars_mapping_lineage_and_no_admission(asset):
    source, transport, secrets = adapter(asset, [response(METADATA, metadata=True), page()])
    with localcontext() as context:
        context.prec = 2; context.traps[Inexact] = context.traps[Rounded] = True
        with source:
            batches = list(source.extract(batch_size=1))
    assert [b.records.Id.iloc[0] for b in batches] == [9007199254740993, 9007199254740994]
    assert [b.records.Amount.iloc[0] for b in batches] == [Decimal('-9007199254740993.1234'), Decimal('0.0000')]
    assert batches[0].records.Calendar.iloc[0] == date(2026,1,1)
    assert batches[0].records.Optional.iloc[0] is None and batches[1].records.Optional.iloc[0] == 'NA'
    mapped = list(CanonicalMapper(mapping()).map_batches(batches))
    assert [b.records.value.iloc[0] for b in mapped] == [Decimal('-9007199254740993.1234'), Decimal('0')]
    assert all(b.dataset.quality is None and not hasattr(b, 'admitted') for b in mapped)
    assert all(b.dataset.lineage[0].mapping_version == '1.0' for b in mapped)
    assert all(b.asset.metadata['odata_schema_fingerprint'] == hashlib.sha256(METADATA).hexdigest() for b in batches)
    assert 'TOKEN_SECRET' not in repr(mapped) + repr(batches)
    assert [context['purpose'] for _,context in secrets] == ['metadata','entity']
    assert transport.closed


@pytest.mark.parametrize('absolute', [False,True])
def test_relative_and_absolute_continuation_and_stable_keys(asset, absolute):
    ids = []
    for size in (1,2):
        source, transport, _ = adapter(asset, [response(METADATA, metadata=True),
            page(ROWS[:1], link=continuation(absolute=absolute)), page(ROWS[1:])])
        batches = list(source.extract(batch_size=size))
        ids.append(sum((list(b.record_ids) for b in batches), []))
        assert len(transport.calls) == 3 and transport.closed
    assert ids[0] == ids[1]


@pytest.mark.parametrize('body', [
    b'not XML SECRET', METADATA.replace(b'Version="4.0"', b'Version="2.0"'),
    b'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///SECRET">]><x>&secret;</x>',
    METADATA.replace(b'<edmx:DataServices>', b'<edmx:Reference Uri="https://SECRET/"/><edmx:DataServices>'),
    METADATA.replace(b'Name="Records"', b'Name="Other"'),
    METADATA.replace(b'Type="Edm.Decimal"', b'Type="Edm.Double"'),
    METADATA.replace(b'Precision="24"', b'Precision="39"'),
    METADATA.replace(b'Scale="4"', b'Scale="variable"'),
    METADATA.replace(b'Name="Id" Type="Edm.Int64" Nullable="false"', b'Name="Id" Type="Edm.Int64" Nullable="true"'),
    METADATA.replace(b'Name="Record"', b'Name="Record" BaseType="Example.Other"'),
    METADATA.replace(b'<PropertyRef Name="Id"/>', b'<PropertyRef Name="Location"/>'),
    METADATA.replace(b'</EntityType>', b'<Property Name="Amount" Type="Edm.Decimal"/></EntityType>'),
])
def test_invalid_metadata_rejects_safely(asset, body):
    source, transport, _ = adapter(asset, [response(body, metadata=True)])
    with pytest.raises(NetworkAdapterError) as failure:
        list(source.extract())
    assert 'SECRET' not in str(failure.value) and len(transport.calls) == 1
    assert transport.closed and not source._resources and not source._active


@pytest.mark.parametrize('updates', [
    {'max_metadata_bytes':10}, {'max_metadata_nodes':2}, {'max_metadata_depth':2},
    {'expected_metadata_fingerprint':'0'*64},
])
def test_metadata_bounds_and_schema_changes(asset, updates):
    source, transport, _ = adapter(asset, [response(METADATA, metadata=True)], cfg=config(**updates))
    with pytest.raises(NetworkAdapterError): source.discover_schema()
    assert transport.closed


@pytest.mark.parametrize('updates', [
    {'protocol_version':'4.01'}, {'allowed_origins':()}, {'record_key_fields':('Missing',)},
    {'projected_fields':('Id','Id')}, {'order_by':('Amount',)}, {'service_root':ROOT+'?company=B'},
    {'service_root':ROOT.replace('/org-a/','/%6frg-a/')}, {'max_retries':True},
    {'credential_reference':'https://SECRET/'}, {'authorization_context':' '},
    {'scope_parameters':(('company','A'),('company','B'))}, {'expected_metadata_fingerprint':'invalid'},
    {'max_metadata_depth':65}, {'connect_timeout':61},
    {'service_root':42}, {'expected_metadata_fingerprint':123},
])
def test_invalid_configuration_is_inert(asset, updates):
    source, transport, secrets=adapter(asset,[],cfg=config(**updates))
    with pytest.raises(ValueError):source.validate_configuration()
    assert not secrets and not transport.calls


@pytest.mark.parametrize('headers', [
    (('OData-Version','2.0'),), (('Content-Type','text/plain'),),
    (('Content-Encoding','gzip'),), (('Content-Type','application/json;IEEE754Compatible=invalid'),),
])
def test_response_protocol_and_encoding_headers_fail_closed(asset,headers):
    source,transport,_=adapter(asset,[response(METADATA,metadata=True,extra_headers=headers)])
    with pytest.raises(NetworkAdapterError):source.discover_schema()
    assert transport.closed


@pytest.mark.parametrize('version,media', [
    ('4.01','application/xml'), ('4.0','application/xml;IEEE754Compatible=unknown'),
    ('4.0','application/xml;IEEE754Compatible="true'), ('4.0','application/xml;charset=latin-1'),
])
def test_single_protocol_headers_are_actually_validated(asset,version,media):
    item=RESTResponse(200,(('OData-Version',version),('Content-Type',media)),METADATA)
    source,transport,_=adapter(asset,[item])
    with pytest.raises(NetworkAdapterError):source.discover_schema()
    assert transport.closed


@pytest.mark.parametrize('field', ['Id','Amount'])
def test_missing_projected_fields_reject_before_yield(asset,field):
    row={key:value for key,value in ROWS[0].items() if key!=field}
    source,transport,_=adapter(asset,[response(METADATA,metadata=True),page([row])])
    with pytest.raises(NetworkAdapterError,match='missing or changed'):next(source.extract())
    assert transport.closed and not source._resources


def test_metadata_alias_and_int32_decoding(asset):
    body=METADATA.replace(b'Namespace="Example"',b'Namespace="Example" Alias="E"').replace(
        b'EntityType="Example.Record"',b'EntityType="E.Record"').replace(b'Type="Edm.Int64"',b'Type="Edm.Int32"')
    source,_,_=adapter(asset,[response(body,metadata=True),page([{**ROWS[0],'Id':123}])])
    assert next(source.extract()).records.Id.iloc[0]==123


@pytest.mark.parametrize('values', [('1.0','1.00'),('0.0','-0.00')])
@pytest.mark.parametrize('split', [False,True])
def test_decimal_entity_keys_preserve_numeric_equality(asset,values,split):
    body=METADATA.replace(b'Type="Edm.Int64"',b'Type="Edm.Decimal" Precision="38" Scale="4"')
    rows=[{**row,'Id':key} for row,key in zip(ROWS,values)]
    responses=[response(body,metadata=True)]
    responses += [page(rows[:1],link=continuation()),page(rows[1:])] if split else [page(rows)]
    source,transport,_=adapter(asset,responses)
    stream=source.extract(batch_size=1)
    if split:
        first=next(stream)
        assert first.records.Id.iloc[0]==Decimal(values[0])
    with pytest.raises(NetworkAdapterError,match='duplicate'):next(stream)
    assert transport.closed and not source._resources


def test_key_configuration_order_does_not_change_identity(asset):
    body=METADATA.replace(b'<PropertyRef Name="Id"/>',b'<PropertyRef Name="Id"/><PropertyRef Name="Location"/>')
    results=[]
    for keys in (('Id','Location'),('Location','Id')):
        source,_,_=adapter(asset,[response(body,metadata=True),page()],cfg=config(record_key_fields=keys,order_by=keys))
        results.append(list(source.extract())[0].record_ids)
    assert results[0]==results[1]


@pytest.mark.parametrize('provider', [lambda *a,**k:RESTCredentials(token='bad\r\nSECRET'),
                                     lambda *a,**k:RESTCredentials(token=''),
                                     lambda *a,**k:None])
def test_invalid_bearer_credentials_never_reach_transport(asset,provider):
    source,transport,_=adapter(asset,[],provider=provider)
    with pytest.raises(NetworkAdapterError) as failure:source.discover_schema()
    assert 'SECRET' not in str(failure.value) and not transport.calls and transport.closed


def test_credential_provider_exception_is_sanitized(asset):
    def failing(*args,**kwargs):raise RuntimeError('TOKEN_SECRET')
    source,transport,_=adapter(asset,[],provider=failing)
    with pytest.raises(NetworkAdapterError) as failure:source.discover_schema()
    assert 'TOKEN_SECRET' not in str(failure.value) and not transport.calls and transport.closed


def test_request_limits_and_retry_total_budget(asset):
    source,transport,secrets=adapter(asset,[],cfg=config(max_request_bytes=1))
    with pytest.raises(NetworkAdapterError):source.discover_schema()
    assert not transport.calls and not secrets
    source,transport,_=adapter(asset,[response(b'',status=429,extra_headers=(('Retry-After','2'),))],
        cfg=config(max_total_retry_delay=1),sleep=lambda _:pytest.fail('must not sleep'))
    with pytest.raises(NetworkAdapterError,match='budget'):source.discover_schema()
    assert len(transport.calls)==1 and transport.closed


@pytest.mark.parametrize('field,value', [
    ('Id', 9007199254740993), ('Id','9223372036854775808'), ('Id',None), ('Id','1.0'),
    ('Amount', 1.25), ('Amount','NaN'), ('Amount','Infinity'), ('Amount','1.00001'),
    ('Amount','999999999999999999999.0000'), ('Flag','true'), ('Flag',None),
    ('Start','2026-01-01T00:00:00'), ('Start','2026-01-01T00:00:00+15:00'),
    ('Start','2026-01-01T00:00:00-00:00'),
    ('Start','2026-01-01T00:00:00.12345678Z'), ('Start','1200-01-01T00:00:00Z'),
    ('Calendar','2026-02-30'), ('Optional',{'nested':1}), ('Location',['001']),
    ('Location','\ud800'),
])
def test_bad_edm_values_reject_before_yield(asset, field, value):
    rows = [{**ROWS[0], field:value}]
    source, transport, _ = adapter(asset, [response(METADATA, metadata=True), page(rows)])
    with pytest.raises(NetworkAdapterError): next(source.extract())
    assert transport.closed and not source._resources and not source._active


def test_numeric_json_wire_format_is_exact_and_dates_preserve_instants(asset):
    payload = json.dumps({'@odata.context':ROOT+'$metadata#Records', 'value':[
        {**ROWS[0], 'Id':9007199254740993, 'Amount':'RAW_DECIMAL', 'Start':'2026-01-01T03:00:00.1234567+03:00'}]}).replace('"RAW_DECIMAL"', '-9007199254740993.1234')
    source, _, _ = adapter(asset, [response(METADATA, metadata=True), response(payload.encode(), ieee=False)])
    batch = next(source.extract())
    assert batch.records.Amount.iloc[0] == Decimal('-9007199254740993.1234')
    assert batch.records.Start.iloc[0] == pd.Timestamp('2026-01-01T00:00:00.1234567Z')


@pytest.mark.parametrize('link', [
    'https://evil.example.test/Records', '//evil.example.test/Records',
    ROOT+'Other', ROOT.replace('/org-a/','/org-b/')+'Records',
    ROOT+'%52ecords', ROOT+'%252e%252e/Records', ROOT+'../Records', ROOT+'Records/extra',
    ROOT+'Records#fragment', ROOT+'Records\\extra', ROOT+'Records?$skiptoken=x&$skiptoken=y',
    continuation(changes={'company':'B'}), continuation(changes={'$select':'Id'}),
    continuation(changes={'$orderby':'Amount desc'}), continuation(changes={'$filter':'company eq B'}),
    '',
])
def test_continuation_scope_escape_never_receives_credentials(asset, link):
    source, transport, secrets = adapter(asset, [response(METADATA, metadata=True), page(ROWS[:1], link=link)])
    with pytest.raises(NetworkAdapterError): list(source.extract())
    assert len(transport.calls) == len(secrets) == 2 and transport.closed


@pytest.mark.parametrize('address', ['127.0.0.1','10.0.0.1','169.254.169.254','::ffff:127.0.0.1','224.0.0.1','168.63.129.16'])
def test_ssrf_rejects_before_secrets(asset, address):
    source, transport, secrets = adapter(asset, [], resolver=lambda *_:(address,))
    with pytest.raises(NetworkAdapterError): source.discover_schema()
    assert not secrets and not transport.calls and transport.closed


def test_dns_rebinding_on_later_page_rejects_before_credentials(asset):
    addresses = iter([('93.184.216.34',),('93.184.216.34',),('127.0.0.1',)])
    source, transport, secrets = adapter(asset, [response(METADATA, metadata=True), page(ROWS[:1],link=continuation())],
        resolver=lambda *_:next(addresses))
    stream = source.extract(); first = next(stream)
    with pytest.raises(NetworkAdapterError): next(stream)
    assert not hasattr(first,'admitted') and len(secrets) == len(transport.calls) == 2 and transport.closed


@pytest.mark.parametrize('status', [301,302,307,308])
def test_redirects_reject_without_forwarding_credentials(asset, status):
    source, transport, secrets = adapter(asset, [response(b'',status=status,extra_headers=(('Location','https://SECRET/'),))])
    with pytest.raises(NetworkAdapterError) as failure: source.discover_schema()
    assert 'SECRET' not in str(failure.value) and len(transport.calls) == len(secrets) == 1 and transport.closed


def test_authorization_and_purpose_denial_precedes_network(asset):
    for kwargs in ({'authorize':lambda *_:False}, {'purposes':lambda *_:False}, {'cfg':config(organization_id=UUID(int=999))}):
        source, transport, secrets = adapter(asset, [], **kwargs)
        with pytest.raises(NetworkAdapterError): source.discover_schema()
        assert not secrets and not transport.calls and not source._resources


def test_timeouts_rate_limits_and_retry_budgets(asset):
    sleeps = []
    source, transport, _ = adapter(asset, [TimeoutError('SECRET'),
        response(b'',status=429,extra_headers=(('Retry-After','2'),)), response(METADATA,metadata=True)], sleep=sleeps.append)
    assert source.discover_schema()['Amount'] == 'Edm.Decimal' and sleeps == [1,2]
    source, transport, _ = adapter(asset, [response(b'',status=429,extra_headers=(('Retry-After','999'),))], sleep=sleeps.append)
    with pytest.raises(NetworkAdapterError): source.discover_schema()
    assert len(transport.calls) == 1 and transport.closed
    source, transport, _ = adapter(asset, [TimeoutError('SECRET')]*3, sleep=lambda _:None)
    with pytest.raises(NetworkAdapterError) as failure: source.discover_schema()
    assert 'SECRET' not in str(failure.value) and len(transport.calls) == 3 and transport.closed


@pytest.mark.parametrize('updates', [{'max_response_bytes':1}, {'max_rows':1,'page_size':1}, {'max_pages':1}])
def test_extraction_limits_reject_and_clean_up(asset, updates):
    source, transport, _ = adapter(asset, [response(METADATA,metadata=True), page(link=continuation(config(**updates)))],cfg=config(**updates))
    with pytest.raises(NetworkAdapterError): list(source.extract())
    assert transport.closed and not source._active


def test_late_failure_cycles_duplicate_keys_and_cancellation(asset):
    for second in (page(ROWS[:1]), page(ROWS[1:],link=continuation()),
                   page([{**ROWS[1], 'Flag':'bad'}])):
        source, transport, _ = adapter(asset, [response(METADATA,metadata=True),page(ROWS[:1],link=continuation()),second])
        stream=source.extract(); first=next(stream)
        with pytest.raises(NetworkAdapterError):next(stream)
        assert not hasattr(first,'admitted') and transport.closed and not source._active
    source, transport, _ = adapter(asset, [response(METADATA,metadata=True),page(link=continuation())])
    stream=source.extract(batch_size=1);next(stream);stream.close()
    assert transport.closed and not source._resources and not source._active
    source.close();source.close()
    with pytest.raises(NetworkAdapterError):source.discover_schema()


@pytest.mark.parametrize('body', [b'not JSON SECRET', b'{"value":[],"value":[]}',
    b'{"@odata.context":"x","value":[{"Amount":NaN}]}',
    json.dumps({'@odata.context':ROOT+'$metadata#Other','value':ROWS}).encode(),
    json.dumps({'@odata.context':ROOT+'$metadata#Records','value':ROWS,'@odata.nextLink':None}).encode(),
])
def test_malformed_envelopes_are_sanitized(asset, body):
    source, transport, _ = adapter(asset, [response(METADATA,metadata=True),response(body)])
    with pytest.raises(NetworkAdapterError) as failure:list(source.extract())
    assert 'SECRET' not in str(failure.value) and transport.closed


def test_odata_and_parquet_canonical_equivalence_with_exact_decimal(asset, tmp_path):
    source, _, _ = adapter(asset, [response(METADATA,metadata=True),page()])
    batches=list(source.extract())
    path=tmp_path/'reference.parquet'
    reference = {'Id':[int(row['Id']) for row in ROWS], 'Location':['001','001'],
        'Start':[datetime(2026,1,1,tzinfo=timezone.utc),datetime(2026,1,8,tzinfo=timezone.utc)],
        'End':[datetime(2026,1,8,tzinfo=timezone.utc),datetime(2026,1,15,tzinfo=timezone.utc)],
        'Amount':[Decimal('-9007199254740993.1234'),Decimal('0.0000')],
        'Flag':[True,False], 'Optional':[None,'NA'], 'Calendar':[date(2026,1,1),None]}
    schema=pa.schema([('Id',pa.int64()),('Location',pa.string()),('Start',pa.timestamp('ns',tz='UTC')),
        ('End',pa.timestamp('ns',tz='UTC')),('Amount',pa.decimal128(24,4)),('Flag',pa.bool_()),
        ('Optional',pa.string()),('Calendar',pa.date32())])
    pq.write_table(pa.Table.from_pydict(reference,schema=schema),path)
    with ParquetAdapter(ParquetConfiguration(path=str(path),source_dataset='sales-import'),asset) as parquet:
        other=list(parquet.extract())
    outputs=[]
    for raw in (batches,other):
        mapped=list(CanonicalMapper(mapping()).map_batches(raw))
        result=pd.concat([b.records for b in mapped],ignore_index=True).drop(columns=['source_record_id','lineage_id']).astype(object)
        outputs.append(result.where(pd.notna(result),None))
    pd.testing.assert_frame_equal(*outputs)


def test_odata_and_rest_canonical_equivalence_for_exact_binary_values(asset):
    rows=[{**row,'Amount':amount} for row,amount in zip(ROWS,('-12.5000','0.0000'))]
    source, _, _=adapter(asset,[response(METADATA,metadata=True),page(rows)])
    odata=list(source.extract())
    rawrows=[{**row,'Amount':float(row['Amount'])} for row in rows]
    transport=Transport([RESTResponse(200,(('Content-Type','application/json'),),json.dumps(rawrows).encode())])
    rest=RESTAdapter(RESTConfiguration(source_reference='rest-import',source_dataset='sales-import',organization_id=ORG,
        record_key_fields=('Id',),endpoint='https://rest.example.test/records',allowed_origins=('https://rest.example.test',)),
        asset,authorize_scope=lambda *_:True,transport_factory=lambda:transport,resolver=lambda *_:('93.184.216.34',))
    raw=list(rest.extract())
    outputs=[]
    for batches in (odata,raw):
        result=list(CanonicalMapper(mapping()).map_batches(batches))[0].records
        outputs.append(result[['series_id','period_start','period_end','metric','value','unit','currency','flag','optional']].astype(object))
    pd.testing.assert_frame_equal(*outputs)


def test_actual_controlled_loopback_transport(asset):
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            item=response(METADATA,metadata=True) if urlsplit(self.path).path.endswith('/$metadata') else page(root=origin+'/org-a/')
            self.send_response(item.status)
            for name,value in item.headers:self.send_header(name,value)
            self.send_header('Content-Length',str(len(item.body)));self.end_headers();self.wfile.write(item.body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    origin='http://127.0.0.1:'+str(server.server_port)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    cfg=config(service_root=origin+'/org-a/',allowed_origins=(origin,))
    source=ODataAdapter(cfg,asset,authorize_scope=lambda *_:True,authorize_request=lambda *_:True,
        resolve_credentials=lambda *a,**k:RESTCredentials(token='TEST_TOKEN'),
        policy=RESTNetworkPolicy(allow_loopback_for_tests=True,allow_http_for_tests=True))
    try:
        with source:batches=list(source.extract())
        assert batches[0].records.Amount.iloc[0]==Decimal('-9007199254740993.1234')
        assert len(calls)==2 and not source._resources
    finally:server.shutdown();server.server_close();thread.join(timeout=2)


@pytest.mark.parametrize('role,error_type', [
    ('authorizer',ValueError), ('authorizer',NetworkAdapterError),
    ('provider',RuntimeError), ('provider',NetworkAdapterError),
])
@pytest.mark.parametrize('purpose', ['metadata','entity'])
def test_collaborator_exceptions_are_sanitized_at_boundary(asset,role,error_type,purpose):
    secret='SYNTHETIC_PRIVATE_TOKEN_012345'
    credential_ref=config().credential_reference
    def fail_authorization(cfg,metadata,request_purpose):
        if request_purpose==purpose:
            raise error_type(secret+' '+credential_ref)
        return True
    def fail_credentials(reference,**context):
        if context['purpose']==purpose:
            raise error_type(secret+' '+reference)
        return RESTCredentials(token='TEST_TOKEN')
    kwargs={'purposes':fail_authorization} if role=='authorizer' else {'provider':fail_credentials}
    responses=[] if purpose=='metadata' else [response(METADATA,metadata=True)]
    source,transport,_=adapter(asset,responses,**kwargs)
    with pytest.raises(NetworkAdapterError) as failure:
        list(source.extract())
    expected='OData request authorization failed' if role=='authorizer' else 'OData credential resolution failed'
    assert str(failure.value)==expected
    rendered=''.join(traceback.format_exception(type(failure.value),failure.value,failure.value.__traceback__))
    assert secret not in rendered and credential_ref not in rendered
    assert failure.value.__cause__ is None and failure.value.__suppress_context__
    assert len(transport.calls)==(0 if purpose=='metadata' else 1)
    assert transport.closed and not source._resources and not source._active


def test_internal_validation_errors_retain_diagnostics(asset):
    source,transport,_=adapter(asset,[],purposes=lambda *_:False)
    with pytest.raises(NetworkAdapterError,match='request purpose is not authorized'):
        source.discover_schema()
    assert not transport.calls and transport.closed
    source,transport,_=adapter(asset,[],provider=lambda *a,**k:None)
    with pytest.raises(NetworkAdapterError,match='invalid OData Bearer credentials'):
        source.discover_schema()
    assert not transport.calls and transport.closed


def schema_document(*declarations,reference='Example.Record'):
    """Independent name-table fixtures, using the reference scalar declarations."""
    text=METADATA.decode()
    schema=text[text.index('<Schema '):text.index('</Schema>')+len('</Schema>')]
    elements=[]
    for namespace,alias,key_type,has_set in declarations:
        value=schema.replace('Namespace="Example"','Namespace="'+namespace+'"'+(' Alias="'+alias+'"' if alias else ''))
        value=value.replace('Type="Edm.Int64"','Type="'+key_type+'"')
        if has_set:
            value=value.replace('EntityType="Example.Record"','EntityType="'+reference+'"')
        else:
            start=value.index('<EntityContainer')
            end=value.index('</EntityContainer>')+len('</EntityContainer>')
            value=value[:start]+value[end:]
        elements.append(value)
    return (text[:text.index('<Schema ')]+''.join(elements)+text[text.index('</Schema>')+len('</Schema>'):]).encode()


@pytest.mark.parametrize('reverse', [False,True])
def test_namespace_alias_shadowing_rejects_before_entity_request(asset,reverse):
    declarations=[('Example',None,'Edm.Int64',True),('Other','Example','Edm.String',False)]
    if reverse:declarations.reverse()
    body=schema_document(*declarations)
    source,transport,secrets=adapter(asset,[response(body,metadata=True)])
    with pytest.raises(NetworkAdapterError,match='alias conflicts with namespace'):
        list(source.extract())
    assert len(transport.calls)==len(secrets)==1
    assert transport.closed and not source._active and not source._resources


@pytest.mark.parametrize('declarations,reference,message', [
    ((('Example',None,'Edm.Int64',True),('Example',None,'Edm.Int64',False)), 'Example.Record','duplicate OData schema namespace'),
    ((('Example','E','Edm.Int64',True),('Other','E','Edm.Int64',False)), 'E.Record','ambiguous OData schema alias'),
    ((('Example',None,'Edm.Int64',True),), 'Unknown.Record','unknown OData entity type reference'),
    ((('Example','E','Edm.Int64',True),), 'E.Unknown','unknown OData entity type reference'),
    ((('Example',None,'Edm.Int64',True),), 'Record','invalid OData entity type reference'),
])
def test_namespace_alias_name_table_errors_reject(asset,declarations,reference,message):
    source,transport,_=adapter(asset,[response(schema_document(*declarations,reference=reference),metadata=True)])
    with pytest.raises(NetworkAdapterError,match=message):source.discover_schema()
    assert len(transport.calls)==1 and transport.closed


@pytest.mark.parametrize('reverse', [False,True])
@pytest.mark.parametrize('reference', ['Example.Record','E.Record'])
def test_valid_namespace_alias_resolution_is_order_independent(asset,reverse,reference):
    declarations=[('Example','E','Edm.Int64',True),('Other','O','Edm.String',False)]
    if reverse:declarations.reverse()
    body=schema_document(*declarations,reference=reference)
    cfg=config(expected_metadata_fingerprint=hashlib.sha256(body).hexdigest())
    source,transport,_=adapter(asset,[response(body,metadata=True),page()],cfg=cfg)
    batches=list(source.extract())
    assert batches[0].records.Id.tolist()==[9007199254740993,9007199254740994]
    assert batches[0].asset.metadata['odata_schema_fingerprint']==hashlib.sha256(body).hexdigest()
    assert transport.closed


@pytest.mark.parametrize('token', ['%FF','%E2%82','%C0%AF','%ED%A0%80'])
@pytest.mark.parametrize('query_only', [False,True])
def test_invalid_utf8_continuation_rejects_before_followup(asset,token,query_only):
    link=continuation(token='REPLACEME').replace('REPLACEME',token)
    if query_only:link='?'+urlsplit(link).query
    source,transport,secrets=adapter(asset,[response(METADATA,metadata=True),page(ROWS[:1],link=link)])
    with pytest.raises(NetworkAdapterError,match='invalid UTF-8 OData continuation query'):
        list(source.extract())
    assert len(transport.calls)==len(secrets)==2
    assert transport.closed and not source._active and not source._resources


@pytest.mark.parametrize('token', ['東京-é-🙂','https://evil.example.test/path?name=é&x=1','literal%FF+space value'])
@pytest.mark.parametrize('query_only', [False,True])
def test_valid_continuation_tokens_preserve_semantics(asset,token,query_only):
    link=continuation(token=token)
    # Reordering valid options must not change their values or scope.
    path,query=link.split('?',1)
    query='&'.join(reversed(query.split('&')))
    link=('' if query_only else path)+'?'+query
    source,transport,_=adapter(asset,[response(METADATA,metadata=True),page(ROWS[:1],link=link),page(ROWS[1:])])
    batches=list(source.extract())
    assert len(batches)==2
    url=transport.calls[-1][0]
    assert urlsplit(url).hostname=='odata.example.test'
    assert parse_qs(urlsplit(url).query,encoding='utf-8',errors='strict')['$skiptoken']==[token]
    assert transport.closed
