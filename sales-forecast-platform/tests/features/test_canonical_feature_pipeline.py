from decimal import Decimal
import traceback
from uuid import UUID

import numpy as np
import pandas as pd
import pytest

from conftest import feature_config, observations, requests
from src.data.contracts.features import FeatureConfiguration
from src.data.contracts.forecast import ForecastConfig
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline, FeatureBudgets, FeatureExecutionError


@pytest.mark.parametrize('base_measurement',[False,True])
def test_typed_measurement_reuse_preserves_valid_serialization(base_measurement):
    from src.data.contracts.common import Measurement
    from src.data.contracts.features import FeatureExecutionRules

    config=feature_config();execution=config.execution
    measurement=execution.measurement
    if base_measurement:
        measurement=Measurement.model_validate(measurement.model_dump())
    assert measurement.model_dump(warnings='error')==measurement.model_dump()
    typed=FeatureExecutionRules.model_validate({**dict(execution),'measurement':measurement})
    plain=FeatureExecutionRules.model_validate(execution.model_dump())
    assert typed.model_dump_json()==plain.model_dump_json()==execution.model_dump_json()
    reconstructed=FeatureConfiguration.model_validate({**dict(config),'execution':typed})
    assert reconstructed.model_dump_json()==config.model_dump_json()
    assert CanonicalFeaturePipeline(reconstructed).digest==CanonicalFeaturePipeline(config).digest


def configured(config, mutate):
    values=config.model_dump();mutate(values)
    return FeatureConfiguration.model_validate(values)


def run(engine,history,context,origin,state,forecast,**kw):
    return engine.prepare_inference(history,context,state,forecast_origin=origin,forecast_config=forecast,**kw)


def test_identical_training_and_inference_no_current_target_leakage(setup,forecast):
    config,history,context,origin,engine,state=setup
    before=history.copy(deep=True);before_context=context.copy(deep=True)
    current=history.iloc[:2].copy();current.period_start=origin;current.period_end=origin+pd.DateOffset(days=7);current.value=999999.
    current.available_at=current.period_end
    # One current target per series, still invisible to history computation.
    current.iloc[1,current.columns.get_loc('series_id')]=UUID(int=11)
    current.iloc[1,current.columns.get_loc('product_class')]='C'
    full=pd.concat([history,current],ignore_index=True)
    training=engine.prepare_training(full,context,state,forecast_origin=origin,forecast_config=forecast)
    inference=run(engine,history,context,origin,state,forecast)
    pd.testing.assert_frame_equal(training,inference,check_exact=True)
    first=inference.iloc[0]
    assert first.previous==80 and first.previous_four==50 and first.mean_four==65 and first.trend==30
    assert first.std_four==pd.Series([50.,60.,70.,80.]).std(ddof=1)
    assert tuple(inference.columns)==config.ordered_outputs
    assert [str(d) for d in inference.dtypes]==[config.output_dtypes[n] for n in config.ordered_outputs]
    pd.testing.assert_frame_equal(before,history);pd.testing.assert_frame_equal(before_context,context)


def test_organization_and_series_isolation(forecast):
    config=feature_config();history=observations(organizations=(1,2));origin=history.period_end.max();engine=CanonicalFeaturePipeline(config)
    state=engine.fit_state(history,training_cutoff=origin,partition_reference='two-organizations')
    context=requests(history);result=run(engine,history,context,origin,state,forecast)
    assert result.previous.tolist()==[80.,180.,1080.,1180.]
    shuffled=run(engine,history.sample(frac=1,random_state=42),context.iloc[::-1],origin,state,forecast)
    pd.testing.assert_frame_equal(result,shuffled,check_exact=True)


@pytest.mark.parametrize('basis',['period','observation'])
def test_missing_week_is_explicit(setup,forecast,basis):
    config,history,context,origin,_,_=setup
    config=configured(config,lambda c:c['execution']['temporal'].update(lag_basis=basis,gap_policy='reject' if basis=='period' else 'allow_observation_gaps'))
    history=history[history.period_start!=origin-pd.DateOffset(days=7)]
    engine=CanonicalFeaturePipeline(config);state=engine.fit_state(history,training_cutoff=origin,partition_reference='gapped-training')
    if basis=='period':
        with pytest.raises(FeatureExecutionError,match='missing configured history'):run(engine,history,context,origin,state,forecast)
    else:assert run(engine,history,context,origin,state,forecast).previous.iloc[0]==70.


