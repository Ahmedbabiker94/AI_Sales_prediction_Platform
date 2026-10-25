"""M5 tests use actual bounded FINALIZED publications, no model estimators."""
import json
import math
import runpy
import traceback
import warnings
from unittest.mock import Mock
import numpy as np
import pandas as pd
import pytest
from src.data.contracts.features import FeatureConfiguration
from src.data.contracts.temporal_preparation import TemporalPreparationPlan
from src.data.contracts.model_evaluation import EvaluationDeclarationError
from src.data.contracts.model_representation import require_compatible
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
from src.preprocessing.canonical_tabular import TabularOutput
from src.preprocessing.canonical_transformer import (
    TransformerPreparationRequest,TransformerPreprocessingState,TransformerOutput,
    fit_transformer_state,FrozenTransformerPreprocessor,
)
from src.services.canonical_tabular_preparation_service import CanonicalTabularPreparationService
from src.services.canonical_transformer_preparation_service import CanonicalTransformerPreparationService


@pytest.fixture
def bundle(tmp_path):
    factory=runpy.run_path('tests/training/test_temporal_training_preparation.py')['make_temporal'].__wrapped__(tmp_path)
    def build(*,rule='known_in_advance',numeric=False,**kwargs):
        if numeric:
            change=kwargs.pop('raw_change',None)
            def raw_change(raw):
                raw['offer']=[float(i%2*2) for i in range(len(raw))]
                if change:change(raw)
            kwargs['raw_change']=raw_change
        b=factory(exo=True,**kwargs)
        cfg=b['configuration'].model_dump()
        if numeric:
            del cfg['category_encoding']['promotion']
            cfg['execution']['categories']=tuple(r for r in cfg['execution']['categories'] if r['field']!='promotion')
            cfg['output_dtypes']['promotion']='float64'
        ex=cfg['execution']['exogenous'][0];ex['availability']=rule;ex['offset']=None if rule in ('known_in_advance','external_forecast','observed_only') else 1
        b['configuration']=FeatureConfiguration.model_validate(cfg)
        p=b['plan'].model_dump();p['configuration_digest']=CanonicalFeaturePipeline(b['configuration']).digest
        b['plan']=TemporalPreparationPlan.model_validate(p)
        b['tabular']=CanonicalTabularPreparationService(b['service'])
        b['transformer']=CanonicalTransformerPreparationService(b['tabular'])
        return b
    return build


def request(b,**kw):
    data=dict(fold_id='fold-1',origins=tuple(b['at'](i) for i in (5,6,7)),feature_names=('lag1',),
        static_names=('location',),known_future_names=('promotion',),
        window=dict(lookback=3,window=3,stride=1,past_offsets=(-3,-2,-1),padding='left'))
    data.update(kw);return TransformerPreparationRequest(**data)


def prepare(b,**kw):return b['transformer'].prepare(b['plan'],b['configuration'],request(b,**kw))


def test_provider_backed_stream_values_statistics_masks_and_identity(bundle,monkeypatch):
    b=bundle();opened=b['provider'].open_read;reads=[];fitted=[]
    from contextlib import contextmanager
    @contextmanager
    def capture(*a,**kw):
        reads.append(1)
        with opened(*a,**kw) as session:yield session
    fit=CanonicalFeaturePipeline.fit_state
    def fitting(self,frame,**kw):
        fitted.append(frame.copy());assert frame.period_end.max()<=b['at'](4)
        return fit(self,frame,**kw)
    monkeypatch.setattr(b['provider'],'open_read',capture);monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',fitting)
    result=prepare(b);assert len(reads)==len(fitted)==1
    assert len(result.outputs)==3 and len(result.targets)==3
    first=result.outputs[0];state=first.state
    assert state.encoder.centers==(62.,) and state.encoder.scales==(math.sqrt(2516.),)
    assert state.context.centers==(0.,0.) and state.context.scales==(1.,1.)
    assert first.encoder_tensor.shape==(2,3,1) and first.static_tensor.shape==(2,1) and first.decoder_tensor.shape==(2,1,1)
    assert first.encoder_values[0][-1][0]==(8.-62.)/math.sqrt(2516.)
    assert first.representation.family=='transformer' and first.model_family=='transformer'
    assert first.representation.masks.lengths==(1,1)
    assert first.static_values==((0.,),(1.,))
    assert first.known_future_values==(((-1.,),),((-1.,),)) # original train-prefix vocabulary contains A, not B
    for out in result.outputs:
        loaded=TransformerOutput.model_validate_json(out.canonical_json)
        assert loaded==out and loaded.canonical_bytes==out.canonical_bytes and loaded.output_id==out.output_id
        assert loaded.state.state_id==TransformerPreprocessingState.model_validate_json(state.canonical_json).state_id
        assert out.representation.binding.scaler==out.state.scaler_reference
        assert require_compatible(first.representation,out.representation)==first.representation.compatibility_id
        assert out.decoder_periods==tuple((grid[0].forecast_origin,) for grid in out.representation.samples)
        array=out.decoder_tensor;array[:]=987;assert not np.array_equal(array,out.decoder_tensor)


