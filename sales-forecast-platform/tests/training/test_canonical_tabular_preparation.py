"""M3 actual FINALIZED provider/canonical execution; no model fitting."""
from contextlib import contextmanager
import runpy
import traceback
import warnings
from unittest.mock import Mock

import pandas as pd
import pytest

from src.data.contracts.model_evaluation import EvaluationDeclarationError
from src.data.contracts.model_representation import require_compatible
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
from src.preprocessing.canonical_tabular import TabularPreprocessingState, TabularOutput, bind_output, FrozenTabularPreprocessor
from src.services.canonical_tabular_preparation_service import CanonicalTabularPreparationService


@pytest.fixture
def bundle(tmp_path):
    factory = runpy.run_path('tests/training/test_temporal_training_preparation.py')['make_temporal'].__wrapped__(tmp_path)
    def make(**kwargs):
        b = factory(**kwargs)
        b['tabular'] = CanonicalTabularPreparationService(b['service'])
        return b
    return make


def prepare(b, **kwargs):
    return b['tabular'].prepare(b['plan'], b['configuration'], **kwargs)


@pytest.mark.parametrize('family', ['xgboost', 'lightgbm'])
def test_train_only_fitting_state_and_m2_parity(bundle, monkeypatch, family):
    b = bundle(exo=True)
    fits=[]; original=CanonicalFeaturePipeline.fit_state
    def fit(self, frame, **kw):
        fits.append(frame.copy())
        assert frame.period_end.max() <= b['at'](4)
        return original(self, frame, **kw)
    monkeypatch.setattr(CanonicalFeaturePipeline, 'fit_state', fit)
    result = prepare(b, model_family=family)
    assert len(fits)==1 and len(result.outputs)==4
    assert len({o.state.state_id for o in result.outputs})==1
    assert {o.representation.binding.partition for o in result.outputs}=={'training','validation','test'}
    first=result.outputs[0]
    assert first.state.canonical_state.vocabularies['promotion']==('A',)
    assert first.matrix.promotion.eq(0).all()
    assert all(o.matrix.promotion.eq(-1).all() for o in result.outputs[1:])
    for output in result.outputs:
        assert output.model_family==family
        restored=TabularOutput.model_validate_json(output.canonical_json)
        assert restored.output_id==output.output_id
        assert restored.matrix.equals(output.matrix)
        assert require_compatible(first.representation,output.representation)==first.representation.compatibility_id
        loaded=TabularPreprocessingState.model_validate_json(output.state.canonical_json)
        assert loaded.state_id==output.state.state_id
        frame=output.matrix; frame.iloc[0,0]=99
        assert not frame.equals(output.matrix)
    X,y=result.partition('training')
    assert len(X)==len(y)==4


def test_multiorganization_boundaries_and_no_silent_training_merge(bundle):
    b=bundle(organizations=(2,3)); result=prepare(b)
    assert len({o.state.state_id for o in result.outputs})==2
    for output in result.outputs:
        assert set(output.matrix.index.get_level_values(0))=={output.state.dataset.reference.organization_id}
    with pytest.raises(EvaluationDeclarationError):result.partition('training')


@pytest.mark.parametrize('change', ['future_labels','future_category'])
def test_future_values_do_not_fit_or_change_training(bundle, change):
    base=prepare(bundle(exo=True))
    def mutate(raw):
        for n in range(len(raw)):
            if n%12>=6:
                if change=='future_labels': raw.loc[n,'reported']=999999.0
                else: raw.loc[n,'offer']='UNSEEN_FUTURE'
    later=prepare(bundle(exo=True,raw_change=mutate))
    assert base.outputs[0].values==later.outputs[0].values
    assert base.outputs[1].values==later.outputs[1].values
    assert base.outputs[0].state.canonical_state.vocabularies==later.outputs[0].state.canonical_state.vocabularies


