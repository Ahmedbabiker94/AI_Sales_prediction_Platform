import pandas as pd
import pytest

from conftest import feature_config
from src.data.contracts.features import FeatureConfiguration, FeatureState
from src.data.contracts.forecast import ForecastConfig
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline, FeatureExecutionError
from src.features.feature_schema_compatibility import describe_feature_schema, verify_feature_schema, FeatureSchemaCompatibility, FeatureSchemaMismatch


def test_strict_schema_and_serialization(setup,forecast):
    config,history,context,origin,engine,state=setup
    matrix=engine.prepare_inference(history,context,state,forecast_origin=origin,forecast_config=forecast)
    expected=describe_feature_schema(config,state,forecast_config=forecast,forecast_origin=origin)
    restored=FeatureSchemaCompatibility.model_validate_json(expected.model_dump_json())
    assert restored==expected and restored.schema_id==expected.schema_id
    assert verify_feature_schema(restored,config,state,matrix,requests=context,forecast_config=forecast,forecast_origin=origin)==expected.schema_id
    assert FeatureState.model_validate_json(state.model_dump_json()).state_id==state.state_id


@pytest.mark.parametrize('fault',['order','dtype','config_version','ddof','state_reference','schema','digest','missing_metadata'])
def test_mismatched_schema_cannot_be_used_silently(setup,forecast,fault):
    config,history,context,origin,engine,state=setup
    matrix=engine.prepare_inference(history,context,state,forecast_origin=origin,forecast_config=forecast)
    expected=describe_feature_schema(config,state,forecast_config=forecast,forecast_origin=origin)
    if fault=='order':matrix=matrix[matrix.columns[::-1]]
    if fault=='dtype':matrix['previous']=matrix.previous.astype('float32')
    if fault=='config_version':config=config.model_copy(update={'version':'2.1'})
    if fault=='ddof':
        values=config.model_dump();values['calculations'][3]['parameters']['ddof']=0
        config=FeatureConfiguration.model_validate(values)
    if fault=='state_reference':state=state.model_copy(update={'training_partition_reference':'different-partition'})
    if fault=='schema':state=state.model_copy(update={'training_schema_digest':'a'*64})
    if fault=='digest':expected=expected.model_copy(update={'configuration_digest':'b'*64})
    if fault=='missing_metadata':expected=None
    with pytest.raises(FeatureSchemaMismatch):verify_feature_schema(expected,config,state,matrix,requests=context,forecast_config=forecast,forecast_origin=origin)


def test_fitted_state_and_configuration_are_immutable(setup):
    config,_,_,_,_,state=setup
    with pytest.raises(TypeError):state.vocabularies['product_class']=('changed',)
    with pytest.raises(TypeError):state.vocabularies['product_class']+=('changed',)
    with pytest.raises(ValueError):state.configuration_digest='invalid'
    with pytest.raises(ValueError):config.execution.temporal.minimum_history=-1
    with pytest.raises(ValueError):config.execution.categories[0].unknown='use_code'
    assert state.vocabularies['product_class']==('B','C')


@pytest.mark.parametrize('fault',['version','schema','organization','state_version'])
def test_executor_checks_fitted_state_reuse(setup,forecast,fault):
    _,history,context,origin,engine,state=setup
    if fault=='version':state=state.model_copy(update={'configuration_version':'2.2'})
    if fault=='schema':history=history.copy();history['value']=history.value.astype('float32')
    if fault=='organization':state=state.model_copy(update={'organization_ids':(__import__('uuid').UUID(int=22),)})
    if fault=='state_version':state=state.model_copy(update={'executor_version':'2.0.0'})
    with pytest.raises(FeatureExecutionError):engine.prepare_inference(history,context,state,forecast_origin=origin,forecast_config=forecast)


