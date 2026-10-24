"""M4 uses actual FINALIZED publications and canonical features, never models."""
import json
import math
import runpy
import traceback
import warnings
from dataclasses import replace
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from src.data.contracts.model_evaluation import EvaluationDeclarationError
from src.data.contracts.model_representation import require_compatible
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
from src.preprocessing.canonical_recurrent import (
    RecurrentPreparationRequest,RecurrentPreprocessingState,RecurrentOutput,
    FrozenRecurrentPreprocessor,fit_recurrent_state,
)
from src.preprocessing.canonical_tabular import TabularOutput
from src.services.canonical_tabular_preparation_service import CanonicalTabularPreparationService
from src.services.canonical_recurrent_preparation_service import CanonicalRecurrentPreparationService


@pytest.fixture
def bundle(tmp_path):
    factory=runpy.run_path('tests/training/test_temporal_training_preparation.py')['make_temporal'].__wrapped__(tmp_path)
    def make(**kwargs):
        b=factory(**kwargs)
        b['tabular']=CanonicalTabularPreparationService(b['service'])
        b['recurrent']=CanonicalRecurrentPreparationService(b['tabular'])
        return b
    return make


def request(b,**kw):
    data=dict(model_family='lstm',fold_id='fold-1',origins=tuple(b['at'](n) for n in (5,6,7)),
        feature_names=('location','lag1'),window=dict(lookback=3,window=3,stride=1,past_offsets=(-3,-2,-1),padding='left'))
    data.update(kw)
    return RecurrentPreparationRequest(**data)


def prepare(b,**kw):return b['recurrent'].prepare(b['plan'],b['configuration'],request(b,**kw))


@pytest.mark.parametrize('family',['lstm','gru'])
def test_provider_backed_window_values_masks_scaler_and_serialization(bundle,monkeypatch,family):
    b=bundle();fits=[];reads=[]
    original=CanonicalFeaturePipeline.fit_state;opened=b['provider'].open_read
    from contextlib import contextmanager
    @contextmanager
    def read(*a,**kw):
        reads.append(1)
        with opened(*a,**kw) as session:yield session
    monkeypatch.setattr(b['provider'],'open_read',read)
    def fit(self,frame,**kw):
        fits.append(frame.copy());assert frame.period_end.max()<=b['at'](4)
        return original(self,frame,**kw)
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',fit)
    result=prepare(b,model_family=family)
    assert len(reads)==len(fits)==1 and len(result.outputs)==3
    state=result.outputs[0].state
    # Training lag rows are north 8,16 and south 108,116; no held-out rows.
    assert state.centers==(0.,62.) and state.scales==(1.,math.sqrt(2516.))
    assert state.counts==(4,4) and state.fit_row_count==4
    assert all(o.model_family==family for o in result.outputs)
    first=result.outputs[0]
    assert first.representation.axes==('batch','time','features')
    assert first.tensor.shape==(2,3,2) and first.tensor.dtype==np.float64
    assert first.representation.masks.lengths==(1,1)
    assert first.representation.masks.padding==((True,True,False),)*2
    assert first.periods[0]==tuple(b['at'](n) for n in (2,3,4))
    assert first.values[0][-1][1]==(8.-62.)/math.sqrt(2516.)
    assert first.values[1][-1][1]==(108.-62.)/math.sqrt(2516.)
    for output,targets in zip(result.outputs,result.targets):
        assert len(targets)==len(output.values)
        loaded=RecurrentOutput.model_validate_json(output.canonical_json)
        assert loaded.output_id==output.output_id and loaded.values==output.values
        assert RecurrentPreprocessingState.model_validate_json(state.canonical_json).state_id==state.state_id
        assert output.representation.binding.scaler.state_digest==state.state_id
        assert require_compatible(first.representation,output.representation)==first.representation.compatibility_id
        copy=output.tensor;copy[:]=123;assert not np.array_equal(copy,output.tensor)