@pytest.mark.parametrize('fault',['family','dtype','horizon','authorization','release'])
def test_reject_before_output(bundle, monkeypatch, fault):
    b=bundle(exo=True); kwargs={}
    if fault=='family':kwargs['model_family']='lstm'
    elif fault=='dtype':kwargs['dtype']='int8'
    elif fault=='horizon':
        b['plan']=b['plan'].model_copy(update={'forecast_horizon':2})
    elif fault=='authorization':b['service'].authorize=lambda *_:False
    else:b['service'].verify_availability=lambda *_:False
    fits=Mock(side_effect=AssertionError('no fit'))
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',fits)
    with pytest.raises(EvaluationDeclarationError):prepare(b,**kwargs)
    fits.assert_not_called()


@pytest.mark.parametrize('fault',['state','schema','order','bool','nan','range','lossy'])
def test_actual_output_and_frozen_state_substitution_reject(bundle,fault):
    result=prepare(bundle()); output=result.outputs[0]
    state=output.state; r=output.representation; matrix=output.matrix
    if fault=='state':state=state.model_copy(update={'canonical_state':state.canonical_state.model_copy(update={'training_cutoff':pd.Timestamp('2099-01-01',tz='UTC')})})
    elif fault=='schema':state=state.model_copy(update={'canonical_schema':state.canonical_schema.model_copy(update={'fitted_state_id':'9'*64})})
    elif fault=='order':matrix=matrix.iloc[:,::-1]
    else:
        # Preserve raw identity in an object column; no numpy conversion.
        name=matrix.columns[1]; matrix[name]=matrix[name].astype(object)
        matrix.iloc[0,1]={'bool':True,'nan':float('nan'),'range':float('inf'),'lossy':2**54+1}[fault]
    with pytest.raises(EvaluationDeclarationError):bind_output(state,r,matrix,'xgboost')


def test_strict_float32_rejects_lossy_rolling_without_rounding(bundle):
    b=bundle()
    with pytest.raises(EvaluationDeclarationError):prepare(b,dtype='float32')


def test_no_raw_csv_or_legacy_recoding(monkeypatch):
    from src.training.train_pipeline import run_training_pipeline
    from src.preprocessing.tree_preprocessor import TreePreprocessor
    reference=runpy.run_path('tests/fixtures/reference_training.py')['reference_frame']()
    read=Mock(return_value=reference); monkeypatch.setattr(pd,'read_csv',read)
    with pytest.raises(ValueError,match='canonical tabular service'):run_training_pipeline('secret.csv')
    for operation in (TreePreprocessor().fit_transform,TreePreprocessor().transform):
        with pytest.raises(ValueError,match='canonical'):operation(pd.DataFrame())
    read.assert_called_once_with('secret.csv')


def test_sanitized_diagnostics_and_unrelated_warnings(bundle,capsys,caplog):
    b=bundle(); b['configuration']=b['configuration'].model_copy(update={'configuration_id':{'M3_SECRET_NEW':'VALUE_SECRET'}})
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        with pytest.raises(EvaluationDeclarationError) as exc:prepare(b)
        warnings.warn('unrelated M3 warning',UserWarning)
    e=exc.value
    assert e.__cause__ is None and e.__context__ is None
    captured=capsys.readouterr()
    diagnostics=str(e)+repr(e)+repr(e.args)+''.join(traceback.format_exception(e))+captured.err+caplog.text+''.join(str(w.message) for w in seen)
    assert 'M3_SECRET_NEW' not in diagnostics and 'VALUE_SECRET' not in diagnostics
    assert [str(w.message) for w in seen]==['unrelated M3 warning']