@pytest.mark.parametrize('rule',['lagged','observed_only'])
def test_nonfuture_rules_reject(bundle,rule):
    with pytest.raises(EvaluationDeclarationError):prepare(bundle(rule=rule))


def test_unpublished_covariate_rejects_before_numeric_fit(bundle,monkeypatch):
    b=bundle(raw_change=lambda raw:raw.__setitem__('promo_release',raw['to']))
    import src.services.canonical_transformer_preparation_service as service
    fitter=Mock(side_effect=AssertionError('unpublished fit'));monkeypatch.setattr(service,'fit_transformer_state',fitter)
    with pytest.raises(EvaluationDeclarationError):prepare(b)
    fitter.assert_not_called()


@pytest.mark.parametrize('fault',['actual','calculated','no_evidence','missing_zero','duplicate','horizon','future_offsets'])
def test_future_role_and_geometry_rejection(bundle,fault):
    b=bundle();kw={}
    if fault=='actual':kw['known_future_names']=('lag1',);kw['feature_names']=('promotion',)
    elif fault=='calculated':kw['static_names']=('lag1',);kw['feature_names']=('location',)
    elif fault=='duplicate':kw['static_names']=('lag1',)
    elif fault=='horizon':kw['decoder_horizon']=2
    elif fault=='future_offsets':kw['future_offsets']=(1,)
    else:
        cfg=b['configuration'].model_dump();r=cfg['execution']['exogenous'][0]
        if fault=='no_evidence':r['evidence_field']=None
        else:r.update(missing='zero',substitute_reference='test-explicit-substitute')
        b['configuration']=FeatureConfiguration.model_validate(cfg)
        p=b['plan'].model_dump();p['configuration_digest']=CanonicalFeaturePipeline(b['configuration']).digest
        b['plan']=TemporalPreparationPlan.model_validate(p)
    with pytest.raises(EvaluationDeclarationError):prepare(b,**kw)


@pytest.mark.parametrize('fault',['scaler_policy','scaler_digest','context_scaler','static_value','future_dtype','future_mask','static_mask','release',
    'decoder_period','encoder_period','length','horizon','future_evidence','context_evidence','reordered_schema','nonfinite','boolean'])