@pytest.mark.parametrize('fault',['calendar','frequency','timezone','weekday','gap','minimum','category_code','currency','offset_bool','min_periods_bool','ddof_bool','ddof_missing','operation','current_target','dtype','rolling_offset','ignored_missing_rule'])
def test_unsupported_or_ambiguous_configuration_rejects(fault):
    values=feature_config().model_dump();temporal=values['execution']['temporal']
    if fault=='calendar':temporal['calendar']='business_calendar'
    if fault=='frequency':temporal['frequency']='monthly'
    if fault=='timezone':temporal['timezone']='invalid-zone'
    if fault=='weekday':temporal['week_start']=None
    if fault=='gap':temporal['gap_policy']='allow_observation_gaps'
    if fault=='minimum':temporal['minimum_history']=True
    if fault=='category_code':values['execution']['categories'][0].update(unknown='use_code',unknown_code=True)
    if fault=='currency':values['execution']['measurement']['currency']=None
    if fault=='offset_bool':values['calculations'][0]['parameters']['offset']=True
    if fault=='min_periods_bool':values['calculations'][2]['parameters']['min_periods']=True
    if fault=='ddof_bool':values['calculations'][3]['parameters']['ddof']=True
    if fault=='ddof_missing':values['calculations'][3]['parameters'].pop('ddof')
    if fault=='operation':values['calculations'][0]['operation']='eval_python'
    if fault=='current_target':values['ordered_outputs']=('value',);values['output_dtypes']={'value':'float64'};values['missing_value_behavior']={'value':'reject'}
    if fault=='dtype':values['output_dtypes']['previous']='float32'
    if fault=='rolling_offset':values['calculations'][2]['parameters']['offset']=3
    if fault=='ignored_missing_rule':values['missing_value_behavior']['value']='zero'
    with pytest.raises((ValueError,FeatureExecutionError)):
        CanonicalFeaturePipeline(FeatureConfiguration.model_validate(values))


def test_legacy_declarations_deserialize_but_are_not_executable():
    values=feature_config().model_dump();values.pop('execution');values['version']='1.0'
    config=FeatureConfiguration.model_validate(values)
    assert config.execution is None
    assert FeatureConfiguration.model_validate_json(config.model_dump_json())==config
    with pytest.raises(FeatureExecutionError):CanonicalFeaturePipeline(config)


def test_feature_state_fit_schema_is_not_a_model_compatibility_claim(setup,forecast):
    config,_,context,origin,_,state=setup
    assert describe_feature_schema(config,state,forecast_config=forecast,forecast_origin=origin).configuration.execution.metric_meaning=='signed reported sales'
    # There is deliberately no legacy artifact fallback or inference of model
    # provenance from a shared feature count.
    with pytest.raises(FeatureSchemaMismatch):verify_feature_schema(None,config,state,pd.DataFrame(columns=config.ordered_outputs),requests=context,forecast_config=forecast,forecast_origin=origin)


def test_extension_dtype_parameters_bind_training_schema(setup,forecast):
    config,history,context,origin,_,_=setup
    history=history.copy();history['product_class']=history.product_class.astype(pd.StringDtype(storage='pyarrow'))
    engine=CanonicalFeaturePipeline(config);state=engine.fit_state(history,training_cutoff=origin,partition_reference='arrow-strings')
    changed=history.copy();changed['product_class']=changed.product_class.astype(pd.StringDtype(storage='python'))
    with pytest.raises(FeatureExecutionError,match='binding mismatch'):
        engine.prepare_inference(changed,context,state,forecast_origin=origin,forecast_config=forecast)


@pytest.mark.parametrize('fault',['plain_index','duplicate_index','wrong_organization','misaligned_period'])
def test_matrix_scope_and_period_index_are_part_of_schema(setup,forecast,fault):
    config,history,context,origin,engine,state=setup
    matrix=engine.prepare_inference(history,context,state,forecast_origin=origin,forecast_config=forecast)
    expected=describe_feature_schema(config,state,forecast_config=forecast,forecast_origin=origin)
    if fault=='plain_index':matrix=matrix.reset_index(drop=True)
    else:
        values=list(matrix.index)
        if fault=='duplicate_index':values[1]=values[0]
        if fault=='wrong_organization':values[0]=(__import__('uuid').UUID(int=22),values[0][1],values[0][2])
        if fault=='misaligned_period':values[0]=(values[0][0],values[0][1],values[0][2]+pd.Timedelta(hours=1))
        matrix.index=pd.MultiIndex.from_tuples(values,names=matrix.index.names)
    with pytest.raises(FeatureSchemaMismatch):verify_feature_schema(expected,config,state,matrix,requests=context,forecast_config=forecast,forecast_origin=origin)


def test_existing_measurement_is_reused(setup):
    from src.data.contracts import Measurement
    config=setup[0].model_dump()
    config['execution']['measurement']=Measurement(metric='reported',unit='amount',currency='USD')
    assert FeatureConfiguration.model_validate(config).execution.measurement.metric=='reported'


