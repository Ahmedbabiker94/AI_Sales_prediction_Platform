"""Shared foundation tested without any concrete connector or live services."""
from datetime import datetime, timezone
from uuid import UUID
import zipfile

import numpy as np
import pandas as pd
import pytest

from src.data.adapters.base import SourceAdapter, SourceBatch, SourceType
from src.data.adapters.selection import SourceConfiguration, SourceSelectionError, identify_local_file, select_connector
from src.data.contracts import AssetMetadata, DataFormat, MetadataResolution, SourceMapping
from src.data.processing.canonical_mapper import CanonicalMapper, CanonicalMappingError


@pytest.fixture
def asset():
    return AssetMetadata(asset_id=UUID(int=1), organization_id=UUID(int=2), source_id=UUID(int=3),
                         ingestion_batch_id=UUID(int=4), created_at=datetime(2026,1,1,tzinfo=timezone.utc))


def config(**updates):
    values=dict(mapping_id='explicit',version='1.0',organization_id=UUID(int=2),business_scope='retail',
        field_mappings={'location':'branch','department':'group','amount':'reported','start':'from','end':'to','promotion':'offer'},
        identity_dimensions=['location','department'],target_metric='reported_sales',target_value_field='amount',
        metric_meaning='signed reported sales',sign_semantics='unchanged source sign',policy_metric='sales',
        policy_context='signed_reported_training',period_start_field='start',period_end_field='end',
        period_interpretation='explicit_bounds',frequency='weekly',timezone='UTC',unit_required=True,
        currency_required=True,measurement={'metric':'reported_sales','unit':'monetary_amount','currency':'SAR'},
        required_auxiliary_fields=['promotion'])
    values.update(updates)
    return SourceMapping(**values)


def raw():
    frame=pd.DataFrame({'branch':['a|b','a|b','a|b'],'group':['c','c','c'],'reported':[-12.5,0.,42.],
        'from':['2026-01-01','2026-01-08','2026-01-15'],'to':['2026-01-08','2026-01-15','2026-01-22'],'offer':[1.,2.,3.]})
    frame.index=pd.Index([101,71,39],name='raw_row')
    return frame


def batch(frame,asset,*,ids=('one','two','three'),batch_id='0',source_format=DataFormat.JSON):
    return SourceBatch(frame,ids,asset,'opaque-source',source_format,'memory','1.0',batch_id)


class MemoryAdapter(SourceAdapter):
    """Test-only lifecycle implementation; never registered in production."""
    def __init__(self, frame, asset, kind=SourceType.JSON):
        self.frame,self.asset,self.kind=frame,asset,kind
        self.closed=False
        self.access_checks=0
    def validate_configuration(self):
        if self.frame is None:raise ValueError('missing records')
    def identify_source_type(self):return self.kind
    def test_access(self):self.access_checks+=1
    def discover_schema(self):return {name:str(dtype) for name,dtype in self.frame.dtypes.items()}
    def extract(self,*,batch_size=10000):
        if type(batch_size) is not int or batch_size<=0:raise ValueError('invalid batch size')
        for offset in range(0,len(self.frame),batch_size):
            rows=self.frame.iloc[offset:offset+batch_size]
            yield batch(rows,self.asset,ids=tuple(str(i) for i in range(offset,offset+len(rows))),batch_id=str(offset))
    def close(self):self.closed=True


def test_distinct_source_schemas_have_equivalent_bulk_canonical_values(asset):
    first=raw();second=first.rename(columns={c:f'other_{c}' for c in first})
    cfg=config();other=config(field_mappings={k:f'other_{v}' for k,v in cfg.field_mappings.items()})
    left=CanonicalMapper(cfg).map_batch(batch(first,asset))
    right=CanonicalMapper(other).map_batch(batch(second,asset))
    pd.testing.assert_frame_equal(left.records,right.records)
    assert left.dataset.series==right.dataset.series
    assert left.dataset.observations==[]  # bulk, no Pydantic object per row