def test_direct_output_semantic_substitution_rejects(bundle,fault):
    output=prepare(bundle()).outputs[0];raw=output.model_dump(mode='json')
    if fault=='scaler_policy':raw['representation']['binding']['scaler']['policy']='identity_v1'
    elif fault=='scaler_digest':raw['representation']['binding']['scaler']['state_digest']='9'*64
    elif fault=='context_scaler':raw['state']['context']['scales'][0]=2.
    elif fault=='static_value':raw['static_values'][0][0]=17.
    elif fault=='future_dtype':raw['representation']['known_future']['features'][0]['dtype']='float32'
    elif fault=='future_mask':raw['representation']['known_future_masks']['missing'][0][0][0]=True
    elif fault=='static_mask':raw['representation']['static_missing_mask'][0][0]=True
    elif fault=='release':raw['representation']['known_future_available_at'][0][0]=output.state.encoder.base.plan.folds[0].assessment_cutoff.isoformat()
    elif fault=='decoder_period':raw['decoder_periods'][0][0]=output.encoder_periods[0][-1].isoformat()
    elif fault=='encoder_period':raw['encoder_periods'][0].reverse()
    elif fault=='length':raw['representation']['masks']['lengths'][0]=3
    elif fault=='horizon':raw['representation']['samples'][0][0]['horizon_step']=2
    elif fault=='future_evidence':raw['representation']['future_availability'][0]['evidence_identity']='8'*64
    elif fault=='context_evidence':raw['context_evidence'][0]='7'*64
    elif fault=='reordered_schema':raw['state']['request']['known_future_names']=['location']
    else:raw['known_future_values'][0][0][0]=True if fault=='boolean' else float('inf')
    with pytest.raises(EvaluationDeclarationError):TransformerOutput.model_validate_json(json.dumps(raw))


def test_training_only_fit_and_frozen_reload_without_labels_or_refitting(bundle,monkeypatch):
    b=bundle();source=b['tabular'].prepare(b['plan'],b['configuration']);req=request(b)
    state=fit_transformer_state(source.outputs[0].state,source.outputs,req)
    class Forbidden:
        def __iter__(self):raise AssertionError('held-out data touched during fit')
    poisoned=tuple(o if o.representation.binding.partition=='training' else o.model_copy(update={'values':Forbidden()}) for o in source.outputs)
    assert fit_transformer_state(state.encoder.base,poisoned,req).canonical_bytes==state.canonical_bytes
    original=FrozenTransformerPreprocessor(state).transform(source.outputs)
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',Mock(side_effect=AssertionError('no category refit')))
    import src.preprocessing.canonical_transformer as module
    monkeypatch.setattr(module,'fit_recurrent_state',Mock(side_effect=AssertionError('no scaler refit')))
    loaded=TransformerPreprocessingState.model_validate_json(state.canonical_json)
    assert [o.canonical_bytes for o in FrozenTransformerPreprocessor(loaded).transform(source.outputs)]==[o.canonical_bytes for o in original]


def test_series_organization_isolation_stride_and_future_values(bundle):
    b=bundle(organizations=(2,3));r=prepare(b,scaling='identity_v1',origins=(b['at'](7),),
        window=dict(lookback=3,window=2,stride=2,past_offsets=(-3,-1),padding='left'))
    assert len(r.outputs)==2
    for o in r.outputs:
        assert len({g[0].organization_id for g in o.representation.samples})==1
        assert o.encoder_values[1][0][0]-o.encoder_values[0][0][0]==100.
        assert o.encoder_periods[0]==(b['at'](4),b['at'](6))
    def future(raw):raw.loc[raw['from']>=b['at'](6),'reported']=999999.
    other=bundle(raw_change=future);base=bundle()
    a=prepare(base,scaling='identity_v1');c=prepare(other,scaling='identity_v1')
    assert [o.encoder_values for o in a.outputs[:2]]==[o.encoder_values for o in c.outputs[:2]]


def test_strict_float32_after_scaling(bundle):
    b=bundle()
    assert all(o.encoder_tensor.dtype==np.float32 for o in prepare(b,scaling='identity_v1',tensor_dtype='float32').outputs)
    with pytest.raises(EvaluationDeclarationError):prepare(b,tensor_dtype='float32')


def test_admission_authorization_and_sanitized_diagnostics(bundle,monkeypatch,capsys,caplog):
    b=bundle();secret='M5_SECRET_894731'
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        with pytest.raises(EvaluationDeclarationError) as exc:TransformerPreparationRequest.model_validate_json('{"'+secret+'":"'+secret+'"}')
        warnings.warn('unrelated M5 warning',UserWarning)
    e=exc.value;captured=capsys.readouterr()
    assert secret not in str(e)+repr(e)+repr(e.args)+''.join(traceback.format_exception(e))+captured.err+caplog.text
    assert e.__context__ is None and e.__cause__ is None and len(seen)==1
    req=request(b);p=b['plan'].model_copy(update={'budgets':b['plan'].budgets.model_copy(update={'max_output_cells':1})})
    spy=Mock(side_effect=AssertionError('admit before capture'));monkeypatch.setattr(b['tabular'],'prepare',spy)
    with pytest.raises(EvaluationDeclarationError):b['transformer'].prepare(p,b['configuration'],req)
    spy.assert_not_called()


