from datetime import datetime, timezone
from uuid import UUID

import pytest

from src.data.contracts import (
    FeatureCalculation, FeatureConfiguration, Frequency, LineageRecord,
    MetadataResolution, Observation, SourceMapping,
)

ORG = UUID(int=1)


def resolution(**overrides):
    values = dict(field='currency', metric='reported', original_value_reference='raw://batch/row/currency',
                  status='suggested', proposed_value='SAR', evidence_kind='statistical',
                  evidence_reference='suggestion://uniform-rows', organization_id=ORG,
                  business_scope='retail', rule_id='declared_rule', rule_version='1.0',
                  resolved_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    values.update(overrides)
    return MetadataResolution(**values)


def mapping(**overrides):
    values = dict(mapping_id='reference', version='1.0.0', organization_id=ORG, business_scope='retail',
                  field_mappings={'location': 'branch', 'period_start': 'date', 'amount': 'reported_amount'},
                  identity_dimensions=['location'], target_metric='reported', target_value_field='amount', metric_meaning='signed reported sales',
                  sign_semantics='signed source values; transaction meaning requires source evidence',
                  policy_metric='sales', policy_context='signed_reported_training',
                  period_start_field='period_start', period_interpretation='start of weekly interval',
                  frequency=Frequency.WEEKLY, timezone='UTC', unit_required=True, currency_required=True)
    values.update(overrides)
    return SourceMapping(**values)


def features(**overrides):
    values = dict(configuration_id='history', version='1.0', target_metric='reported',
                  canonical_inputs=['amount', 'kind'], history_order='oldest_first',
                  calculations=[FeatureCalculation(output='previous', operation='lag', inputs=['amount'], parameters={'offset':1})],
                  lag_offsets=[1, 52], rolling_windows=[4, 12], missing_value_behavior={'previous':'fill_zero'},
                  category_encoding={'kind':'frame_local_sorted_codes'}, ordered_outputs=['kind','previous'],
                  output_dtypes={'kind':'int8','previous':'float64'})
    values.update(overrides)
    return FeatureConfiguration(**values)


def test_explicit_semantics_and_distinct_metrics_preserve_signed_values():
    m = mapping()
    assert m.metric_meaning == 'signed reported sales' and m.policy_metric == 'sales'
    observations = [Observation(series_id=UUID(int=2), period_start=datetime(2026,1,1),
                    period_end=datetime(2026,1,2), frequency=Frequency.DAILY,
                    metric=metric, value=value, unit='declared_unit')
                    for metric, value in [('gross_sales',10),('returns',2),('damage',1),('adjustments',-1),('reported',-5),('reported',0)]]
    assert [o.value for o in observations] == [10,2,1,-1,-5,0]
    assert observations[-2].metric == 'reported'  # sign does not reclassify the metric
    with pytest.raises(ValueError): mapping(metric_meaning='')


def test_missing_metadata_and_statistical_suggestions_are_not_ready():
    assert not mapping().metadata_ready
    assert not mapping(resolutions=[resolution()]).metadata_ready
    currency = resolution(status='authorized', evidence_kind='business_authorization', authority_reference='authority://approved')
    assert not mapping(resolutions=[currency]).metadata_ready  # required unit unresolved
    unit = resolution(field='unit', proposed_value='declared_unit', status='authorized',
                      evidence_kind='source_declaration', authority_reference='authority://source')
    assert mapping(resolutions=[currency,unit]).metadata_ready
    assert not mapping(measurement={'metric':'reported','unit':'declared_unit'}).metadata_ready
    assert mapping(measurement={'metric':'reported','unit':'declared_unit','currency':'SAR'}).metadata_ready
    assert mapping(unit_required=False,currency_required=False).metadata_ready


@pytest.mark.parametrize('overrides', [
    {'status':'authorized'}, {'status':'authorized','authority_reference':'authority://someone'},
    {'status':'authorized','evidence_kind':'source_declaration'},
    {'rule_version':'invalid'}, {'resolved_at':datetime(2026,1,1)},
])
def test_invalid_resolution_cannot_claim_authorization(overrides):
    with pytest.raises(ValueError): resolution(**overrides)


def test_resolution_scope_and_metric_binding():
    with pytest.raises(ValueError): mapping(resolutions=[resolution(organization_id=UUID(int=3))])
    with pytest.raises(ValueError): mapping(resolutions=[resolution(business_scope='other')])
    evidence = resolution(metric='other',status='authorized',evidence_kind='source_declaration',authority_reference='authority://source')
    assert not mapping(unit_required=False,resolutions=[evidence]).metadata_ready
    assert mapping(organization_id=UUID(int=3)).organization_id != mapping().organization_id


@pytest.mark.parametrize('version',['','1','01.0','1.0.bad','-1.0'])
def test_configuration_versions_are_validated(version):
    with pytest.raises(ValueError): mapping(version=version)
    with pytest.raises(ValueError): features(version=version)


@pytest.mark.parametrize('overrides', [
    {'identity_dimensions':['unknown']}, {'target_value_field':'unknown'}, {'identity_dimensions':['location','location']},
    {'required_auxiliary_fields':['unknown']}, {'field_mappings':{}},
    {'measurement':{'metric':'other','unit':'unit'}},
])
def test_mapping_relationships_are_validated(overrides):
    with pytest.raises(ValueError): mapping(**overrides)


@pytest.mark.parametrize('overrides', [
    {'ordered_outputs':['previous','previous']}, {'output_dtypes':{'previous':'float64'}},
    {'ordered_outputs':['undeclared']}, {'lag_offsets':[0]}, {'rolling_windows':[4,4]},
    {'category_encoding':{'unknown':'codes'}}, {'missing_value_behavior':{'unknown':'fill_zero'}},
    {'calculations':[FeatureCalculation(output='previous',operation='lag',inputs=['future'])]},
])
def test_feature_schema_relationships_are_validated(overrides):
    with pytest.raises(ValueError): features(**overrides)


def test_order_and_serialization_are_preserved():
    config = features()
    assert FeatureConfiguration.model_validate_json(config.model_dump_json()).ordered_outputs == ('kind','previous')
    m = mapping(resolutions=[resolution()])
    assert SourceMapping.model_validate_json(m.model_dump_json()) == m
    assert MetadataResolution.model_validate_json(resolution().model_dump_json()) == resolution()


def test_old_lineage_payload_still_deserializes():
    payload=dict(lineage_id=UUID(int=2),source_id=UUID(int=3),source_dataset='input',source_record_id='row',
                 ingestion_batch_id=UUID(int=4),adapter_name='csv',adapter_version='1.0',mapping_version='1.0',
                 ingested_at=datetime(2026,1,1,tzinfo=timezone.utc))
    old=LineageRecord(**payload)
    assert old.metadata_resolutions == ()
    assert LineageRecord.model_validate_json(old.model_dump_json()) == old
    extended=LineageRecord(**payload,metadata_resolutions=[resolution()])
    assert extended.metadata_resolutions[0].original_value_reference == 'raw://batch/row/currency'


def test_conflicting_metadata_is_not_silently_selected():
    approved = resolution(status='authorized', evidence_kind='business_authorization', authority_reference='authority://approved')
    other = resolution(status='authorized', evidence_kind='source_declaration', authority_reference='authority://source', proposed_value='USD')
    with pytest.raises(ValueError, match='conflicting authorized'):
        mapping(resolutions=[approved, other])
    with pytest.raises(ValueError, match='conflicts with declared'):
        mapping(measurement={'metric':'reported','unit':'declared_unit','currency':'USD'}, resolutions=[approved])


def test_failed_evidence_assignment_leaves_statistical_suggestion_unchanged():
    suggestion = resolution()
    before = suggestion.model_dump()
    with pytest.raises(ValueError):
        suggestion.status = 'authorized'
    assert suggestion.model_dump() == before
    assert not mapping(unit_required=False, resolutions=[suggestion]).metadata_ready


def test_nested_mapping_and_evidence_are_immutable_and_input_is_detached():
    from src.data.contracts import Measurement
    evidence = resolution(status='authorized', evidence_kind='source_declaration', authority_reference='authority://source')
    source = {'location':'branch', 'period_start':'date', 'amount':'reported_amount'}
    declared = Measurement(metric='reported', unit='declared_unit', currency='SAR')
    config = mapping(field_mappings=source, measurement=declared, resolutions=[evidence])
    source['location'] = 'changed'
    declared.currency = 'USD'
    assert config.field_mappings['location'] == 'branch'
    assert config.measurement.currency == 'SAR'
    with pytest.raises(TypeError): config.field_mappings['location'] = 'changed'
    with pytest.raises(AttributeError): config.resolutions.append(evidence)
    with pytest.raises(ValueError): config.resolutions[0].organization_id = UUID(int=9)
    with pytest.raises(ValueError): config.measurement.currency = 'USD'
    assert config.metadata_ready


def test_readiness_revalidates_unsafe_evidence_and_scope():
    valid = resolution(status='authorized', evidence_kind='source_declaration', authority_reference='authority://source')
    for corrupted in [valid.model_copy(update={'organization_id':UUID(int=9)}),
                      valid.model_copy(update={'business_scope':'other'}),
                      valid.model_copy(update={'evidence_kind':'statistical'})]:
        # model_copy/model_construct are explicitly unvalidated Pydantic APIs.
        config = mapping(unit_required=False).model_copy(update={'resolutions':(corrupted,)})
        assert not config.metadata_ready
    other = resolution(status='authorized', evidence_kind='business_authorization', authority_reference='authority://other', proposed_value='USD')
    config = mapping(unit_required=False).model_copy(update={'resolutions':(valid,other)})
    assert not config.metadata_ready


@pytest.mark.parametrize('field', ['metric','original_value_reference','proposed_value','evidence_reference',
                                  'authority_reference','business_scope','rule_id'])
def test_resolution_rejects_whitespace_only_fields(field):
    with pytest.raises(ValueError): resolution(**{field:' \t '})


@pytest.mark.parametrize('field', ['mapping_id','target_metric','target_value_field','metric_meaning','sign_semantics',
                                  'policy_metric','policy_context','business_scope','period_start_field',
                                  'period_end_field','period_interpretation','timezone'])
def test_mapping_rejects_whitespace_only_fields(field):
    with pytest.raises(ValueError): mapping(**{field:' \t '})


@pytest.mark.parametrize('field', ['metric','unit','currency'])
def test_measurement_declaration_rejects_whitespace(field):
    values={'metric':'reported','unit':'declared_unit','currency':'SAR'}
    values[field]='   '
    with pytest.raises(ValueError): mapping(measurement=values)


def test_valid_declarations_are_not_silently_stripped():
    assert mapping(metric_meaning=' signed reported sales ').metric_meaning == ' signed reported sales '
    assert resolution(authority_reference=' authority://source ').authority_reference == ' authority://source '


@pytest.mark.parametrize('overrides', [
    {'identity_dimensions':['amount']}, {'identity_dimensions':['period_start']},
    {'period_end_field':'period_start'}, {'period_end_field':'amount'},
    {'field_mappings':{'location':'reported_amount','period_start':'date','amount':'reported_amount'}},
    {'field_mappings':{'location':'date','period_start':'date','amount':'reported_amount'}},
])
def test_mapping_rejects_conflicting_canonical_and_source_roles(overrides):
    with pytest.raises(ValueError): mapping(**overrides)


def test_dimension_key_order_is_stable_and_identity_scope_includes_organization():
    import json
    fields={'location':'branch','department':'dept','period_start':'date','amount':'reported_amount'}
    first=mapping(field_mappings=fields,identity_dimensions=['location','department'])
    second=mapping(field_mappings=fields,identity_dimensions=['department','location'])
    assert first == second
    assert first.identity_dimensions == ('department','location')
    # Test-only illustration of the documented convention, not production ID generation.
    def logical_identity(config, values):
        return json.dumps([str(config.organization_id), [[key,values[key]] for key in config.identity_dimensions]], ensure_ascii=False)
    values={'location':'a|b','department':'c'}
    assert logical_identity(first,values) == logical_identity(second,values)
    third=mapping(field_mappings=fields,identity_dimensions=['location','department'],organization_id=UUID(int=3))
    assert logical_identity(first,values) != logical_identity(third,values)
    assert logical_identity(first,values) != logical_identity(first,{'location':'a','department':'b|c'})


@pytest.mark.parametrize('field', ['lag_offsets','rolling_windows'])
@pytest.mark.parametrize('value', [True,False,0,-1,1.5,'1'])
def test_history_definitions_require_strict_positive_integers(field,value):
    with pytest.raises(ValueError): features(**{field:[value]})


@pytest.mark.parametrize('operation,key,value', [('lag','offset',True),('lag','offset',0),('lag','offset',-1),
                                                ('rolling_mean','window',False),('rolling_mean','window',0),
                                                ('rolling_mean','window',-4)])
def test_history_parameters_require_positive_non_boolean_integers(operation,key,value):
    with pytest.raises(ValueError): FeatureCalculation(output='previous',operation=operation,inputs=['amount'],parameters={key:value})


@pytest.mark.parametrize('operation,parameters', [('lag',{}),('lag',{'offset':2}),
                                                 ('rolling_mean',{'window':9}),('rolling_std',{'window':4,'offset':2})])
def test_calculations_cannot_override_authoritative_history_definitions(operation,parameters):
    with pytest.raises(ValueError): features(calculations=[FeatureCalculation(output='previous',operation=operation,inputs=['amount'],parameters=parameters)])


def test_nested_feature_configuration_is_immutable_and_serializable():
    parameters={'nested':{'choices':[1,2]}}
    calculation=FeatureCalculation(output='previous',operation='custom',inputs=['amount'],parameters=parameters)
    config=features(calculations=[calculation])
    parameters['nested']['choices'].append(3)
    assert config.calculations[0].parameters['nested']['choices'] == (1,2)
    with pytest.raises(TypeError): config.calculations[0].parameters['nested']['choices'] += (3,)
    with pytest.raises(AttributeError): config.lag_offsets.append(-1)
    with pytest.raises(TypeError): config.output_dtypes['previous'] = 'changed'
    with pytest.raises(ValueError): config.ordered_outputs = ('previous','kind')
    assert FeatureConfiguration.model_validate_json(config.model_dump_json()) == config
    assert config.ordered_outputs == ('kind','previous')