def test_stride_and_series_department_organization_isolation(bundle):
    def change(raw):raw.loc[raw.branch.eq('south'),'group']='food'
    b=bundle(organizations=(2,3),raw_change=change)
    result=prepare(b,scaling='identity_v1',origins=(b['at'](7),),
        window=dict(lookback=3,window=2,stride=2,past_offsets=(-3,-1),padding='left'))
    assert len(result.outputs)==2
    for output in result.outputs:
        assert output.periods[0]==(b['at'](4),b['at'](6))
        by_location={dict(g[0].dimensions)['location']:row for g,row in zip(output.representation.samples,output.values)}
        assert by_location['south'][0][1]-by_location['north'][0][1]==100
        assert by_location['south'][1][1]-by_location['north'][1][1]==100
        assert len({g[0].organization_id for g in output.representation.samples})==1
        assert set(dict(g[0].dimensions)['department'] for g in output.representation.samples)=={'toys','food'}


def test_train_only_fit_and_future_values_do_not_change_earlier_windows(bundle):
    b=bundle();source=b['tabular'].prepare(b['plan'],b['configuration'])
    req=request(b,scaling='identity_v1')
    state=fit_recurrent_state(source.outputs[0].state,source.outputs,req)
    changed=[]
    for o in source.outputs:
        raw=o.model_dump()
        if o.representation.binding.partition!='training':
            raw['values']=tuple((row[0],999999.,*row[2:]) for row in o.values)
        changed.append(TabularOutput.model_validate(raw))
    changed=tuple(changed)
    other=fit_recurrent_state(state.base,changed,req)
    assert other.canonical_bytes==state.canonical_bytes
    before=FrozenRecurrentPreprocessor(state).transform(source.outputs)
    after=FrozenRecurrentPreprocessor(state).transform(changed)
    assert before[0].values==after[0].values and before[1].values==after[1].values
    # At test O=7, the previously released validation-origin feature at 6 is
    # legitimately historical. The target label is never a sequence feature.
    assert before[2].values!=after[2].values


def test_frozen_inference_parity_and_zero_fit(bundle,monkeypatch):
    b=bundle(exo=True);source=b['tabular'].prepare(b['plan'],b['configuration'])
    req=request(b,feature_names=('location','lag1','promotion'),scaling='identity_v1')
    state=fit_recurrent_state(source.outputs[0].state,source.outputs,req)
    expected=FrozenRecurrentPreprocessor(state).transform(source.outputs)
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',Mock(side_effect=AssertionError('no category fit')))
    import src.preprocessing.canonical_recurrent as module
    monkeypatch.setattr(module,'fit_recurrent_state',Mock(side_effect=AssertionError('no scaler fit')))
    loaded=RecurrentPreprocessingState.model_validate_json(state.canonical_json)
    actual=FrozenRecurrentPreprocessor(loaded).transform(source.outputs)
    assert [o.canonical_bytes for o in actual]==[o.canonical_bytes for o in expected]
    assert actual[-1].values[0][-1][-1]==-1.0  # frozen unknown category, not recoded


@pytest.mark.parametrize('fault',['stride','offsets','all_padding','unknown_origin','family','policy','dtype'])
def test_geometry_and_unsupported_policies_reject(bundle,fault):
    b=bundle();kw={}
    if fault=='stride':kw['window']=dict(lookback=3,window=3,stride=2,past_offsets=(-3,-2,-1),padding='left')
    elif fault=='offsets':kw['window']=dict(lookback=3,window=3,stride=1,past_offsets=(-4,-2,-1),padding='left')
    elif fault=='all_padding':kw['origins']=(b['at'](4),)
    elif fault=='unknown_origin':kw['origins']=(b['at'](10),)
    elif fault=='family':kw['model_family']='xgboost'
    elif fault=='policy':kw['scaling']='minmax_v1'
    else:kw['tensor_dtype']='float16'
    with pytest.raises(EvaluationDeclarationError):prepare(b,**kw)


@pytest.mark.parametrize('fault',['scaler','schema','mask','shape','period','boolean','nonfinite','fill'])
def test_actual_tensor_binding_rejects_malformed_substitutions(bundle,fault):
    output=prepare(bundle(),scaling='identity_v1').outputs[0];raw=output.model_dump()
    if fault=='scaler':raw['representation']['binding']['scaler']['state_digest']='9'*64
    elif fault=='schema':raw['state']['scales']=(1.,2.)
    elif fault=='mask':raw['representation']['masks']['lengths']=(3,3)
    elif fault=='shape':raw['representation']['shape']=(2,2,2)
    elif fault=='period':raw['periods']=(tuple(reversed(raw['periods'][0])),raw['periods'][1])
    else:
        values=[[list(row) for row in batch] for batch in output.values]
        if fault=='fill':values[0][0][0]=1.0
        else:values[0][-1][1]=True if fault=='boolean' else float('inf')
        raw['values']=tuple(tuple(tuple(row) for row in batch) for batch in values)
    with pytest.raises(EvaluationDeclarationError):RecurrentOutput.model_validate(raw)