@pytest.mark.parametrize('budget',[{'max_calculations':1},{'max_configuration_bytes':16}])
def test_configuration_resource_budgets(setup,budget):
    from src.features.canonical_feature_pipeline import FeatureBudgets
    with pytest.raises(FeatureExecutionError):CanonicalFeaturePipeline(setup[0],budgets=FeatureBudgets(**budget))


def test_state_serialization_budget(setup,forecast):
    from src.features.canonical_feature_pipeline import FeatureBudgets
    config,history,context,origin,_,state=setup
    engine=CanonicalFeaturePipeline(config,budgets=FeatureBudgets(max_state_bytes=16))
    with pytest.raises(FeatureExecutionError):engine.fit_state(history,training_cutoff=origin,partition_reference='too-small-state')
    with pytest.raises(FeatureExecutionError):engine.prepare_inference(history,context,state,forecast_origin=origin,forecast_config=forecast)


def test_signature_cannot_describe_missing_category_state(setup,forecast):
    from types import MappingProxyType
    config,_,_,origin,_,state=setup
    state=state.model_copy(update={'vocabularies':MappingProxyType({})})
    with pytest.raises(FeatureSchemaMismatch):describe_feature_schema(config,state,forecast_config=forecast,forecast_origin=origin)


def test_reserved_category_codes_must_fit_output_dtype(setup):
    config,history,_,origin,_,_=setup;values=config.model_dump()
    values['execution']['categories'][0].update(unknown='use_code',unknown_code=-200)
    engine=CanonicalFeaturePipeline(FeatureConfiguration.model_validate(values))
    with pytest.raises(FeatureExecutionError):engine.fit_state(history,training_cutoff=origin,partition_reference='invalid-reserved-code')


@pytest.fixture
def horizon_case(setup):
    config,history,context,origin,engine,state=setup
    forecast=ForecastConfig(forecast_frequency='weekly',forecast_horizon=2)
    predictions=history.groupby(['organization_id','series_id']).tail(1).copy()
    predictions['period_start']=origin
    predictions['period_end']=origin+pd.DateOffset(days=7)
    predictions['available_at']=origin
    predictions['value']=-100.
    matrix=engine.prepare_inference(history,context,state,forecast_origin=origin,
        forecast_config=forecast,prior_predictions=predictions)
    expected=describe_feature_schema(config,state,forecast_config=forecast,forecast_origin=origin)
    return config,history,context,origin,engine,state,forecast,matrix,expected


def test_fixed_horizon_identity_and_correct_two_period_coverage(horizon_case):
    config,_,context,origin,_,state,forecast,matrix,expected=horizon_case
    one=describe_feature_schema(config,state,forecast_config=ForecastConfig(forecast_frequency='weekly',forecast_horizon=1),forecast_origin=origin)
    assert one.forecast_horizon==1 and expected.forecast_horizon==2
    assert one.schema_id!=expected.schema_id
    assert verify_feature_schema(expected,config,state,matrix,requests=context,forecast_config=forecast,forecast_origin=origin)==expected.schema_id
    restored=FeatureSchemaCompatibility.model_validate_json(expected.model_dump_json())
    assert restored==expected and restored.schema_id==expected.schema_id


@pytest.mark.parametrize('declared,runtime',[(1,2),(2,1)])
def test_fixed_horizon_rejects_other_runtime_horizon(horizon_case,declared,runtime):
    config,history,context,origin,engine,state,two,matrix,_=horizon_case
    expected=describe_feature_schema(config,state,forecast_config=ForecastConfig(forecast_frequency='weekly',forecast_horizon=declared),forecast_origin=origin)
    forecast=ForecastConfig(forecast_frequency='weekly',forecast_horizon=runtime)
    if runtime==1:
        matrix=engine.prepare_inference(history,context,state,forecast_origin=origin,forecast_config=forecast)
    with pytest.raises(FeatureSchemaMismatch):
        verify_feature_schema(expected,config,state,matrix,requests=context,forecast_config=forecast,forecast_origin=origin)