def test_memory_adapter_maps_without_files_and_preserves_raw(asset):
    frame=raw();before=frame.copy(deep=True)
    adapter=MemoryAdapter(frame,asset)
    with adapter:
        adapter.validate_configuration();adapter.test_access()
        assert 'reported' in adapter.discover_schema()
        results=list(CanonicalMapper(config()).map_batches(adapter.extract(batch_size=2)))
    assert adapter.closed and adapter.access_checks==1
    output=pd.concat([r.records for r in results])
    pd.testing.assert_series_equal(output.value,frame.reported.rename('value'))
    pd.testing.assert_frame_equal(frame,before)
    assert output.source_record_id.tolist()==['0','1','2']
    for result in results:
        lineage=result.dataset.lineage[0]
        assert result.records.lineage_id.eq(lineage.lineage_id).all()
        assert lineage.mapping_version=='1.0' and lineage.source_id==asset.source_id
        assert lineage.adapter_name=='memory' and result.dataset.asset.metadata['metric_meaning']=='signed reported sales'
        assert result.dataset.asset.timezone=='UTC'
    assert asset.metadata=={}  # mapping metadata did not mutate source metadata


def test_identity_order_organization_and_concatenation_collisions(asset):
    cfg=config();mapped=CanonicalMapper(cfg).map_batch(batch(raw(),asset))
    reordered=CanonicalMapper(config(identity_dimensions=['department','location'])).map_batch(batch(raw(),asset))
    assert mapped.records.series_id.tolist()==reordered.records.series_id.tolist()
    other_asset=asset.model_copy(update={'organization_id':UUID(int=99)})
    tenant=CanonicalMapper(config(organization_id=UUID(int=99))).map_batch(batch(raw(),other_asset))
    assert set(mapped.records.series_id).isdisjoint(tenant.records.series_id)
    changed=raw();changed['branch']='a';changed['group']='b|c'
    collision=CanonicalMapper(cfg).map_batch(batch(changed,asset))
    assert set(mapped.records.series_id).isdisjoint(collision.records.series_id)


@pytest.mark.parametrize('dimension',[1,np.int64(2),'001','branch-7'])
def test_string_and_integral_dimensions(asset,dimension):
    frame=raw();frame['branch']=dimension
    result=CanonicalMapper(config()).map_batch(batch(frame,asset))
    assert result.dataset.series[0].dimensions['location']==str(dimension)


@pytest.mark.parametrize('metric,meaning',[('gross','gross sales'),('returned','returns'),('reported','signed reported sales'),('damaged','damage'),('adjusted','adjustments')])
def test_metrics_remain_distinct_without_sign_classification(asset,metric,meaning):
    cfg=config(target_metric=metric,metric_meaning=meaning,measurement={'metric':metric,'unit':'amount','currency':'SAR'})
    result=CanonicalMapper(cfg).map_batch(batch(raw(),asset))
    assert result.records.metric.eq(metric).all()
    assert result.records.value.tolist()==[-12.5,0.,42.]


@pytest.mark.parametrize('measurement',[None,{'metric':'reported_sales','unit':'amount'}])
def test_unresolved_required_metadata_is_rejected(measurement):
    with pytest.raises(CanonicalMappingError,match='required'):CanonicalMapper(config(measurement=measurement))


@pytest.mark.parametrize('status,kind,authority',[('suggested','statistical',None),('authorized','business_authorization','authority://unverified')])
def test_resolution_declarations_do_not_fill_missing_metadata(status,kind,authority):
    record=MetadataResolution(field='currency',metric='reported_sales',original_value_reference='raw://currency',
        status=status,proposed_value='SAR',evidence_kind=kind,evidence_reference='evidence://ref',authority_reference=authority,
        organization_id=UUID(int=2),business_scope='retail',rule_id='rule',rule_version='1.0',resolved_at=datetime(2026,1,1,tzinfo=timezone.utc))
    cfg=config(measurement={'metric':'reported_sales','unit':'amount'},resolutions=[record])
    with pytest.raises(CanonicalMappingError,match='required currency'):CanonicalMapper(cfg)