def test_framework_free_imports():
    import subprocess,sys,os
    code='''
import sys,importlib.abc
class Deny(importlib.abc.MetaPathFinder):
 def find_spec(self,name,path=None,target=None):
  if name.split('.')[0] in ('torch','tensorflow','xgboost','lightgbm','mlflow','sqlalchemy'):raise AssertionError(name)
sys.meta_path.insert(0,Deny())
import src.services.canonical_transformer_preparation_service
'''
    run=subprocess.run([sys.executable,'-c',code],env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH='.'),text=True,capture_output=True,timeout=30)
    assert run.returncode==0,run.stderr


def test_numeric_decoder_train_only_standardization_and_parity(bundle):
    b=bundle(numeric=True)
    source=b['tabular'].prepare(b['plan'],b['configuration']);req=request(b)
    state=fit_transformer_state(source.outputs[0].state,source.outputs,req)
    assert state.context.centers==(0.,1.) and state.context.scales==(1.,1.)
    results=FrozenTransformerPreprocessor(state).transform(source.outputs)
    assert results[0].known_future_values==(((1.,),),((1.,),)) # origin 5 has published offer 2
    assert results[1].known_future_values==(((-1.,),),((-1.,),))
    altered=[]
    for output in source.outputs:
        raw=output.model_dump()
        if output.representation.binding.partition!='training':raw['values']=tuple((*row[:-1],999.) for row in output.values)
        altered.append(TabularOutput.model_validate(raw))
    assert fit_transformer_state(state.encoder.base,tuple(altered),req).canonical_bytes==state.canonical_bytes
    reloaded=TransformerPreprocessingState.model_validate_json(state.canonical_json)
    assert [o.output_id for o in FrozenTransformerPreprocessor(reloaded).transform(source.outputs)]==[o.output_id for o in results]


@pytest.mark.parametrize('allowed',[False,True])
def test_external_forecast_requires_original_authorization(bundle,allowed):
    b=bundle(rule='external_forecast');calls=[]
    b['service'].authorize_external=lambda *args:calls.append(args) or allowed
    if allowed:
        assert prepare(b).outputs[0].representation.future_availability[0].rule=='external_forecast'
    else:
        with pytest.raises(EvaluationDeclarationError):prepare(b)
    assert calls


def test_masked_encoder_gaps_and_all_padding_reject(bundle):
    b=bundle();source=b['tabular'].prepare(b['plan'],b['configuration'])
    state=fit_transformer_state(source.outputs[0].state,source.outputs,request(b,scaling='identity_v1',missing='mask',origins=(b['at'](7),)))
    subset=tuple(o for o in source.outputs if o.representation.samples[0][0].forecast_origin!=b['at'](5))
    output=FrozenTransformerPreprocessor(state).transform(subset)[0]
    assert output.representation.masks.lengths==(3,3)
    assert output.representation.masks.padding==((False,False,False),)*2
    assert output.encoder_values[0][1]==(0.,) and output.representation.masks.missing[0][1]==(True,)
    assert output.representation.available_at[0][1] is None
    with pytest.raises(EvaluationDeclarationError):prepare(b,origins=(b['at'](4),))


@pytest.mark.parametrize('fault',['foreign','missing_origin','duplicate','schema','geometry'])
def test_frozen_inference_scope_and_state_substitutions(bundle,fault):
    b=bundle();source=b['tabular'].prepare(b['plan'],b['configuration'])
    state=fit_transformer_state(source.outputs[0].state,source.outputs,request(b))
    batches=source.outputs
    if fault=='foreign':
        other=bundle(organizations=(3,));batches=other['tabular'].prepare(other['plan'],other['configuration']).outputs
    elif fault=='missing_origin':batches=batches[:-1]
    elif fault=='duplicate':batches=batches+(batches[0],)
    else:
        raw=state.model_dump(mode='json')
        if fault=='schema':raw['context']['feature_schema']['features'].reverse()
        else:raw['request']['window']['past_offsets']=[-5,-3,-1]
        with pytest.raises(EvaluationDeclarationError):FrozenTransformerPreprocessor(TransformerPreprocessingState.model_validate_json(json.dumps(raw)))
        return
    with pytest.raises(EvaluationDeclarationError):FrozenTransformerPreprocessor(state).transform(batches)