@pytest.mark.parametrize('fault',[
    'second_series_missing','original_slice','equal_count_different_coverage',
    'different_missing_extra','duplicate','unexpected_series','missing_series',
    'extra_row','row_order','wrong_organization','dimension_scope','metric_scope',
    'duplicate_request','missing_request','origin_shift','unaligned_origin','naive_origin',
    'index_timezone',
])
def test_per_series_coverage_rejects_malformed_matrix_or_request(horizon_case,fault):
    from uuid import UUID
    config,_,context,origin,_,state,forecast,matrix,expected=horizon_case
    matrix=matrix.copy();context=context.copy();keys=list(matrix.index)
    if fault=='second_series_missing':matrix=matrix.iloc[:3]
    if fault=='original_slice':matrix=matrix.iloc[[0,3]]
    if fault=='equal_count_different_coverage':
        keys[3]=(keys[3][0],keys[0][1],origin+pd.DateOffset(days=14))
    if fault=='different_missing_extra':
        keys[1]=(*keys[1][:2],origin+pd.DateOffset(days=14))
        keys[2]=(*keys[2][:2],origin-pd.DateOffset(days=7))
    if fault=='duplicate':keys[1]=keys[0]
    if fault=='unexpected_series':keys[3]=(keys[3][0],UUID(int=333),keys[3][2])
    if fault=='missing_series':matrix=matrix.iloc[:2]
    if fault=='extra_row':matrix=pd.concat([matrix,matrix.iloc[[0]]])
    if fault=='row_order':matrix=matrix.iloc[::-1]
    if fault=='wrong_organization':keys[0]=(UUID(int=77),keys[0][1],keys[0][2])
    if fault=='dimension_scope':context.loc[0,'product_class']='C'
    if fault=='metric_scope':context['metric']='returns'
    if fault=='duplicate_request':context=pd.concat([context,context.iloc[[0]]])
    if fault=='missing_request':context=context.iloc[:1]
    if fault=='origin_shift':origin+=pd.DateOffset(days=7)
    if fault=='unaligned_origin':origin+=pd.Timedelta(hours=1)
    if fault=='naive_origin':origin=origin.tz_localize(None)
    if fault=='index_timezone':keys=[(org,series,start.tz_convert('Europe/London')) for org,series,start in keys]
    if fault in ('equal_count_different_coverage','different_missing_extra','duplicate','unexpected_series','wrong_organization','index_timezone'):
        matrix.index=pd.MultiIndex.from_tuples(keys,names=matrix.index.names)
    with pytest.raises(FeatureSchemaMismatch):
        verify_feature_schema(expected,config,state,matrix,requests=context,forecast_config=forecast,forecast_origin=origin)


@pytest.mark.parametrize('frequency,timezone,start,expected_dates',[
    ('weekly','UTC','2020-12-07',['2021-01-04','2021-01-11']),
    ('daily','America/New_York','2021-03-11',['2021-03-15','2021-03-16']),
    ('daily','America/New_York','2021-11-04',['2021-11-08','2021-11-09']),
])
def test_hand_calculated_calendar_coverage(setup,frequency,timezone,start,expected_dates):
    from conftest import observations,requests
    config=setup[0].model_dump()
    config['execution']['temporal'].update(frequency=frequency,timezone=timezone,week_start=0 if frequency=='weekly' else None)
    config=FeatureConfiguration.model_validate(config)
    history=observations(series=(10,),count=4,frequency=frequency,timezone=timezone,start=start)
    context=requests(history);origin=pd.Timestamp(expected_dates[0],tz=timezone)
    engine=CanonicalFeaturePipeline(config)
    state=engine.fit_state(history,training_cutoff=origin,partition_reference='hand-calculated-calendar')
    forecast=ForecastConfig(forecast_frequency=frequency,forecast_horizon=2)
    prediction=history.iloc[[-1]].copy()
    prediction['period_start']=origin
    prediction['period_end']=pd.Timestamp(expected_dates[1],tz=timezone)
    prediction['available_at']=origin
    prediction['value']=100.
    matrix=engine.prepare_inference(history,context,state,forecast_origin=origin,forecast_config=forecast,prior_predictions=prediction)
    assert matrix.index.get_level_values('period_start').tolist()==[pd.Timestamp(day,tz=timezone) for day in expected_dates]
    # The prior observations are [10,-20,0,40]; current target is excluded.
    assert matrix.previous.tolist()==[40.,100.]
    assert matrix.mean_four.tolist()==[7.5,30.]
    expected=describe_feature_schema(config,state,forecast_config=forecast,forecast_origin=origin)
    assert verify_feature_schema(expected,config,state,matrix,requests=context,forecast_config=forecast,forecast_origin=origin)==expected.schema_id
    if frequency=='weekly':assert matrix.index.get_level_values('period_start')[0].isocalendar().week==1