@pytest.mark.parametrize('updates',[{'frequency':'custom'},{'period_interpretation':'infer_week_end'},
                                  {'period_end_field':None},{'metric_meaning':'unknown'}, {'timezone':'not/a/timezone'}])
def test_unsupported_interpretations_fail_closed(updates):
    with pytest.raises(CanonicalMappingError):CanonicalMapper(config(**updates))


@pytest.mark.parametrize('mutation',['invalid_date','missing_date','numeric_date','reverse_period','infinite','nan','duplicate','missing_field','missing_dimension','bool_dimension','float_dimension','aux_infinite','empty'])
def test_invalid_source_data_is_rejected_without_mutation(asset,mutation):
    frame=raw()
    if mutation=='invalid_date':frame.loc[101,'from']='not-a-date'
    elif mutation=='missing_date':frame.loc[101,'to']=None
    elif mutation=='numeric_date':frame['from']=123
    elif mutation=='reverse_period':frame.loc[101,'to']='2025-01-01'
    elif mutation=='infinite':frame.loc[101,'reported']=np.inf
    elif mutation=='nan':frame.loc[101,'reported']=np.nan
    elif mutation=='duplicate':frame.loc[71,['from','to']]=frame.loc[101,['from','to']]
    elif mutation=='missing_field':frame=frame.drop(columns='offer')
    elif mutation=='missing_dimension':frame.loc[101,'branch']=None
    elif mutation=='bool_dimension':frame['branch']=True
    elif mutation=='float_dimension':frame['branch']=1.2
    elif mutation=='aux_infinite':frame.loc[101,'offer']=np.inf
    else:frame=frame.iloc[:0]
    before=frame.copy(deep=True)
    ids=tuple(str(i) for i in range(len(frame)))
    with pytest.raises(CanonicalMappingError):CanonicalMapper(config()).map_batch(batch(frame,asset,ids=ids))
    pd.testing.assert_frame_equal(frame,before)


def test_conflicting_mappings_and_organizations_fail(asset):
    cfg=config()
    with pytest.raises(CanonicalMappingError):CanonicalMapper(config(field_mappings={**cfg.field_mappings,'source_record_id':'extra'}))
    with pytest.raises(CanonicalMappingError):CanonicalMapper(config(field_mappings={**cfg.field_mappings,'alias':'reported'}))
    with pytest.raises(CanonicalMappingError):CanonicalMapper(cfg).map_batch(batch(raw(),asset.model_copy(update={'organization_id':UUID(int=9)})))


def test_duplicates_across_batches_fail(asset):
    batches=[batch(raw().iloc[:1],asset,ids=('one',),batch_id='0'),batch(raw().iloc[:1],asset,ids=('different',),batch_id='1')]
    with pytest.raises(CanonicalMappingError,match='across batches'):list(CanonicalMapper(config()).map_batches(batches))


def test_lifecycle_releases_resources_on_failure(asset):
    adapter=MemoryAdapter(raw(),asset)
    with pytest.raises(RuntimeError):
        with adapter:raise RuntimeError('test failure')
    assert adapter.closed


def test_explicit_registry_selection_never_connects(tmp_path,asset):
    path=tmp_path/'records.json';path.write_text('[{"a":1}]')
    adapter=MemoryAdapter(raw(),asset)
    selected=select_connector(SourceConfiguration(str(path)),{SourceType.JSON:lambda cfg:adapter})
    assert selected is adapter and adapter.access_checks==0
    with pytest.raises(SourceSelectionError,match='registered'):select_connector(SourceConfiguration(str(path)),{})