def test_loaded_frozen_inference_is_identical_without_refitting(bundle,monkeypatch):
    b=bundle(exo=True); inputs=[]; original=CanonicalFeaturePipeline.prepare_training
    def record(self, history, requests, state, **kwargs):
        inputs.append((history.copy(deep=True), requests.copy(deep=True), kwargs.copy()))
        return original(self, history, requests, state, **kwargs)
    monkeypatch.setattr(CanonicalFeaturePipeline,'prepare_training',record)
    result=prepare(b)
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',Mock(side_effect=AssertionError('no refit')))
    for output,(history,requests,kwargs) in zip(result.outputs,inputs):
        loaded=TabularPreprocessingState.model_validate_json(output.state.canonical_json)
        kernel=FrozenTabularPreprocessor(loaded)
        restored=kernel.transform(history,requests,output.representation,exogenous=kwargs['exogenous'])
        assert restored.values==output.values
        assert restored.output_id==output.output_id
        future=history.iloc[[-1]].copy()
        future['period_start']=b['at'](10);future['period_end']=b['at'](11)
        future['available_at']=b['at'](11);future['value']=99999999.0
        injected=pd.concat([history,future])
        assert kernel.transform(injected,requests,output.representation,exogenous=kwargs['exogenous']).values==output.values
        wrong=requests.copy();wrong['location']='SUBSTITUTED'
        with pytest.raises(EvaluationDeclarationError):kernel.transform(history,wrong,output.representation,exogenous=kwargs['exogenous'])


@pytest.mark.parametrize('case',['missing','late','unknown_reject'])
def test_category_and_feature_availability_policy_is_explicit(bundle,case):
    def mutate(raw):
        if case=='missing':raw.loc[4,'offer']=None
        if case=='late':raw.loc[0,'promo_release']=pd.Timestamp('2020-03-01',tz='UTC')
    if case=='missing':
        from src.providers.dataset_store_provider import StagingError
        with pytest.raises(StagingError):bundle(exo=True,raw_change=mutate)
        return
    b=bundle(exo=True,raw_change=mutate)
    if case=='unknown_reject':
        from src.data.contracts.features import FeatureConfiguration
        from src.data.contracts.temporal_preparation import TemporalPreparationPlan
        cfg=b['configuration'].model_dump()
        cfg['execution']['categories']=tuple(dict(r,unknown='reject',unknown_code=None) if r['field']=='promotion' else r
            for r in cfg['execution']['categories'])
        b['configuration']=FeatureConfiguration.model_validate(cfg)
        plan=b['plan'].model_dump();plan['configuration_digest']=CanonicalFeaturePipeline(b['configuration']).digest
        b['plan']=TemporalPreparationPlan.model_validate(plan)
    with pytest.raises(EvaluationDeclarationError):prepare(b)


@pytest.mark.parametrize('family',['xgboost','lightgbm'])
def test_exact_float32_output_when_features_are_representable(bundle,family):
    from src.data.contracts.features import FeatureConfiguration
    from src.data.contracts.temporal_preparation import TemporalPreparationPlan
    b=bundle();cfg=b['configuration'].model_dump()
    names=('location','lag1')
    cfg['ordered_outputs']=names
    cfg['output_dtypes']={n:cfg['output_dtypes'][n] for n in names}
    cfg['missing_value_behavior']={n:cfg['missing_value_behavior'][n] for n in names}
    b['configuration']=FeatureConfiguration.model_validate(cfg)
    p=b['plan'].model_dump();p['configuration_digest']=CanonicalFeaturePipeline(b['configuration']).digest
    b['plan']=TemporalPreparationPlan.model_validate(p)
    result=prepare(b,model_family=family,dtype='float32')
    assert all(str(o.matrix.lag1.dtype)=='float32' for o in result.outputs)
    assert all(o.representation.feature_schema.features[1].numeric_policy=='strict_exact_float32_v1' for o in result.outputs)


def test_output_budget_and_cancellation_fail_closed(bundle):
    from src.data.contracts.temporal_preparation import TemporalPreparationPlan
    b=bundle();p=b['plan'].model_dump();p['budgets']['max_assembly_bytes']=1
    b['plan']=TemporalPreparationPlan.model_validate(p)
    with pytest.raises(EvaluationDeclarationError):prepare(b)
    b=bundle()
    with pytest.raises(EvaluationDeclarationError):prepare(b,cancelled=lambda:True)