@pytest.mark.parametrize('ddof',[0,1])
def test_standard_deviation_is_selected_explicitly(setup,forecast,ddof):
    config,history,context,origin,_,_=setup
    config=configured(config,lambda c:c['calculations'][3]['parameters'].update(ddof=ddof))
    engine=CanonicalFeaturePipeline(config);state=engine.fit_state(history,training_cutoff=origin,partition_reference='std-training')
    assert run(engine,history,context,origin,state,forecast).std_four.iloc[0]==np.std([50.,60.,70.,80.],ddof=ddof)


@pytest.mark.parametrize('cold',['reject','configured_missing'])
def test_short_history_and_warm_up_are_explicit(setup,forecast,cold):
    config,_,_,_,_,_=setup;history=observations(count=3);origin=history.period_end.max();context=requests(history)
    config=configured(config,lambda c:(c['execution']['temporal'].update(cold_start=cold),c['missing_value_behavior'].update(previous_four='zero',mean_four='zero',std_four='zero',trend='zero')))
    engine=CanonicalFeaturePipeline(config);state=engine.fit_state(history,training_cutoff=origin,partition_reference='short-training')
    if cold=='reject':
        with pytest.raises(FeatureExecutionError,match='minimum history'):run(engine,history,context,origin,state,forecast)
    else:
        result=run(engine,history,context,origin,state,forecast)
        assert result.iloc[0].previous==0 and result.iloc[0].mean_four==0
        # Filling a FEATURE did not insert a fictitious target observation.
        assert len(history)==6 and history.value.iloc[:3].tolist()==[10.,-20.,0.]


def test_min_periods_partial_window_is_explicit(setup,forecast):
    config,_,_,_,_,_=setup;history=observations(count=3);origin=history.period_end.max()
    def change(c):
        c['execution']['temporal'].update(minimum_history=3)
        c['calculations'][2]['parameters']['min_periods']=3
        c['calculations'][3]['parameters']['min_periods']=3
        c['missing_value_behavior'].update(previous_four='zero',trend='zero')
    config=configured(config,change);engine=CanonicalFeaturePipeline(config);state=engine.fit_state(history,training_cutoff=origin,partition_reference='partial-window')
    assert run(engine,history,requests(history),origin,state,forecast).mean_four.iloc[0]==pytest.approx(-10/3)


def test_recursive_prior_predictions_and_horizon_dates(setup):
    config,history,context,origin,engine,state=setup
    predictions=history.groupby(['organization_id','series_id']).tail(1).copy()
    predictions.period_start=origin;predictions.period_end=origin+pd.DateOffset(days=7);predictions.value=-100.
    before=predictions.copy(deep=True)
    forecast=ForecastConfig(forecast_frequency='weekly',forecast_horizon=2)
    result=run(engine,history,context,origin,state,forecast,prior_predictions=predictions)
    train=engine.prepare_training(history,context,state,forecast_origin=origin,forecast_config=forecast,prior_predictions=predictions)
    pd.testing.assert_frame_equal(result,train,check_exact=True)
    assert result.previous.iloc[:2].tolist()==[80.,-100.]
    assert result.index.get_level_values('period_start')[:2].tolist()==[origin,origin+pd.DateOffset(days=7)]
    assert result.mean_four.iloc[1]==np.mean([-100.,80.,70.,60.])
    pd.testing.assert_frame_equal(predictions,before)
    with pytest.raises(FeatureExecutionError,match='prior recursive prediction'):run(engine,history,context,origin,state,forecast)