def test_detection_uses_content_not_extensions(tmp_path):
    path=tmp_path/'wrong.csv';path.write_text('[{"a":1}]')
    assert identify_local_file(path)==SourceType.JSON
    with pytest.raises(SourceSelectionError):select_connector(SourceConfiguration(str(path),SourceType.CSV),{})
    path.write_bytes(b'PAR1payloadPAR1');assert identify_local_file(path)==SourceType.PARQUET
    with zipfile.ZipFile(path,'w') as archive:
        archive.writestr('[Content_Types].xml','test');archive.writestr('xl/workbook.xml','test')
    assert identify_local_file(path)==SourceType.EXCEL


@pytest.mark.parametrize('contents',[b'a,b\n1,2\n',b'not a source',b'PAR1',b'\x00\xffbad'])
def test_ambiguous_and_invalid_signatures_fail(tmp_path,contents):
    path=tmp_path/'input';path.write_bytes(contents)
    with pytest.raises(SourceSelectionError):identify_local_file(path)


def test_explicit_csv_selection_uses_registered_stub_only(tmp_path,asset):
    path=tmp_path/'input.csv';path.write_text('a,b\n1,2\n')
    adapter=MemoryAdapter(raw(),asset,SourceType.CSV)
    assert select_connector(SourceConfiguration(str(path),SourceType.CSV),{SourceType.CSV:lambda cfg:adapter}) is adapter
    assert adapter.access_checks==0


@pytest.mark.parametrize('kind',[SourceType.POSTGRESQL,SourceType.REST])
def test_network_selection_requires_explicit_authorization(kind,asset):
    configuration=SourceConfiguration('opaque-config-name',kind)
    adapter=MemoryAdapter(raw(),asset,kind)
    registry={kind:lambda cfg:adapter}
    with pytest.raises(SourceSelectionError):select_connector(configuration,registry)
    with pytest.raises(SourceSelectionError):select_connector(configuration,registry,authorize_network=lambda cfg:False)
    assert select_connector(configuration,registry,authorize_network=lambda cfg:True) is adapter
    assert adapter.access_checks==0


@pytest.mark.parametrize('reference',['https://example.com','postgresql://user:password@example/db','http://127.0.0.1'])
def test_urls_do_not_authorize_network_and_errors_do_not_echo_secrets(reference):
    with pytest.raises(SourceSelectionError) as error:SourceConfiguration(reference,SourceType.REST)
    assert reference not in str(error.value) and 'password' not in str(error.value)


def test_connector_mismatch_is_rejected_and_closed(tmp_path,asset):
    path=tmp_path/'input.json';path.write_text('[]')
    adapter=MemoryAdapter(raw(),asset,SourceType.REST)
    with pytest.raises(SourceSelectionError,match='identity'):
        select_connector(SourceConfiguration(str(path)),{SourceType.JSON:lambda cfg:adapter})
    assert adapter.closed and adapter.access_checks==0


def test_source_provenance_rejects_credential_urls(asset):
    with pytest.raises(ValueError,match='opaque'):
        SourceBatch(raw(),('one','two','three'),asset,'postgresql://user:password@host/db',DataFormat.DATABASE,'test','1.0','0')


def test_nullable_integer_targets_preserve_exact_values(asset):
    frame=raw();frame['reported']=pd.Series([-12,0,9007199254740993],index=frame.index,dtype='Int64')
    result=CanonicalMapper(config()).map_batch(batch(frame,asset))
    pd.testing.assert_series_equal(result.records.value,frame.reported.rename('value'),check_exact=True)


def test_reserved_dimension_names_do_not_lose_business_identity(asset):
    # A configured dimension may share a standard field name; its values remain
    # in SeriesDefinition, while bulk series_id is always the canonical UUID.
    cfg=config(field_mappings={'series_id':'branch','department':'group','amount':'reported','start':'from','end':'to','promotion':'offer'},
               identity_dimensions=['series_id','department'])
    result=CanonicalMapper(cfg).map_batch(batch(raw(),asset))
    assert result.dataset.series[0].dimensions['series_id']=='a|b'
    assert isinstance(result.records.series_id.iloc[0],UUID)