@pytest.mark.parametrize('fault',['missing_horizon','legacy_version','zero','negative','boolean','oversized'])
def test_legacy_or_invalid_horizon_declarations_fail_closed(horizon_case,fault):
    *_,expected=horizon_case
    data=expected.model_dump()
    if fault=='missing_horizon':data.pop('forecast_horizon')
    elif fault=='legacy_version':data['version']='1.0'
    else:data['forecast_horizon']={'zero':0,'negative':-1,'boolean':True,'oversized':53}[fault]
    with pytest.raises(ValueError):FeatureSchemaCompatibility.model_validate(data)


@pytest.mark.parametrize('fault',['frequency','boolean_horizon','unsupported_calendar','before_fit'])
def test_describe_rejects_incompatible_forecast_context(horizon_case,fault):
    config,_,_,origin,_,state,forecast,_,_=horizon_case
    if fault=='frequency':forecast=ForecastConfig(forecast_frequency='daily',forecast_horizon=2)
    if fault=='boolean_horizon':forecast=forecast.model_copy(update={'forecast_horizon':True})
    if fault=='before_fit':origin-=pd.DateOffset(days=7)
    if fault=='unsupported_calendar':
        rules=config.execution.temporal.model_copy(update={'calendar':'fiscal'})
        config=config.model_copy(update={'execution':config.execution.model_copy(update={'temporal':rules})})
    with pytest.raises(FeatureSchemaMismatch):
        describe_feature_schema(config,state,forecast_config=forecast,forecast_origin=origin)


def test_period_columns_must_agree_with_generated_index(horizon_case):
    config,history,context,origin,_,_,forecast,_,_=horizon_case
    data=config.model_dump()
    for name in ('period_start','period_end'):
        data['ordered_outputs']=(*data['ordered_outputs'],name)
        data['output_dtypes'][name]='datetime64[ns, UTC]'
        data['missing_value_behavior'][name]='reject'
    config=FeatureConfiguration.model_validate(data);engine=CanonicalFeaturePipeline(config)
    state=engine.fit_state(history,training_cutoff=origin,partition_reference='period-output')
    prediction=history.groupby('series_id').tail(1).copy();prediction['period_start']=origin
    prediction['period_end']=origin+pd.DateOffset(days=7);prediction['available_at']=origin
    matrix=engine.prepare_inference(history,context,state,forecast_origin=origin,forecast_config=forecast,prior_predictions=prediction)
    expected=describe_feature_schema(config,state,forecast_config=forecast,forecast_origin=origin)
    assert verify_feature_schema(expected,config,state,matrix,requests=context,forecast_config=forecast,forecast_origin=origin)==expected.schema_id
    matrix.loc[matrix.index[3],'period_end']+=pd.DateOffset(days=7)
    with pytest.raises(FeatureSchemaMismatch):
        verify_feature_schema(expected,config,state,matrix,requests=context,forecast_config=forecast,forecast_origin=origin)


def test_complete_coverage_is_organization_scoped():
    from conftest import observations,requests
    config=feature_config();history=observations(organizations=(1,2),count=4)
    context=requests(history);origin=history.period_end.max();engine=CanonicalFeaturePipeline(config)
    state=engine.fit_state(history,training_cutoff=origin,partition_reference='organization-scoped-coverage')
    forecast=ForecastConfig(forecast_frequency='weekly',forecast_horizon=2)
    prediction=history.groupby(['organization_id','series_id']).tail(1).copy()
    prediction['period_start']=origin;prediction['period_end']=origin+pd.DateOffset(days=7)
    prediction['available_at']=origin;prediction['value']=0.
    matrix=engine.prepare_inference(history,context,state,forecast_origin=origin,forecast_config=forecast,prior_predictions=prediction)
    expected=describe_feature_schema(config,state,forecast_config=forecast,forecast_origin=origin)
    assert len(matrix)==8
    assert verify_feature_schema(expected,config,state,matrix,requests=context.iloc[::-1],forecast_config=forecast,forecast_origin=origin)==expected.schema_id
    # Replacing one organization's full coverage with duplicate other-tenant
    # rows cannot pass merely because series IDs and the total count match.
    mixed=pd.concat([matrix.iloc[:4],matrix.iloc[:4]])
    with pytest.raises(FeatureSchemaMismatch):
        verify_feature_schema(expected,config,state,mixed,requests=context,forecast_config=forecast,forecast_origin=origin)