def test_float32_strictness_and_missing_feature_masks(bundle):
    b=bundle();result=prepare(b,scaling='identity_v1',tensor_dtype='float32')
    assert all(o.tensor.dtype==np.float32 for o in result.outputs)
    with pytest.raises(EvaluationDeclarationError):prepare(b,tensor_dtype='float32')
    source=b['tabular'].prepare(b['plan'],b['configuration'])
    # Explicit absence of origin 5 is an interior masked slot at O=7. It is
    # not relabeled as padding or shifted to another temporal position.
    subset=tuple(o for o in source.outputs if o.representation.samples[0][0].forecast_origin!=b['at'](5))
    state=fit_recurrent_state(source.outputs[0].state,source.outputs,request(b,scaling='identity_v1',missing='mask',origins=(b['at'](7),)))
    output=FrozenRecurrentPreprocessor(state).transform(subset)[0]
    assert output.representation.masks.padding==((False,False,False),)*2
    assert output.representation.masks.missing[0][1]==(True,True)
    assert output.representation.available_at[0][1] is None
    assert output.values[0][1]==(0.,0.)
    assert output.periods[0][1]==b['at'](5)


def test_right_padding_preserves_absolute_positions(bundle):
    b=bundle();source=b['tabular'].prepare(b['plan'],b['configuration'])
    state=fit_recurrent_state(source.outputs[0].state,source.outputs,request(b,scaling='identity_v1',origins=(b['at'](7),),
        window=dict(lookback=3,window=3,stride=1,past_offsets=(-3,-2,-1),padding='right')))
    subset=tuple(o for o in source.outputs if o.representation.samples[0][0].forecast_origin!=b['at'](6))
    output=FrozenRecurrentPreprocessor(state).transform(subset)[0]
    assert output.representation.masks.padding==((False,False,True),)*2
    assert output.representation.masks.lengths==(2,2)
    assert output.periods[0][-1]==b['at'](6) and output.values[0][-1]==(0.,0.)


def test_diagnostics_and_resource_admission(bundle,monkeypatch,capsys,caplog):
    b=bundle();secret='RECURRENT_SECRET_8291'
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        with pytest.raises(EvaluationDeclarationError) as exc:
            RecurrentPreparationRequest.model_validate_json('{"'+secret+'":true,"'+secret+'":false}')
    e=exc.value;captured=capsys.readouterr()
    assert secret not in str(e)+repr(e)+repr(e.args)+''.join(traceback.format_exception(e))+captured.err+caplog.text
    assert not seen and e.__context__ is None and e.__cause__ is None
    req=request(b,window=dict(lookback=512,window=512,stride=1,past_offsets=tuple(range(-512,0)),padding='left'))
    b['plan']=b['plan'].model_copy(update={'budgets':b['plan'].budgets.model_copy(update={'max_output_cells':1})})
    spy=Mock(side_effect=AssertionError('admit before capture'));monkeypatch.setattr(b['tabular'],'prepare',spy)
    with pytest.raises(EvaluationDeclarationError):b['recurrent'].prepare(b['plan'],b['configuration'],req)
    spy.assert_not_called()