def test_repeated_record_ids_across_batches_are_rejected(asset):
    first=batch(raw().iloc[:1],asset,ids=('same',),batch_id='0')
    second=batch(raw().iloc[1:2],asset,ids=('same',),batch_id='1')
    with pytest.raises(CanonicalMappingError):list(CanonicalMapper(config()).map_batches([first,second]))


def test_conflicting_measurement_evidence_fails_before_mapping():
    evidence=MetadataResolution(field='currency',metric='reported_sales',original_value_reference='raw-currency',
        status='authorized',proposed_value='USD',evidence_kind='business_authorization',evidence_reference='evidence-ref',
        authority_reference='authority-ref',organization_id=UUID(int=2),business_scope='retail',rule_id='rule',rule_version='1.0',
        resolved_at=datetime(2026,1,1,tzinfo=timezone.utc))
    with pytest.raises(ValueError,match='conflicts'):config(resolutions=[evidence])


def test_locale_ambiguous_dates_are_not_guessed(asset):
    frame=raw();frame['from']='01/02/2026'
    with pytest.raises(CanonicalMappingError,match='calendar dates'):
        CanonicalMapper(config()).map_batch(batch(frame,asset))


def test_explicit_timezone_offsets_are_preserved_as_instants(asset):
    frame=raw()
    frame['from']=['2026-01-01T03:00:00+03:00','2026-01-08T03:00:00+03:00','2026-01-15T03:00:00+03:00']
    frame['to']=['2026-01-08T03:00:00+03:00','2026-01-15T03:00:00+03:00','2026-01-22T03:00:00+03:00']
    result=CanonicalMapper(config()).map_batch(batch(frame,asset))
    assert result.records.period_start.iloc[0]==pd.Timestamp('2026-01-01',tz='UTC')
    assert result.records.frequency.eq('weekly').all()


def test_nested_auxiliary_output_is_detached_from_raw(asset):
    frame = raw()
    frame['offer'] = [{'items': [1, {'nested': [2]}]} for _ in range(len(frame))]
    result = CanonicalMapper(config()).map_batch(batch(frame, asset))
    result.records.promotion.iloc[0]['items'][1]['nested'].append(99)
    assert frame.offer.iloc[0] == {'items': [1, {'nested': [2]}]}
    assert result.records.promotion.iloc[1] == {'items': [1, {'nested': [2]}]}


@pytest.mark.parametrize('kind', ['custom', 'cycle', 'nonstring_key'])
def test_unsupported_auxiliary_structures_rejected(asset, kind):
    class Custom:
        def __deepcopy__(self, memo):
            raise AssertionError('custom copying must never execute')
        def __str__(self):
            raise AssertionError('custom stringification must never execute')
    value = Custom()
    if kind == 'cycle':
        value = []
        value.append(value)
    elif kind == 'nonstring_key':
        value = {1: 'not a string key'}
    frame = raw()
    frame['offer'] = pd.Series([value, 'text', 'text'], index=frame.index, dtype=object)
    with pytest.raises(CanonicalMappingError, match='auxiliary'):
        CanonicalMapper(config()).map_batch(batch(frame, asset))


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf'),
                                  np.float32('nan'), np.float64('inf'), np.float32('-inf')])
@pytest.mark.parametrize('nested', [False, True])
def test_object_auxiliary_nonfinite_numeric_values_rejected(asset, value, nested):
    frame = raw()
    first = {'items': [value]} if nested else value
    frame['offer'] = pd.Series([first, 'legitimate text', 'Infinity'], index=frame.index, dtype=object)
    with pytest.raises(CanonicalMappingError, match='non-finite auxiliary'):
        CanonicalMapper(config()).map_batch(batch(frame, asset))