def test_explicit_missing_mask_and_reserved_category_code(bundle,monkeypatch):
    from src.data.contracts.features import FeatureConfiguration
    from src.data.contracts.temporal_preparation import TemporalPreparationPlan
    b=bundle(exo=True)
    cfg=b['configuration'].model_dump()
    cfg['execution']['categories']=tuple(dict(r,missing='use_code',missing_code=-2) if r['field']=='promotion' else r
        for r in cfg['execution']['categories'])
    cfg['lag_offsets']=(*cfg['lag_offsets'],52)
    cfg['calculations']=(*cfg['calculations'],dict(output='missing_lag',operation='lag',inputs=('value',),parameters={'offset':52}))
    cfg['ordered_outputs']=(*cfg['ordered_outputs'],'missing_lag')
    cfg['output_dtypes']['missing_lag']='float64';cfg['missing_value_behavior']['missing_lag']='nan'
    b['configuration']=FeatureConfiguration.model_validate(cfg)
    p=b['plan'].model_dump();p['configuration_digest']=CanonicalFeaturePipeline(b['configuration']).digest
    b['plan']=TemporalPreparationPlan.model_validate(p)
    captured=[];original=CanonicalFeaturePipeline.prepare_training
    def record(self,history,requests,state,**kw):
        captured.append((history.copy(),requests.copy(),kw['exogenous'].copy()))
        return original(self,history,requests,state,**kw)
    monkeypatch.setattr(CanonicalFeaturePipeline,'prepare_training',record)
    result=prepare(b)
    assert all(o.matrix.missing_lag.isna().all() for o in result.outputs)
    assert all(all(row[-1] is None for row in o.values) for o in result.outputs)
    assert all(all(row[-1] is True for row in o.representation.missing_mask) for o in result.outputs)
    history,requests,exogenous=captured[1]
    # The publication fixture rejects null required auxiliary values at admission.
    # Independently exercise the pure frozen kernel's explicit missing policy.
    mask=exogenous.period_start.eq(b['at'](4))
    exogenous.loc[mask,'value']=None
    output=result.outputs[1]
    missing=FrozenTabularPreprocessor(output.state).transform(history,requests,output.representation,exogenous=exogenous)
    assert missing.matrix.promotion.eq(-2).all()
    for o in result.outputs:
        assert TabularOutput.model_validate_json(o.canonical_json).output_id==o.output_id


def test_multi_department_history_and_output_are_isolated(bundle):
    def mutate(raw):
        raw.loc[raw.branch.eq('south'),'group']='food'
    b=bundle(organizations=(2,3),raw_change=mutate)
    result=prepare(b)
    for o in result.outputs:
        by_location={dict(grid[0].dimensions)['location']:(grid[0],row) for grid,row in zip(o.representation.samples,o.values)}
        north,nrow=by_location['north'];south,srow=by_location['south']
        assert dict(north.dimensions)['department']=='toys'
        assert dict(south.dimensions)['department']=='food'
        assert nrow[1]+100==srow[1]  # separate series, no cross-department lag


def test_guarded_state_and_output_ingress_and_signed_zero(bundle,capsys,caplog):
    import json
    import math
    b=bundle();o=prepare(b).outputs[0]
    matrix=o.matrix.copy();matrix.iloc[0,1]=-0.0
    bound=bind_output(o.state,o.representation,matrix,'xgboost')
    assert math.copysign(1,bound.matrix.iloc[0,1])==-1
    assert math.copysign(1,TabularOutput.model_validate_json(bound.canonical_json).values[0][1])==-1
    for value in (o,o.state):
        raw=value.model_dump(mode='json');secret='M3_FIELD_'+__import__('uuid').uuid4().hex
        raw[secret]={'value':secret}
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter('always')
            with pytest.raises(EvaluationDeclarationError) as exc:type(value).model_validate_json(json.dumps(raw))
        e=exc.value;out=capsys.readouterr()
        assert not seen and e.__cause__ is None and e.__context__ is None
        assert secret not in str(e)+repr(e)+repr(e.args)+''.join(traceback.format_exception(e))+out.err+caplog.text
        wire='{"version":"999",'+value.canonical_json[1:]
        with pytest.raises(EvaluationDeclarationError):type(value).model_validate_json(wire)