@pytest.mark.parametrize('fault',['foreign','duplicate','missing_training','unavailable','missing_target'])
def test_frozen_source_scope_and_availability_reject(bundle,fault):
    b=bundle();source=b['tabular'].prepare(b['plan'],b['configuration'])
    state=fit_recurrent_state(source.outputs[0].state,source.outputs,request(b,scaling='identity_v1'))
    altered=list(source.outputs)
    if fault=='duplicate':altered.append(source.outputs[0])
    elif fault=='missing_training':
        with pytest.raises(EvaluationDeclarationError):fit_recurrent_state(state.base,source.outputs[1:],state.request)
        return
    elif fault=='missing_target':altered.pop()
    elif fault=='unavailable':
        bad=altered[0].model_dump();bad['representation']['available_at']=(b['at'](7),b['at'](7))
        with pytest.raises(EvaluationDeclarationError):TabularOutput.model_validate(bad)
        return
    else:
        other=bundle(organizations=(3,));altered=list(other['tabular'].prepare(other['plan'],other['configuration']).outputs)
    with pytest.raises(EvaluationDeclarationError):FrozenRecurrentPreprocessor(state).transform(tuple(altered))


@pytest.mark.parametrize('fault',['negative_scale','boolean_center','nan_center','counts','other_policy','no_scaler','release'])
def test_frozen_scaler_execution_binding_is_not_metadata_only(bundle,fault):
    output=prepare(bundle()).outputs[0];raw=output.model_dump()
    if fault=='negative_scale':raw['state']['scales']=(1.,-1.)
    elif fault=='boolean_center':raw['state']['centers']=(False,62.)
    elif fault=='nan_center':raw['state']['centers']=(0.,float('nan'))
    elif fault=='counts':raw['state']['counts']=(5,5)
    elif fault=='other_policy':raw['representation']['binding']['scaler']['policy']='identity_v1'
    elif fault=='no_scaler':raw['representation']['binding']['scaler']=None
    else:raw['representation']['available_at']=(tuple(b if b is None else pd.Timestamp(b)+pd.Timedelta(days=1) for b in row)
                                                for row in raw['representation']['available_at'])
    if fault=='release':raw['representation']['available_at']=tuple(raw['representation']['available_at'])
    with pytest.raises(EvaluationDeclarationError):RecurrentOutput.model_validate(raw)


def test_no_framework_or_operational_imports():
    import os,subprocess,sys
    script='''
import importlib.abc,sys
blocked=('torch','tensorflow','xgboost','lightgbm','mlflow','sqlalchemy','src.database','src.ml','src.deployment')
class Deny(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if any(fullname==p or fullname.startswith(p+'.') for p in blocked):raise AssertionError(fullname)
sys.meta_path.insert(0,Deny())
import src.preprocessing.canonical_recurrent
import src.services.canonical_recurrent_preparation_service
assert not any(n==p or n.startswith(p+'.') for n in sys.modules for p in blocked)
'''
    result=subprocess.run([sys.executable,'-c',script],env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH='.'),
        capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr


def test_denied_authority_zero_scaler_fit_and_no_partial_output(bundle,monkeypatch):
    b=bundle();b['service'].authorize=lambda *_:False
    import src.services.canonical_recurrent_preparation_service as service
    fit=Mock(side_effect=AssertionError('unauthorized scaler fit'))
    monkeypatch.setattr(service,'fit_recurrent_state',fit)
    with pytest.raises(EvaluationDeclarationError):prepare(b)
    fit.assert_not_called()


def test_zero_variance_and_signed_zero_are_explicit(bundle):
    b=bundle();source=b['tabular'].prepare(b['plan'],b['configuration'])
    constant=[]
    for output in source.outputs:
        raw=output.model_dump();raw['values']=tuple((row[0],-0.0,*row[2:]) for row in output.values)
        constant.append(TabularOutput.model_validate(raw))
    constant=tuple(constant)
    state=fit_recurrent_state(constant[0].state,constant,request(b))
    assert state.scales==(1.,1.)
    outputs=FrozenRecurrentPreprocessor(state).transform(constant)
    assert math.copysign(1.,outputs[0].values[0][-1][1])==-1.
    assert math.copysign(1.,outputs[0].tensor[0,-1,1])==-1.


def test_full_preparation_identity_binds_window_policy_and_family(bundle):
    b=bundle()
    first=prepare(b,scaling='identity_v1')
    repeated=prepare(b,scaling='identity_v1')
    other=prepare(b,scaling='standard_v1',model_family='gru')
    assert first.preparation_id==repeated.preparation_id
    assert first.preparation_id!=other.preparation_id
    assert first.source_preparation_id==other.source_preparation_id