def test_optional_auxiliary_nulls_and_text_preserved(asset):
    frame = raw()
    frame['offer'] = pd.Series([np.nan, None, 'Infinity'], index=frame.index, dtype=object)
    result = CanonicalMapper(config(required_auxiliary_fields=[])).map_batch(batch(frame, asset))
    pd.testing.assert_series_equal(result.records.promotion, frame.offer.rename('promotion'))


@pytest.mark.parametrize('value', [float('inf'), -float('inf'), np.float64('inf')])
def test_optional_auxiliary_infinity_is_not_missing(asset, value):
    frame = raw()
    frame['offer'] = pd.Series([value, 'text', None], index=frame.index, dtype=object)
    with pytest.raises(CanonicalMappingError, match='non-finite auxiliary'):
        CanonicalMapper(config(required_auxiliary_fields=[])).map_batch(batch(frame, asset))


def test_bulk_canonical_records_validate_against_observation(asset):
    from src.data.contracts import Observation
    result = CanonicalMapper(config()).map_batch(batch(raw(), asset, ids=('x' * 512, 'two', 'three')))
    fields = list(Observation.model_fields)
    observations = [Observation.model_validate(row) for row in result.records[fields].to_dict('records')]
    assert [o.value for o in observations] == [-12.5, 0., 42.]
    assert len(observations[0].source_record_id) == 512


def test_overlong_record_id_rejected_at_extraction_and_mapping(asset):
    with pytest.raises(ValueError, match='canonical constraints'):
        batch(raw(), asset, ids=('x' * 513, 'two', 'three'))
    unsafe = batch(raw(), asset)
    object.__setattr__(unsafe, 'record_ids', ('x' * 513, 'two', 'three'))
    with pytest.raises(CanonicalMappingError, match='canonical constraints'):
        CanonicalMapper(config()).map_batch(unsafe)


@pytest.mark.parametrize('field,value', [('target_metric', 'x' * 129), ('unit', 'x' * 65), ('currency', 'LONG')])
def test_observation_declaration_constraints_fail_before_mapping(field, value):
    # Normal R-01 declarations already enforce these canonical limits.
    measurement = {'metric': 'reported_sales', 'unit': 'amount', 'currency': 'SAR'}
    updates = {}
    if field == 'target_metric':
        updates[field] = value
        measurement['metric'] = value
    else:
        measurement[field] = value
    with pytest.raises(ValueError):
        CanonicalMapper(config(measurement=measurement, **updates))


@pytest.mark.parametrize('kind', [SourceType.JSON, SourceType.EXCEL])
def test_large_explicit_selection_delegates_full_validation(tmp_path, asset, kind):
    path = tmp_path / 'misleading.csv'
    if kind == SourceType.JSON:
        path.write_text('["' + 'a' * 70000 + '"]')
    else:
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('[Content_Types].xml', 'test')
            archive.writestr('xl/workbook.xml', 'test')
            archive.writestr('padding', b'x' * (16 * 1024 * 1024))
    with pytest.raises(SourceSelectionError, match='inconclusive'):
        identify_local_file(path)
    adapter = MemoryAdapter(raw(), asset, kind)
    assert select_connector(SourceConfiguration(str(path), kind), {kind: lambda cfg: adapter}) is adapter
    assert adapter.access_checks == 0
    with pytest.raises(SourceSelectionError):
        select_connector(SourceConfiguration(str(path)), {kind: lambda cfg: adapter})
    conflicting = SourceType.CSV
    with pytest.raises(SourceSelectionError):
        select_connector(SourceConfiguration(str(path), conflicting), {conflicting: lambda cfg: adapter})


@pytest.mark.parametrize('content,kind', [(b'PAR1', SourceType.PARQUET), (b'PK\x03\x04broken', SourceType.EXCEL),
                                         (b'[broken', SourceType.JSON), (b'[NaN]', SourceType.JSON),
                                         (b'\x00binary', SourceType.CSV), (b'plain text', SourceType.PARQUET)])