@pytest.mark.parametrize('timezone,start',[('UTC','2020-03-06'),('America/New_York','2020-03-06')])
def test_daily_timezone_dst_calendar_and_missing_day(setup,timezone,start):
    config=setup[0]
    config=configured(config,lambda c:c['execution']['temporal'].update(frequency='daily',timezone=timezone,week_start=None))
    history=observations(frequency='daily',timezone=timezone,start=start);origin=history.period_end.max();engine=CanonicalFeaturePipeline(config)
    state=engine.fit_state(history,training_cutoff=origin,partition_reference='daily-partition')
    forecast=ForecastConfig(forecast_frequency='daily',forecast_horizon=1)
    assert run(engine,history,requests(history),origin,state,forecast).previous.iloc[0]==80.
    broken=history.drop(history.index[3]);broken_state=engine.fit_state(broken,training_cutoff=origin,partition_reference='missing-day')
    with pytest.raises(FeatureExecutionError,match='missing configured history'):run(engine,broken,requests(history),origin,broken_state,forecast)
    bad=history.copy();bad.loc[bad.index[0],'period_start']=bad.period_start.iloc[0]+pd.Timedelta(hours=1)
    with pytest.raises(FeatureExecutionError,match='calendar aligned'):engine.fit_state(bad,training_cutoff=origin,partition_reference='misaligned')


def test_category_state_does_not_refit_per_request(setup,forecast):
    _,history,context,origin,engine,state=setup
    full=run(engine,history,context,origin,state,forecast)
    single=run(engine,history,context.iloc[[1]],origin,state,forecast)
    assert single.product_class.iloc[0]==full.product_class.iloc[1]==1
    pd.testing.assert_frame_equal(single,full.iloc[[1]],check_exact=True)
    with pytest.raises(FeatureExecutionError,match='fit partition'):engine.fit_state(history,training_cutoff=origin-pd.DateOffset(days=7),partition_reference='contains-holdout')


@pytest.mark.parametrize('behavior',['reject','use_code'])
def test_unknown_category_behavior(setup,forecast,behavior):
    config,history,context,origin,_,_=setup
    config=configured(config,lambda c:c['execution']['categories'][0].update(unknown=behavior,unknown_code=-1 if behavior=='use_code' else None))
    engine=CanonicalFeaturePipeline(config);state=engine.fit_state(history,training_cutoff=origin,partition_reference='category-training')
    future=history.copy();future.loc[future.series_id==UUID(int=10),'series_id']=UUID(int=12)
    future.loc[future.series_id==UUID(int=12),'product_class']='A';context=requests(future)
    if behavior=='reject':
        with pytest.raises(FeatureExecutionError,match='unknown feature category'):run(engine,future,context,origin,state,forecast)
    else:assert run(engine,future,context,origin,state,forecast).product_class.tolist()==[1,-1]


def exogenous_setup(config,history,origin,*,availability='known_in_advance',missing='reject',categorical=False):
    def change(c):
        c['canonical_inputs']=list(c['canonical_inputs'])+['signal']
        c['ordered_outputs']=list(c['ordered_outputs'])+['signal']
        c['output_dtypes']['signal']='int8' if categorical else 'float64';c['missing_value_behavior']['signal']='reject'
        c['execution']['exogenous']=[dict(field='signal',availability=availability,offset=1 if availability=='lagged' else None,
            missing=missing,substitute_reference='approved-zero-substitute' if missing=='zero' else None)]
        if categorical:
            c['category_encoding']['signal']='ordinal'
            c['execution']['categories']=list(c['execution']['categories'])+[dict(field='signal',unknown='reject',missing='use_code',missing_code=-2)]
    config=configured(config,change);history=history.copy();history['signal']='present' if categorical else 50.
    at=origin-pd.DateOffset(days=7) if availability=='lagged' else origin
    exo=requests(history).drop(columns=['product_class','unit','currency','frequency'])
    exo['field']='signal';exo['value']='present' if categorical else 55.;exo['period_start']=at
    exo['period_end']=at+pd.DateOffset(days=7);exo['available_at']=origin;exo['evidence_reference']='forecast-proof'
    return config,history,exo