def test_no_labels_are_read_by_fit_or_frozen_kernel(bundle,monkeypatch):
    b=bundle();source=b['tabular'].prepare(b['plan'],b['configuration'])
    # These functions receive only immutable feature outputs, never the target
    # collection. This trap would fail if source.targets were inspected here.
    class NoTargets:
        def __iter__(self):raise AssertionError('target labels accessed')
    guarded=replace(source,targets=NoTargets())
    state=fit_recurrent_state(guarded.outputs[0].state,guarded.outputs,request(b))
    assert FrozenRecurrentPreprocessor(state).transform(guarded.outputs)


def test_all_zero_or_missing_slots_are_not_genuine_observations(bundle):
    from src.data.contracts.features import FeatureConfiguration
    from src.data.contracts.temporal_preparation import TemporalPreparationPlan
    b=bundle();cfg=b['configuration'].model_dump()
    cfg['lag_offsets']=(*cfg['lag_offsets'],52)
    cfg['calculations']=(*cfg['calculations'],dict(output='unavailable',operation='lag',inputs=('value',),parameters={'offset':52}))
    cfg['ordered_outputs']=(*cfg['ordered_outputs'],'unavailable')
    cfg['output_dtypes']['unavailable']='float64';cfg['missing_value_behavior']['unavailable']='nan'
    b['configuration']=FeatureConfiguration.model_validate(cfg)
    p=b['plan'].model_dump();p['configuration_digest']=CanonicalFeaturePipeline(b['configuration']).digest
    b['plan']=TemporalPreparationPlan.model_validate(p)
    source=b['tabular'].prepare(b['plan'],b['configuration'])
    # No training observations for a feature means no invented scaler state.
    with pytest.raises(EvaluationDeclarationError):
        fit_recurrent_state(source.outputs[0].state,source.outputs,request(b,feature_names=('unavailable',),missing='mask'))
    # A partially missing feature is omitted from its own scaler fit, and remains
    # explicit missing data (not genuine zero) in the selected sequence.
    altered=[]
    for o in source.outputs:
        raw=o.model_dump()
        if o.representation.samples[0][0].forecast_origin==b['at'](5):
            raw['values']=tuple((*v[:-1],2.) for v in o.values)
            raw['representation']['missing_mask']=tuple((*m[:-1],False) for m in o.representation.missing_mask)
        altered.append(TabularOutput.model_validate(raw))
    altered=tuple(altered)
    state=fit_recurrent_state(altered[0].state,altered,request(b,feature_names=('unavailable',),missing='mask',scaling='identity_v1'))
    assert state.counts==(2,)
    outputs=FrozenRecurrentPreprocessor(state).transform(altered)
    assert outputs[0].representation.masks.missing[0][-1]==(True,)
    assert outputs[0].representation.available_at[0][-1] is None
    assert outputs[0].values[0][-1]==(0.,)


def test_scaler_fitter_never_visits_heldout_numeric_payloads(bundle):
    b=bundle();source=b['tabular'].prepare(b['plan'],b['configuration'])
    expected=fit_recurrent_state(source.outputs[0].state,source.outputs,request(b))
    class ForbiddenValues:
        def __iter__(self):raise AssertionError('held-out payload visited by fitter')
    poisoned=tuple(o if o.representation.binding.partition=='training' else o.model_copy(update={'values':ForbiddenValues()})
                   for o in source.outputs)
    fitted=fit_recurrent_state(expected.base,poisoned,expected.request)
    assert fitted.canonical_bytes==expected.canonical_bytes
    # These are not valid execution buffers; inference must reject rather than
    # ignore their malformed payloads or publish a partially valid sequence.
    with pytest.raises(EvaluationDeclarationError):FrozenRecurrentPreprocessor(fitted).transform(poisoned)


@pytest.mark.parametrize('fault',['horizon','target'])
def test_h1_target_geometry_cannot_be_reinterpreted(bundle,fault):
    output=prepare(bundle(),scaling='identity_v1').outputs[0]
    raw=output.model_dump();point=raw['representation']['samples'][0][0]
    if fault=='horizon':point['horizon_step']=2
    else:point['target_period_end']=point['target_period_end']+pd.Timedelta(days=7)
    with pytest.raises(EvaluationDeclarationError):RecurrentOutput.model_validate(raw)