def test_explicit_type_does_not_accept_unsupported_or_malformed_signature(tmp_path, content, kind):
    path = tmp_path / 'input'
    path.write_bytes(content)
    def forbidden(cfg):
        pytest.fail('factory must not execute for rejected signatures')
    with pytest.raises(SourceSelectionError):
        select_connector(SourceConfiguration(str(path), kind), {kind: forbidden})


def test_inconclusive_selection_still_validates_connector_and_closes_on_failure(tmp_path, asset):
    path = tmp_path / 'large.json'
    path.write_text('["' + 'a' * 70000 + '"]')
    adapter = MemoryAdapter(None, asset)
    with pytest.raises(ValueError, match='missing records'):
        select_connector(SourceConfiguration(str(path), SourceType.JSON), {SourceType.JSON: lambda cfg: adapter})
    assert adapter.closed


@pytest.mark.parametrize('start', ['2026-03-08T02:30:00', '2026-11-01T01:30:00'])
def test_dst_invalid_local_times_raise_sanitized_mapping_error(asset, start):
    frame = raw()
    frame.loc[101, 'from'] = start
    frame.loc[101, 'to'] = '2026-12-01T00:00:00'
    before = frame.copy(deep=True)
    with pytest.raises(CanonicalMappingError, match='invalid calendar dates or timezone: start') as error:
        CanonicalMapper(config(timezone='America/New_York')).map_batch(batch(frame, asset))
    assert start not in str(error.value)
    assert error.value.__cause__ is None and error.value.__suppress_context__
    pd.testing.assert_frame_equal(frame, before)


def test_later_failure_never_marks_partial_batch_admitted(asset):
    mapper = CanonicalMapper(config())
    stream = mapper.map_batches([batch(raw().iloc[:1], asset, ids=('one',), batch_id='1'),
                                 batch(raw().iloc[:1], asset, ids=('two',), batch_id='2')])
    partial = next(stream)
    assert partial.dataset.quality is None
    assert not hasattr(partial, 'admitted')
    with pytest.raises(CanonicalMappingError, match='across batches'):
        next(stream)


def test_auxiliary_numeric_strings_are_not_coerced(asset):
    frame = raw()
    frame['offer'] = pd.Series(['NaN', '-Infinity', '12.5'], index=frame.index, dtype=object)
    result = CanonicalMapper(config()).map_batch(batch(frame, asset))
    pd.testing.assert_series_equal(result.records.promotion, frame.offer.rename('promotion'))


def test_dst_adjacent_valid_instants_preserved(asset):
    frame = raw()
    frame.loc[101, 'from'] = '2026-03-08T01:30:00'
    frame.loc[101, 'to'] = '2026-03-08T03:30:00'
    result = CanonicalMapper(config(timezone='America/New_York')).map_batch(batch(frame, asset))
    assert result.records.period_end.iloc[0] - result.records.period_start.iloc[0] == pd.Timedelta(hours=1)


@pytest.mark.parametrize('kind', ['str', 'list', 'tuple', 'dict', 'int', 'float', 'decimal',
                                  'date', 'datetime', 'numpy_float', 'numpy_str'])
def test_auxiliary_untrusted_subclasses_rejected_without_hooks(asset, kind):
    from datetime import date
    from decimal import Decimal
    events = []
    def forbidden(self, *args, **kwargs):
        events.append('unsafe method called')
        raise AssertionError('rejected objects must not execute hooks')
    base, initial = {
        'str': (str, 'text'), 'list': (list, [1]), 'tuple': (tuple, (1,)), 'dict': (dict, {'x': 1}),
        'int': (int, 3), 'float': (float, 3.), 'decimal': (Decimal, '3'),
        'date': (date, None), 'datetime': (datetime, None),
        'numpy_float': (np.float64, 3.), 'numpy_str': (np.str_, 'text'),
    }[kind]
    # Install hooks only after construction, avoiding test-setup side effects.
    untrusted = type('Untrusted', (base,), {})
    value = untrusted(2026, 1, 1) if initial is None else untrusted(initial)
    value.state = [1]
    for name in ('__iter__', 'items', '__deepcopy__', '__str__', '__float__', '__int__', 'is_finite'):
        setattr(untrusted, name, forbidden)
    frame = raw()
    frame['offer'] = pd.Series([{'nested': [value]}, 'text', 'text'], index=frame.index, dtype=object)
    events.clear()
    with pytest.raises(CanonicalMappingError, match='unsupported auxiliary value type'):
        CanonicalMapper(config()).map_batch(batch(frame, asset))
    assert events == []
    assert frame.offer.iloc[0]['nested'][0] is value and value.state == [1]