@pytest.mark.parametrize('boundary',['before','after_capture','final','authorization'])
def test_cancellation_and_denial_publish_no_partial_result(bundle,monkeypatch,boundary):
    b=bundle();status={'cancelled':boundary=='before'}
    if boundary=='authorization':b['service'].authorize=lambda *_:False
    elif boundary=='after_capture':
        original=b['tabular'].prepare
        def capture(*a,**kw):
            result=original(*a,**kw);status['cancelled']=True;return result
        monkeypatch.setattr(b['tabular'],'prepare',capture)
    elif boundary=='final':
        original=FrozenTransformerPreprocessor.transform
        def transform(self,*a,**kw):
            result=original(self,*a,**kw);status['cancelled']=True;return result
        monkeypatch.setattr(FrozenTransformerPreprocessor,'transform',transform)
    with pytest.raises(EvaluationDeclarationError):b['transformer'].prepare(b['plan'],b['configuration'],request(b),cancelled=lambda:status['cancelled'])


def test_missing_availability_evidence_and_verification_reject(bundle):
    # Both missing source evidence and denial by the trusted availability verifier
    # fail before an output can be assembled. No forged True flag enters M5.
    b=bundle();b['service'].verify_availability=lambda *_:False
    with pytest.raises(EvaluationDeclarationError):prepare(b)
    with pytest.raises(EvaluationDeclarationError):
        prepare(bundle(raw_change=lambda raw:raw.__setitem__('promo_proof',None)))


@pytest.mark.parametrize('ingress',['python','json','constructor','duplicate_json'])
def test_supported_ingress_confidentiality(bundle,capsys,caplog,ingress):
    from uuid import uuid4
    b=bundle();secret='M5_'+uuid4().hex;payload=request(b).model_dump(mode='json')
    # Use typed Python data for Python/constructor ingress, wire data for JSON.
    if ingress in ('python','constructor'):payload=request(b).model_dump()
    payload['window'][secret]=secret
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        with pytest.raises(EvaluationDeclarationError) as exc:
            if ingress=='python':TransformerPreparationRequest.model_validate(payload)
            elif ingress=='constructor':TransformerPreparationRequest(**payload)
            elif ingress=='json':TransformerPreparationRequest.model_validate_json(json.dumps(payload))
            else:TransformerPreparationRequest.model_validate_json('{"'+secret+'":1,"'+secret+'":2}')
    e=exc.value;capture=capsys.readouterr()
    assert secret not in str(e)+repr(e)+repr(e.args)+''.join(traceback.format_exception(e))+capture.err+caplog.text
    assert not seen and e.__context__ is None and e.__cause__ is None


def test_standard_float32_exact_case_is_usable(bundle):
    def alternating(raw):raw['reported']=[float(i%2*2) for i in range(len(raw))]
    b=bundle(numeric=True,raw_change=alternating)
    result=prepare(b,tensor_dtype='float32')
    assert result.outputs[0].state.encoder.centers==(1.,)
    assert result.outputs[0].state.encoder.scales==(1.,)
    assert all(o.encoder_tensor.dtype==o.decoder_tensor.dtype==np.float32 for o in result.outputs)


@pytest.mark.parametrize('value',[0.5,10.,-99.])
def test_decoder_categorical_codes_must_match_frozen_vocabulary(bundle,value):
    output=prepare(bundle()).outputs[0];raw=output.model_dump(mode='json')
    raw['known_future_values'][0][0][0]=value
    with pytest.raises(EvaluationDeclarationError):TransformerOutput.model_validate_json(json.dumps(raw))