@pytest.mark.parametrize('availability',['known_in_advance','observed_only','lagged','external_forecast'])
def test_exogenous_availability_and_external_authorization(setup,forecast,availability):
    config,history,_,origin,_,_=setup
    config,history,exo=exogenous_setup(config,history,origin,availability=availability)
    calls=[];engine=CanonicalFeaturePipeline(config,authorize_external_forecast=lambda request:(calls.append(request) or True))
    state=engine.fit_state(history,training_cutoff=origin,partition_reference='exogenous-training');before=exo.copy(deep=True)
    if availability=='observed_only':
        with pytest.raises(FeatureExecutionError,match='unavailable'):run(engine,history,requests(history),origin,state,forecast,exogenous=exo)
    else:
        assert run(engine,history,requests(history),origin,state,forecast,exogenous=exo).signal.eq(55.).all()
        assert bool(calls)==(availability=='external_forecast')
    pd.testing.assert_frame_equal(before,exo)
    exo.available_at=origin+pd.Timedelta(seconds=1)
    with pytest.raises(FeatureExecutionError,match='unavailable'):run(engine,history,requests(history),origin,state,forecast,exogenous=exo)


@pytest.mark.parametrize('missing',['reject','zero'])
def test_absent_future_input_requires_explicit_substitute(setup,forecast,missing):
    config,history,_,origin,_,_=setup;config,history,_=exogenous_setup(config,history,origin,missing=missing)
    engine=CanonicalFeaturePipeline(config);state=engine.fit_state(history,training_cutoff=origin,partition_reference='absent-input')
    if missing=='reject':
        with pytest.raises(FeatureExecutionError,match='absent'):run(engine,history,requests(history),origin,state,forecast)
    else:assert run(engine,history,requests(history),origin,state,forecast).signal.eq(0).all()


def test_nullable_category_is_distinct_from_missing_identity(setup,forecast):
    config,history,_,origin,_,_=setup;config,history,exo=exogenous_setup(config,history,origin,categorical=True)
    history.loc[history.index[0],'signal']=None;exo['value']=None
    engine=CanonicalFeaturePipeline(config);state=engine.fit_state(history,training_cutoff=origin,partition_reference='nullable-category')
    assert run(engine,history,requests(history),origin,state,forecast,exogenous=exo).signal.eq(-2).all()
    bad=requests(history);bad.loc[0,'product_class']=None
    with pytest.raises(FeatureExecutionError):run(engine,history,bad,origin,state,forecast,exogenous=exo)


@pytest.mark.parametrize('failure',[False,'exception'])
def test_external_forecast_permission_and_errors_fail_closed(setup,forecast,failure):
    config,history,_,origin,_,_=setup;config,history,exo=exogenous_setup(config,history,origin,availability='external_forecast')
    def authorize(_):
        if failure=='exception':raise FeatureExecutionError('EXTERNAL_PRIVATE_SECRET')
        return False
    engine=CanonicalFeaturePipeline(config,authorize_external_forecast=authorize);state=engine.fit_state(history,training_cutoff=origin,partition_reference='external-training')
    with pytest.raises(FeatureExecutionError) as error:run(engine,history,requests(history),origin,state,forecast,exogenous=exo)
    assert 'EXTERNAL_PRIVATE_SECRET' not in ''.join(traceback.format_exception(error.value))


@pytest.mark.parametrize('bad_value',[np.nan,np.inf,-np.inf,None,True,Decimal('NaN'),Decimal('Infinity'),Decimal('0.1')])
def test_invalid_or_lossy_targets_reject(setup,bad_value):
    config,history,_,origin,_,_=setup;history=history.copy();history['value']=history.value.astype(object);history.loc[0,'value']=bad_value
    with pytest.raises(FeatureExecutionError):CanonicalFeaturePipeline(config).fit_state(history,training_cutoff=origin,partition_reference='bad-target')


def test_signed_zero_and_exact_decimal_are_preserved(setup,forecast):
    config,history,context,origin,_,_=setup;history=history.copy();history['value']=history.value.map(lambda v:Decimal(str(v)))
    history.loc[history.groupby(['organization_id','series_id']).tail(1).index,'value']=Decimal('-0.0000')
    before=history.copy(deep=True);engine=CanonicalFeaturePipeline(config);state=engine.fit_state(history,training_cutoff=origin,partition_reference='decimal-training')
    result=run(engine,history,context,origin,state,forecast)
    assert np.signbit(result.previous).all()
    assert history.value.iloc[1]==Decimal('-20.0')
    pd.testing.assert_frame_equal(history,before,check_exact=True)