def test_missing_reporting_context_does_not_discard_model(bundle,monkeypatch,tmp_path):
    from types import ModuleType
    import numpy as np
    import sys
    from src.monitoring.outlier_analysis import OutlierReportOptions
    from src.training.train_pipeline import run_training_pipeline
    b=bundle()
    model=Mock();model.predict.side_effect=lambda X:np.zeros(len(X))
    trainer=ModuleType('src.training.trainer');trainer.train_model=Mock(return_value=model)
    monkeypatch.setitem(sys.modules,trainer.__name__,trainer)
    result=run_training_pipeline('unused.csv',tabular_service=b['tabular'],temporal_plan=b['plan'],feature_configuration=b['configuration'],
        outlier_report=OutlierReportOptions(str(tmp_path/'report.csv')))
    assert result['model'] is model and result['outlier_results']['status']=='ERROR'
    assert result['cv_results']['status']=='NOT_RUN' and not (tmp_path/'report.csv').exists()
    X,y=trainer.train_model.call_args.args
    assert len(X)==len(y)==4
    assert set(X.index.get_level_values('period_start'))=={b['at'](4),b['at'](5)}


@pytest.mark.parametrize('fault',['calendar','incomplete_training_target'])
def test_coherently_rewritten_state_metadata_is_not_calendar_compatible(bundle,fault):
    from src.data.contracts.backtesting import identity
    from src.data.contracts.features import FeatureState
    from src.data.contracts.temporal_preparation import TemporalPreparationPlan
    output=prepare(bundle()).outputs[0];raw=output.state.model_dump()
    plan=raw['plan']
    if fault=='calendar':plan['timezone']='Etc/UTC'
    else:plan['folds'][0]['model_fit_cutoff']=plan['folds'][0]['training_origins'][-1]+pd.Timedelta(days=1)
    plan=TemporalPreparationPlan.model_validate(plan);raw['plan']=plan
    fold=plan.folds[0]
    engine=CanonicalFeaturePipeline(output.state.canonical_schema.configuration)
    raw['canonical_state']['training_partition_reference']=identity([plan.digest,fold.fold_id,raw['dataset'],engine.digest,
        engine._time(fold.calibration_start).isoformat(),engine._time(fold.categorical_cutoff).isoformat()])
    changed=FeatureState.model_validate(raw['canonical_state'])
    raw['canonical_schema']['fitted_state_id']=changed.state_id
    with pytest.raises(EvaluationDeclarationError):TabularPreprocessingState.model_validate(raw)


def scaler_declaration(output, policy):
    from src.data.contracts.backtesting import identity
    from src.data.contracts.model_representation import TabularRepresentation
    fold=output.state.plan.folds[0]
    raw=output.representation.model_dump()
    raw['binding']['scaler']=dict(policy=policy,state_digest='1'*64,
        training_plan_digest=output.state.plan.digest,fold_id=fold.fold_id,
        training_origins_digest=identity([o.isoformat() for o in fold.training_origins]),
        training_cutoff=fold.model_fit_cutoff,canonical_state_id=output.state.canonical_state.state_id,
        source_schema_id=output.state.canonical_schema.schema_id,
        organization_ids=output.state.canonical_state.organization_ids)
    # Deliberately valid M2 metadata: only M3 execution must reject it.
    declaration=TabularRepresentation.model_validate(raw)
    assert declaration.binding.scaler.policy==policy
    return declaration