@pytest.mark.parametrize('key_kind', ['str_subclass', 'custom_object', 'integer'])
def test_auxiliary_untrusted_dictionary_keys_rejected_without_conversion(asset, key_kind):
    events = []
    class Key(str):
        def __str__(self):
            events.append('string conversion')
            raise AssertionError('no conversion')
    class ObjectKey:
        def __str__(self):
            events.append('string conversion')
            raise AssertionError('no conversion')
    key = Key('key') if key_kind == 'str_subclass' else ObjectKey() if key_kind == 'custom_object' else 3
    frame = raw()
    frame['offer'] = pd.Series([{key: [1]}, 'text', 'text'], index=frame.index, dtype=object)
    with pytest.raises(CanonicalMappingError, match='dictionary keys require strings'):
        CanonicalMapper(config()).map_batch(batch(frame, asset))
    assert events == []


def test_auxiliary_trusted_scalars_and_nested_containers_preserved(asset):
    from datetime import date
    from decimal import Decimal
    values = [np.bool_(True), np.int8(-1), np.int16(2), np.int32(3), np.int64(4),
              np.uint8(1), np.uint16(2), np.uint32(3), np.uint64(4), np.float16(1.5),
              np.float32(2.5), np.float64(3.5), np.longdouble('1e300'), np.str_('text'),
              Decimal('12.500'), date(2026, 1, 1), datetime(2026, 1, 1, tzinfo=timezone.utc),
              pd.Timestamp('2026-01-01', tz='UTC')]
    payload = {'values': tuple(values), np.str_('list'): [{'deep': [0, -1]}]}
    frame = raw()
    frame['offer'] = pd.Series([payload, 'text', 'text'], index=frame.index, dtype=object)
    result = CanonicalMapper(config()).map_batch(batch(frame, asset))
    output = result.records.promotion.iloc[0]
    for expected, actual in zip(values, output['values']):
        assert type(expected) is type(actual) and expected == actual
    output['list'][0]['deep'].append(99)
    assert payload['list'][0]['deep'] == [0, -1]
    output['values'][-1].state = [99]
    assert not hasattr(values[-1], 'state')
    assert result.records.value.tolist() == [-12.5, 0., 42.]


@pytest.mark.parametrize('value', [np.longdouble('inf'), np.longdouble('-inf'), np.longdouble('nan')])
def test_auxiliary_trusted_extended_float_nonfinite_rejected(asset, value):
    frame = raw()
    frame['offer'] = pd.Series([{'x': [value]}, 'text', 'text'], index=frame.index, dtype=object)
    with pytest.raises(CanonicalMappingError, match='non-finite auxiliary'):
        CanonicalMapper(config()).map_batch(batch(frame, asset))


def test_auxiliary_timestamp_with_custom_state_rejected(asset):
    value = pd.Timestamp('2026-01-01')
    value.state = [1]
    frame = raw()
    frame['offer'] = pd.Series([{'x': value}, 'text', 'text'], index=frame.index, dtype=object)
    with pytest.raises(CanonicalMappingError, match='unsupported auxiliary timestamp state'):
        CanonicalMapper(config()).map_batch(batch(frame, asset))
    assert value.state == [1]