@pytest.mark.parametrize('fault',['duplicate','mixed_metric','dimension_drift','unicode','naive','period_end'])
def test_invalid_canonical_feature_inputs(setup,fault):
    config,history,_,origin,_,_=setup;history=history.copy()
    if fault=='duplicate':history=pd.concat([history,history.iloc[[0]]])
    if fault=='mixed_metric':history.loc[0,'metric']='returns'
    if fault=='dimension_drift':history.loc[0,'product_class']='changed'
    if fault=='unicode':history.loc[0,'product_class']='\ud800'
    if fault=='naive':history['period_start']=history.period_start.dt.tz_localize(None)
    if fault=='period_end':history.loc[0,'period_end']=history.period_end.iloc[0]+pd.Timedelta(hours=1)
    with pytest.raises(FeatureExecutionError):CanonicalFeaturePipeline(config).fit_state(history,training_cutoff=origin,partition_reference='bad-input')


@pytest.mark.parametrize('budget',[dict(max_rows=2),dict(max_series=1),dict(max_frame_bytes=10),dict(max_result_bytes=1),dict(max_calculation_cells=1)])
def test_input_and_output_budgets(setup,forecast,budget):
    config,history,context,origin,_,state=setup;engine=CanonicalFeaturePipeline(config,budgets=FeatureBudgets(**budget))
    with pytest.raises(FeatureExecutionError):run(engine,history,context,origin,state,forecast)


def test_period_anchor_and_recursive_calendar_cross_month(setup):
    config=setup[0];history=observations(start='2019-12-02');origin=history.period_end.max()
    config=configured(config,lambda c:(c['execution']['temporal'].update(period_anchor='end'),c['calculations'][5].update(inputs=['period_end'])))
    engine=CanonicalFeaturePipeline(config);state=engine.fit_state(history,training_cutoff=origin,partition_reference='end-anchor')
    predictions=history.groupby('series_id').tail(1).copy();predictions.period_start=origin;predictions.period_end=origin+pd.DateOffset(days=7)
    predictions.value=0.
    result=run(engine,history,requests(history),origin,state,ForecastConfig(forecast_frequency='weekly',forecast_horizon=2),prior_predictions=predictions)
    assert result.month.iloc[:2].tolist()==[2,2]
    assert result.index.get_level_values('period_start')[:2].tolist()==[pd.Timestamp('2020-01-27',tz='UTC'),pd.Timestamp('2020-02-03',tz='UTC')]


@pytest.mark.parametrize('fault',['naive_origin','unaligned_origin','horizon_budget','forecast_frequency','prediction_scope','prediction_target'])
def test_forecast_context_rejects_ambiguity(setup,forecast,fault):
    _,history,context,origin,engine,state=setup;kw={}
    if fault=='naive_origin':origin=origin.tz_localize(None)
    if fault=='unaligned_origin':origin=origin+pd.Timedelta(hours=1)
    if fault=='horizon_budget':forecast=ForecastConfig(forecast_frequency='weekly',forecast_horizon=53)
    if fault=='forecast_frequency':forecast=ForecastConfig(forecast_frequency='daily',forecast_horizon=1)
    if fault.startswith('prediction'):
        prediction=history.iloc[[0]].copy();prediction.period_start=origin;prediction.period_end=origin+pd.DateOffset(days=7)
        if fault=='prediction_scope':prediction.series_id=UUID(int=99);forecast=ForecastConfig(forecast_frequency='weekly',forecast_horizon=2)
        # A prediction of the current/only target must not be supplied as history.
        kw['prior_predictions']=prediction
    with pytest.raises(FeatureExecutionError):run(engine,history,context,origin,state,forecast,**kw)