@pytest.mark.parametrize('policy',['standard_v1','minmax_v1','identity_v1'])
def test_all_scaler_references_reject_at_every_m3_boundary(bundle,monkeypatch,policy,capsys,caplog):
    import json
    import uuid
    output=prepare(bundle()).outputs[0]
    declaration=scaler_declaration(output,policy)
    original_bytes=output.canonical_bytes;original_state=output.state.canonical_bytes
    raw=output.model_dump();raw['representation']=declaration.model_dump()
    wire=json.dumps(output.model_dump(mode='json') | {'representation':declaration.model_dump(mode='json')})
    kernel=FrozenTabularPreprocessor(output.state)
    matrix=output.matrix;before=matrix.copy(deep=True)
    cells=Mock(side_effect=AssertionError('no row extraction for unsupported scaling'))
    monkeypatch.setattr(matrix,'itertuples',cells)
    native_features=Mock(side_effect=AssertionError('no execution for unsupported scaling'))
    monkeypatch.setattr(CanonicalFeaturePipeline,'prepare_inference',native_features)
    class NoBuffers:
        def __getattribute__(self,name):
            accesses.append(name)
            raise AssertionError('no input access for unsupported scaling')
    accesses=[]
    operations=(lambda:TabularOutput(**raw), lambda:TabularOutput.model_validate(raw),
        lambda:TabularOutput.model_validate_json(wire),
        lambda:bind_output(output.state,declaration,matrix,'xgboost'),
        lambda:kernel.transform(NoBuffers(),NoBuffers(),declaration))
    secret='SCALER_CONTEXT_'+uuid.uuid4().hex
    messages=[]
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        for operation in operations:
            try:raise RuntimeError(secret)
            except RuntimeError:
                with pytest.raises(EvaluationDeclarationError) as caught:operation()
                error=caught.value
                assert error.__context__ is None and error.__cause__ is None
                diagnostics=str(error)+repr(error.args)+repr(error)+''.join(traceback.format_exception(error))
                assert secret not in diagnostics
                messages.append(str(error))
    captured=capsys.readouterr()
    assert messages==['invalid non-authoritative evaluation declaration']*len(operations)
    assert not seen and secret not in captured.out+captured.err+caplog.text
    cells.assert_not_called();native_features.assert_not_called();assert not accesses
    pd.testing.assert_frame_equal(matrix,before)
    assert output.canonical_bytes==original_bytes and output.state.canonical_bytes==original_state
    assert output.representation.binding.scaler is None
    # Failed attempts cannot poison or mutate the original valid output.
    restored=TabularOutput.model_validate_json(original_bytes)
    assert restored.output_id==output.output_id and restored.matrix.equals(before)


def test_scaler_none_frozen_training_inference_identity_remains_unchanged(bundle,monkeypatch):
    inputs=[];original=CanonicalFeaturePipeline.prepare_training
    def capture(self,history,requests,state,**kwargs):
        inputs.append((history.copy(),requests.copy(),kwargs['exogenous']))
        return original(self,history,requests,state,**kwargs)
    monkeypatch.setattr(CanonicalFeaturePipeline,'prepare_training',capture)
    result=prepare(bundle(exo=True))
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',Mock(side_effect=AssertionError('no inference refit')))
    for output,(history,requests,exogenous) in zip(result.outputs,inputs):
        assert output.representation.binding.scaler is None
        rebound=bind_output(output.state,output.representation,output.matrix,output.model_family)
        inferred=FrozenTabularPreprocessor(output.state).transform(history,requests,output.representation,exogenous=exogenous)
        assert output.values==rebound.values==inferred.values
        assert output.canonical_bytes==rebound.canonical_bytes==inferred.canonical_bytes
        assert output.output_id==rebound.output_id==inferred.output_id
        assert require_compatible(output.representation,inferred.representation)==output.representation.compatibility_id