def test_known_future_boolean_is_explicit_output_type(setup,forecast):
    config,history,_,origin,_,_=setup;config,history,exo=exogenous_setup(config,history,origin)
    config=configured(config,lambda c:c['output_dtypes'].update(signal='bool'))
    history['signal']=True;exo['value']=True
    engine=CanonicalFeaturePipeline(config);state=engine.fit_state(history,training_cutoff=origin,partition_reference='known-event')
    result=run(engine,history,requests(history),origin,state,forecast,exogenous=exo)
    assert result.signal.dtype==bool and result.signal.all()


@pytest.mark.parametrize('missing',['reject','zero'])
def test_missing_numeric_auxiliary_behavior(setup,forecast,missing):
    config,history,_,origin,_,_=setup;config,history,exo=exogenous_setup(config,history,origin,missing=missing)
    exo['value']=np.nan;engine=CanonicalFeaturePipeline(config);state=engine.fit_state(history,training_cutoff=origin,partition_reference='nullable-numeric')
    if missing=='zero':assert run(engine,history,requests(history),origin,state,forecast,exogenous=exo).signal.eq(0).all()
    else:
        with pytest.raises(FeatureExecutionError):run(engine,history,requests(history),origin,state,forecast,exogenous=exo)
    exo['value']=np.inf
    with pytest.raises(FeatureExecutionError):run(engine,history,requests(history),origin,state,forecast,exogenous=exo)


@pytest.mark.parametrize('fault',['duplicate','wrong_tenant','absent_second_horizon'])
def test_exogenous_alignment_and_ambiguity(setup,forecast,fault):
    config,history,_,origin,_,_=setup;config,history,exo=exogenous_setup(config,history,origin)
    engine=CanonicalFeaturePipeline(config);state=engine.fit_state(history,training_cutoff=origin,partition_reference='exo-scoping')
    if fault=='duplicate':exo=pd.concat([exo,exo.iloc[[0]]])
    if fault=='wrong_tenant':exo['organization_id']=UUID(int=23)
    kw={}
    if fault=='absent_second_horizon':
        forecast=ForecastConfig(forecast_frequency='weekly',forecast_horizon=2)
        pred=history.groupby('series_id').tail(1).copy();pred.period_start=origin;pred.period_end=origin+pd.DateOffset(days=7)
        kw['prior_predictions']=pred
    with pytest.raises(FeatureExecutionError):run(engine,history,requests(history),origin,state,forecast,exogenous=exo,**kw)


def test_fitted_state_and_text_budgets(setup,forecast):
    config,history,context,origin,_,state=setup
    engine=CanonicalFeaturePipeline(config,budgets=FeatureBudgets(max_categories=1))
    with pytest.raises(FeatureExecutionError):run(engine,history,context,origin,state,forecast)
    with pytest.raises(FeatureExecutionError):engine.fit_state(history,training_cutoff=origin,partition_reference='too-many-categories')
    engine=CanonicalFeaturePipeline(config,budgets=FeatureBudgets(max_cell_bytes=1))
    bad=history.copy();bad['product_class']='long-name'
    with pytest.raises(FeatureExecutionError):engine.fit_state(bad,training_cutoff=origin,partition_reference='long-cell')


def test_integer_output_overflow_and_numeric_magnitude_fail_closed(setup,forecast):
    config,history,context,origin,_,_=setup
    config=configured(config,lambda c:c['output_dtypes'].update(previous='int8'))
    history=history.copy();history.loc[history.groupby('series_id').tail(1).index,'value']=256.
    engine=CanonicalFeaturePipeline(config);state=engine.fit_state(history,training_cutoff=origin,partition_reference='overflow-output')
    with pytest.raises(FeatureExecutionError,match='integer output'):run(engine,history,context,origin,state,forecast)
    bad=history.copy();bad['value']=bad.value.astype(object);bad.loc[0,'value']=10**400
    with pytest.raises(FeatureExecutionError):engine.fit_state(bad,training_cutoff=origin,partition_reference='unrepresentable-target')


def test_numerical_overflow_never_becomes_a_feature(setup,forecast):
    config,history,context,origin,_,_=setup;history=history.copy();history['value']=1e308
    engine=CanonicalFeaturePipeline(config);state=engine.fit_state(history,training_cutoff=origin,partition_reference='overflow-statistic')
    with pytest.raises(FeatureExecutionError,match='nonfinite calculated'):run(engine,history,context,origin,state,forecast)


def test_late_history_and_future_prediction_availability_are_not_inferred(setup,forecast):
    _,history,context,origin,engine,state=setup
    late=history.copy();late.loc[late.groupby('series_id').tail(1).index,'available_at']=origin+pd.Timedelta(seconds=1)
    # Period completion alone is insufficient; period lags reject the resulting
    # gap instead of consuming the late value or inserting a zero observation.
    with pytest.raises(FeatureExecutionError,match='missing configured history'):
        run(engine,late,context,origin,state,forecast)
    with pytest.raises(FeatureExecutionError,match='fit partition'):
        engine.fit_state(late,training_cutoff=origin,partition_reference='late-fit')
    with pytest.raises(FeatureExecutionError,match='inputs missing'):
        run(engine,history.drop(columns='available_at'),context,origin,state,forecast)
    pred=history.groupby('series_id').tail(1).copy();pred.period_start=origin;pred.period_end=origin+pd.DateOffset(days=7)
    pred.available_at=origin+pd.Timedelta(seconds=1)
    with pytest.raises(FeatureExecutionError,match='prediction scope or horizon'):
        run(engine,history,context,origin,state,ForecastConfig(forecast_frequency='weekly',forecast_horizon=2),prior_predictions=pred)


@pytest.mark.parametrize('field',('id','unit','meaning','version','calculation','history'))
@pytest.mark.parametrize('channel',('warnings','stderr','logging'))
def test_malformed_configuration_diagnostics_are_sanitized(monkeypatch,capsys,caplog,field,channel):
    import logging
    import sys
    import warnings

    config=feature_config();secret='CFGKEY42'
    def malformed(obj,**changes):
        return type(obj).model_construct(**{**obj.__dict__,**changes})
    execution=config.execution
    variants={
        'id':malformed(config,configuration_id={secret:secret}),
        'unit':malformed(config,execution=malformed(execution,measurement=malformed(execution.measurement,unit={secret:secret}))),
        'meaning':malformed(config,execution=malformed(execution,metric_meaning={secret:secret})),
        'version':malformed(config,version=[secret]),
        'calculation':malformed(config,calculations=(secret,)),
        'history':malformed(config,execution=malformed(execution,temporal=malformed(execution.temporal,minimum_history=secret))),
    }
    calls=[]
    def unexpected(*args,**kwargs):
        calls.append(True)
        raise AssertionError('malformed configuration reached executable validation')
    monkeypatch.setattr(CanonicalFeaturePipeline,'_validate_configuration',unexpected)
    capture_enabled=logging._warnings_showwarning is not None
    try:
        if channel=='logging':logging.captureWarnings(True)
        with warnings.catch_warnings(record=channel=='warnings') as emitted:
            warnings.simplefilter('always')
            if channel=='stderr':
                def stderr_warning(message,category,filename,lineno,file=None,line=None):
                    print(warnings.formatwarning(message,category,filename,lineno,line),file=file or sys.stderr,end='')
                warnings.showwarning=stderr_warning
            with pytest.raises(FeatureExecutionError) as error:
                CanonicalFeaturePipeline(variants[field])
            warning_text='' if emitted is None else '\n'.join(str(w.message) for w in emitted)
    finally:
        logging.captureWarnings(capture_enabled)
    output=capsys.readouterr()
    assert not calls
    assert not warning_text and not output.err
    assert secret not in warning_text+output.out+output.err+caplog.text+''.join(traceback.format_exception(error.value))
    assert str(error.value)=='unsupported executable feature configuration'
    with warnings.catch_warnings(record=True) as unrelated:
        warnings.simplefilter('always')
        warnings.warn('unrelated feature diagnostic warning',UserWarning)
    assert len(unrelated)==1 and str(unrelated[0].message)=='unrelated feature diagnostic warning'
