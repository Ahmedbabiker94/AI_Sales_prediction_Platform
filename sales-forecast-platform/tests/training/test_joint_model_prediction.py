"""Genuine original D2 issuance, admitted isolated stores, independent history ledger."""
import copy
from contextlib import contextmanager
import dataclasses
import json
import logging
import runpy
import struct
import sys
import traceback
from uuid import uuid4
import warnings

import numpy as np
import pandas as pd
import pytest

from src.data.contracts.forecast_evaluation import BaselineDefinition
from src.data.contracts.model_candidate import CandidateBundle
from src.data.contracts.model_joint_prediction import JointH1Budgets,SealedJointH1Result
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
from src.services.canonical_model_prediction_service import _Owner,ModelPredictionError
from src.services.canonical_joint_model_prediction_service import (
    JointH1Use,SealedJointH1Session,_BaselineOwner,_JointExecution)
from src.services.canonical_training_preparation_service import CanonicalTrainingPreparationService
from src.services.canonical_xgboost_training_service import CanonicalXGBoostTrainingService

@pytest.fixture(scope='module')
def genuine(tmp_path_factory):
    ns=runpy.run_path('tests/training/test_canonical_model_prediction.py')
    g=ns['genuine'].__wrapped__(tmp_path_factory)
    return g,ns['make_prediction'].__wrapped__(g)

@pytest.fixture
def make(genuine):
    g,prepare=genuine
    def create(*,algorithm='last_observation_v1',**changes):
        b=prepare();calls=[];control={'joint':None,'baseline':None}
        def joint(ctx,stage):
            calls.append(('joint',stage,ctx.forecast_origin))
            return control['joint'](ctx,stage) if control['joint'] else True
        def baseline(ctx,stage):
            calls.append(('baseline',stage,ctx.forecast_origin))
            return control['baseline'](ctx,stage) if control['baseline'] else True
        definition=BaselineDefinition(algorithm=algorithm,lookback=4 if algorithm=='seasonal_naive_v1' else 1,
            season_length=4 if algorithm=='seasonal_naive_v1' else None,supported_horizon=1)
        args={**b['args'],'baseline':definition,'authorize_joint':joint,'authorize_baseline':baseline,**changes}
        op,executor,verifier=g['b']['owner'].bootstrap_joint_h1_prediction(g['bundle'],**args)
        return dict(b=b,op=op,executor=executor,verifier=verifier,control=control,calls=calls,args=args)
    return create


@pytest.mark.parametrize('entry',['eligibility','standalone','joint'])
@pytest.mark.parametrize('channel',['warnings','stderr','logging'])
@pytest.mark.parametrize('variant',['exact_unit','unit','unit_list','metric','currency'])
def test_nested_typed_measurement_rejection_has_no_diagnostic_disclosure(
        genuine,entry,channel,variant,monkeypatch,capsys,caplog):
    from src.data.contracts.model_evaluation import ModelEvaluationEligibility,EvaluationDeclarationError
    from src.services.canonical_joint_model_prediction_service import _JointOwner

    g,prepare=genuine;b=prepare()
    key,value=('C0SEC8297','C0VAL6731') if variant=='exact_unit' else ('K'+uuid4().hex[:9],'V'+uuid4().hex[:9])
    declaration=ModelEvaluationEligibility.model_validate_json(g['declaration'].model_dump_json())
    def malformed(obj,**changes):
        return type(obj).model_construct(**{**dict(obj),**changes})
    config=declaration.candidate.representation.configuration
    field='unit' if variant in ('exact_unit','unit','unit_list') else variant
    payload=[key,value] if variant=='unit_list' else {key:value}
    measurement=malformed(config.execution.measurement,**{field:payload})
    execution=malformed(config.execution,measurement=measurement)
    config=malformed(config,execution=execution)
    schema=malformed(declaration.candidate.representation,configuration=config)
    candidate=malformed(declaration.candidate,representation=schema)
    declaration=malformed(declaration,candidate=candidate)
    calls=[]
    def forbidden(*a,**kw):
        calls.append(True)
        raise AssertionError('malformed declaration reached execution or issuance')
    for owner,method in ((_Owner,'_load_native'),(_Owner,'_predict_native'),(_Owner,'issue'),
                         (_JointOwner,'issue'),(CanonicalFeaturePipeline,'fit_state')):
        monkeypatch.setattr(owner,method,forbidden)
    def operation():
        if entry=='eligibility':return ModelEvaluationEligibility.model_validate(declaration)
        args={**b['args'],'eligibility':declaration}
        if entry=='standalone':return g['b']['owner'].bootstrap_validation_prediction(g['bundle'],**args)
        return g['b']['owner'].bootstrap_joint_h1_prediction(g['bundle'],**args,
            baseline=BaselineDefinition(algorithm='last_observation_v1',lookback=1,supported_horizon=1),
            authorize_joint=lambda *a:True,authorize_baseline=lambda *a:True)
    capture_enabled=logging._warnings_showwarning is not None
    try:
        if channel=='logging':logging.captureWarnings(True)
        with warnings.catch_warnings(record=channel=='warnings') as emitted:
            warnings.simplefilter('always')
            if channel=='stderr':
                def stderr_warning(message,category,filename,lineno,file=None,line=None):
                    print(warnings.formatwarning(message,category,filename,lineno,line),file=file or sys.stderr,end='')
                warnings.showwarning=stderr_warning
            # Guarded rejection must detach even an ambient input-bearing error.
            try:raise RuntimeError(value)
            except RuntimeError:
                with pytest.raises(EvaluationDeclarationError if entry=='eligibility' else ModelPredictionError) as error:
                    operation()
            warning_text='' if emitted is None else '\n'.join(str(w.message) for w in emitted)
    finally:logging.captureWarnings(capture_enabled)
    output=capsys.readouterr();exc=error.value
    diagnostics=(str(exc)+repr(exc)+repr(exc.args)+''.join(traceback.format_exception(exc))
        +warning_text+output.out+output.err+caplog.text)
    assert key not in diagnostics and value not in diagnostics
    assert exc.__cause__ is None and exc.__context__ is None
    assert not calls and not warning_text and not output.err and not caplog.text
    expected='invalid non-authoritative evaluation declaration' if entry=='eligibility' else 'owned validation prediction unavailable or invalid'
    assert str(exc)==expected and exc.args==(expected,)
    with warnings.catch_warnings(record=True) as unrelated:
        warnings.simplefilter('always');warnings.warn('unrelated nested measurement diagnostic',UserWarning)
    assert len(unrelated)==1 and str(unrelated[0].message)=='unrelated nested measurement diagnostic'


@pytest.mark.parametrize('algorithm,expected',[('last_observation_v1',{'north':32.,'south':132.}),('seasonal_naive_v1',{'north':0.,'south':100.})])
def test_genuine_shared_native_and_baseline(genuine,make,monkeypatch,algorithm,expected):
    g,_=genuine;b=make(algorithm=algorithm);h=b['op'].issue();reads=[];features=[];native=[]
    labels_before=sum(e[0]=='label' for e in g['b']['trace'])
    provider=g['evaluation_provider'];read=provider.open_read;infer=CanonicalFeaturePipeline.prepare_inference;predict=_Owner._predict_native
    @contextmanager
    def counted(*a,**kw):
        reads.append('open')
        with read(*a,**kw) as session:yield session
        reads.append('closed')
    def inference(engine,history,requests,state,**kw):
        assert reads==['open','closed']
        assert state.model_dump_json()==g['bundle'].manifest.provenance.states[0].state_json
        assert (history.period_end<=kw['forecast_origin']).all() and (history.available_at<=kw['forecast_origin']).all()
        f=infer(engine,history,requests,state,**kw);features.append(f);return f
    def predicted(*a):
        out=predict(*a);native.append(out.copy());return out
    def forbidden(*a,**kw):pytest.fail('C0 must not fit or replay training rows')
    import xgboost as xgb
    monkeypatch.setattr(provider,'open_read',counted)
    monkeypatch.setattr(CanonicalFeaturePipeline,'prepare_inference',inference)
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',forbidden);monkeypatch.setattr(xgb,'train',forbidden)
    monkeypatch.setattr(g['b']['owner'],'verify_candidate',forbidden)
    monkeypatch.setattr(_Owner,'_predict_native',staticmethod(predicted))
    session=b['executor'].execute(h)
    assert type(session) is SealedJointH1Session and reads==['open','closed']
    monkeypatch.setattr(_Owner,'_load_native',staticmethod(forbidden));monkeypatch.setattr(_Owner,'_predict_native',staticmethod(forbidden))
    result=b['verifier'].verify(session)
    assert reads==['open','closed'] and len(native)==1
    assert tuple(features[0].columns)==('location','lag1','mean4')
    assert sorted(e.point.value for e in result.model.events)==[35.62840270996094,112.37159729003906]
    actual={dict(p.scope.dimensions)['location']:p.value for p in result.baseline_points}
    assert actual==expected
    assert [e.native_float32_hex for e in result.model.events]==[v.tobytes().hex() for v in native[0]]
    assert all(struct.pack('>d',e.point.value)==struct.pack('>d',float(v)) for e,v in zip(result.model.events,native[0]))
    assert result.history_ids==tuple(e.history_identity for e in result.model.events)
    assert result.status=='SEALED_NOT_SCORED'
    assert labels_before==sum(e[0]=='label' for e in g['b']['trace'])
    assert b['verifier'].verify(session).model_dump_json()==result.model_dump_json()
    with pytest.raises(ModelPredictionError):b['executor'].execute(h)
    with pytest.raises(ModelPredictionError):b['op'].issue()
    print('C0_NATIVE',algorithm,actual,[e.point.value for e in result.model.events],result.capture_id)

@pytest.mark.parametrize('kind',['reconstructed','foreign','copied_issuer','receipt','foreign_approval'])
def test_original_authority_required(genuine,make,monkeypatch,kind):
    g,_=genuine;b=make();owner=g['b']['owner'];bundle=g['bundle'];args=dict(b['args'])
    if kind=='reconstructed':bundle=CandidateBundle(bytes(bytearray(bundle.payload)))
    elif kind=='copied_issuer':owner=copy.copy(owner)
    elif kind=='foreign':owner=CanonicalXGBoostTrainingService(g['b']['provider'],**g['b']['options'])
    elif kind=='receipt':args['relationship_approval']=b['b']['bv'].verify(g['declaration'],b['b']['approval'])
    else:args['relationship_approval']=make()['b']['approval']
    monkeypatch.setattr(_Owner,'_load_native',lambda *a:pytest.fail('unauthorized native load'))
    with pytest.raises(ModelPredictionError):owner.bootstrap_joint_h1_prediction(bundle,**args)

@pytest.mark.parametrize('credential',[None,True,'matching-digest',JointH1Use(),SealedJointH1Session()])
def test_forged_credentials(make,credential):
    b=make();h=b['op'].issue()
    with pytest.raises(ModelPredictionError):b['executor'].execute(credential)
    with pytest.raises(ModelPredictionError):b['verifier'].verify(credential)
    session=b['executor'].execute(h);assert b['verifier'].verify(session).status=='SEALED_NOT_SCORED'

@pytest.mark.parametrize('phase',['ready','sealed'])
@pytest.mark.parametrize('kind',['b0','use','cancel','expiry','model_policy','joint_policy','baseline_policy'])
def test_fresh_revocation_and_terminal_states(make,phase,kind):
    b=make();h=b['op'].issue();credential=h if phase=='ready' else b['executor'].execute(h)
    if kind=='b0':b['b']['bo'].revoke(b['b']['approval'])
    elif kind=='use':b['op'].revoke(credential)
    elif kind=='cancel':b['op'].cancel(credential)
    elif kind=='expiry':b['b']['clock'][0]=60.
    elif kind=='model_policy':b['b']['control']['hook']=lambda *a:False
    elif kind=='joint_policy':b['control']['joint']=lambda *a:False
    else:b['control']['baseline']=lambda *a:False
    action=b['executor'].execute if phase=='ready' else b['verifier'].verify
    with pytest.raises(ModelPredictionError):action(credential)
    with pytest.raises(ModelPredictionError):action(credential)

@pytest.mark.parametrize('stage',['model_load','model_predict','verify','scoped_return'])
@pytest.mark.parametrize('kind',['b0','handle','deadline'])
def test_revocation_at_late_callbacks(make,stage,kind):
    b=make();h=b['op'].issue()
    def hook(ctx,action):
        if action==stage:
            if kind=='b0':b['b']['bo'].revoke(b['b']['approval'])
            elif kind=='handle':b['op'].revoke(h)
            else:b['b']['clock'][0]=60.
        return True
    b['control']['joint']=hook
    with pytest.raises(ModelPredictionError):b['executor'].execute(h)
    with pytest.raises(ModelPredictionError):b['executor'].execute(h)

@pytest.mark.parametrize('failure',['baseline','model','cleanup','second_capture','mutated_handoff'])
def test_no_partial_session(make,genuine,monkeypatch,failure):
    g,_=genuine;b=make();h=b['op'].issue()
    def fail(*a,**kw):raise RuntimeError('PRIVATE_'+uuid4().hex)
    if failure=='baseline':monkeypatch.setattr(_BaselineOwner,'execute',fail)
    elif failure=='model':monkeypatch.setattr(_Owner,'_predict_native',staticmethod(fail))
    elif failure=='cleanup':
        provider=g['evaluation_provider'];read=provider.open_read
        @contextmanager
        def failing(*a,**kw):
            with read(*a,**kw) as s:yield s
            fail()
        monkeypatch.setattr(provider,'open_read',failing)
        monkeypatch.setattr(_Owner,'_load_native',lambda *a:pytest.fail('unclosed capture loaded model'))
    elif failure=='second_capture':
        original=_JointExecution.capture
        def twice(self,*a):original(self,*a);return original(self,*a)
        monkeypatch.setattr(_JointExecution,'capture',twice)
    else:
        seal=_Owner._joint_seal
        def changed(self,shared,result):return seal(self,shared,type(result).model_validate_json(result.model_dump_json()))
        monkeypatch.setattr(_Owner,'_joint_seal',changed)
    with pytest.raises(ModelPredictionError):b['executor'].execute(h)
    with pytest.raises(ModelPredictionError):b['executor'].execute(h)

@pytest.mark.parametrize('mode',['extreme','dtype'])
def test_future_capture_values_and_dtypes(make,genuine,monkeypatch,mode):
    g,_=genuine;first=make();a=first['verifier'].verify(first['executor'].execute(first['op'].issue()))
    provider=g['evaluation_provider'];read=provider.open_read;o=g['declaration'].coverage.periods[0].forecast_origin;calls=[];native=_Owner._predict_native
    @contextmanager
    def future(*args,**kw):
        with read(*args,**kw) as session:
            batches=session.batches
            def stream():
                for f in batches():
                    f=f.copy()
                    if (f.period_end>o).all():f['value']=f.value.astype(object) if mode=='dtype' else -1e30
                    yield f
            session.batches=stream
            yield session
    monkeypatch.setattr(provider,'open_read',future)
    def predict(*args):calls.append(1);return native(*args)
    monkeypatch.setattr(_Owner,'_predict_native',staticmethod(predict))
    b=make();h=b['op'].issue()
    if mode=='dtype':
        with pytest.raises(ModelPredictionError):b['executor'].execute(h)
        assert not calls
    else:
        z=b['verifier'].verify(b['executor'].execute(h))
        assert [e.point for e in a.model.events]==[e.point for e in z.model.events]
        assert a.baseline_points==z.baseline_points

@pytest.mark.parametrize('entry',['bootstrap','issue','execute','verify','revoke','cancel'])
def test_guarded_diagnostics(make,genuine,entry,capsys,caplog):
    g,_=genuine;b=make();h=b['op'].issue();secret='C0_'+uuid4().hex
    actions={'bootstrap':lambda:g['b']['owner'].bootstrap_joint_h1_prediction(g['bundle'],**{secret:secret}),
        'issue':lambda:b['op'].issue(**{secret:secret}),'execute':lambda:b['executor'].execute(h,**{secret:secret}),
        'verify':lambda:b['verifier'].verify(h,**{secret:secret}),'revoke':lambda:b['op'].revoke(**{secret:secret}),
        'cancel':lambda:b['op'].cancel(**{secret:secret})}
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        try:raise ValueError(secret)
        except ValueError:
            with pytest.raises(ModelPredictionError) as e:actions[entry]()
    out=capsys.readouterr();error=e.value
    assert secret not in str(error)+repr(error)+repr(error.args)+''.join(traceback.format_exception(error))+repr(seen)+out.err+out.out+caplog.text
    assert error.__context__ is None and error.__cause__ is None

@pytest.mark.parametrize('limit',['max_snapshot_bytes','max_baseline_work','max_output_bytes'])
def test_admission_before_native_load(make,monkeypatch,limit):
    monkeypatch.setattr(_Owner,'_load_native',lambda *a:pytest.fail('budget denial performed model loading'))
    with pytest.raises(ModelPredictionError):
        b=make(joint_budgets=JointH1Budgets(**{limit:1}));b['executor'].execute(b['op'].issue())

@pytest.mark.parametrize('bad',[0,1,None,'false'])
def test_invalid_cancellation_values(make,bad):
    b=make();h=b['op'].issue()
    with pytest.raises(ModelPredictionError):b['executor'].execute(h,cancelled=lambda:bad)


def test_reentrant_requests_and_public_graph(make,monkeypatch):
    b=make();h=b['op'].issue();native=_Owner._predict_native
    def predict(*a):
        with pytest.raises(ModelPredictionError):b['executor'].execute(h)
        return native(*a)
    monkeypatch.setattr(_Owner,'_predict_native',staticmethod(predict))
    session=b['executor'].execute(h);result=b['verifier'].verify(session)
    data=result.model_dump();pending=[data];forbidden={'manifest','labels','state_json','matrix_float32_hex','predictions_float32_hex','artifact_base64','provenance_json'}
    while pending:
        o=pending.pop()
        assert not isinstance(o,(bytes,bytearray,np.ndarray,pd.DataFrame))
        if isinstance(o,dict):assert not forbidden.intersection(o);pending.extend(o.values())
        elif isinstance(o,(tuple,list)):pending.extend(o)
    data['baseline_points']=();assert len(result.baseline_points)==2
    for value in (session,h):
        with pytest.raises(TypeError):copy.copy(value)
    with pytest.raises(ModelPredictionError):b['verifier'].verify(SealedJointH1Result.model_validate_json(result.model_dump_json()))

@pytest.fixture(scope='module')
def multi(tmp_path_factory):
    from src.data.contracts.temporal_preparation import TemporalPreparationPlan
    from src.data.contracts.model_evaluation import declare_evaluation_eligibility
    ns=runpy.run_path('tests/training/test_canonical_model_prediction.py');g=ns['genuine'].__wrapped__(tmp_path_factory);b=g['b']
    data=b['plan'].model_dump();data['folds'][0].update(validation_origins=(b['at'](6),b['at'](7)),test_origins=(b['at'](8),),assessment_cutoff=b['at'](9))
    b['plan']=TemporalPreparationPlan.model_validate(data);b['bootstrap']()
    owner=CanonicalXGBoostTrainingService(b['provider'],**{**b['options'],'plan':b['plan'],'training_scope':b['scope']})
    bundle=owner.train();b['owner']=owner
    args=runpy.run_path('tests/data/contracts/test_model_evaluation.py')['_genuine_arguments'](bundle)
    args['relationship']=g['declaration'].relationship
    g['bundle']=bundle;g['declaration']=declare_evaluation_eligibility(bundle,**args)
    return g,ns['make_prediction'].__wrapped__(g)

@pytest.fixture
def make_multi(multi):return make.__wrapped__(multi)


def test_two_origins_exact_grid_and_baseline(make_multi,multi):
    g,_=multi;b=make_multi();session=b['executor'].execute(b['op'].issue());r=b['verifier'].verify(session)
    assert len(r.model.events)==len(r.baseline_points)==4
    assert [p.scope.forecast_origin for p in r.baseline_points]==[g['b']['at'](6)]*2+[g['b']['at'](7)]*2
    for p in r.baseline_points:
        delta=0 if dict(p.scope.dimensions)['location']=='north' else 100
        assert p.value==(32. if p.scope.forecast_origin==g['b']['at'](6) else -2.)+delta

@pytest.mark.parametrize('producer',['joint','model','baseline'])
def test_final_origin_scoped_denial_cannot_be_hidden(make_multi,multi,producer):
    g,_=multi;b=make_multi();h=b['op'].issue();last=g['b']['at'](7)
    if producer=='model':
        b['b']['control']['hook']=lambda ctx,stage:False if stage=='joint_scoped_return' and ctx.forecast_origin==last else True
    else:
        b['control'][producer]=lambda ctx,stage:False if stage=='scoped_return' and ctx.forecast_origin==last else True
    with pytest.raises(ModelPredictionError):b['executor'].execute(h)
    with pytest.raises(ModelPredictionError):b['executor'].execute(h)

@pytest.mark.parametrize('mode',['reconstructed','reordered','missing','duplicate','extra'])
def test_original_baseline_handoff_identity_and_grid(make,monkeypatch,mode):
    b=make();h=b['op'].issue();original=_BaselineOwner.execute
    def wrong(*a):
        points=original(*a)
        if mode=='reconstructed':return tuple(p for p in points)
        if mode=='reordered':return tuple(reversed(points))
        if mode=='missing':return points[:-1]
        if mode=='duplicate':return (points[0],points[0])
        return points+(points[0],)
    monkeypatch.setattr(_BaselineOwner,'execute',wrong)
    with pytest.raises(ModelPredictionError):b['executor'].execute(h)


def test_mutated_baseline_history_rejects_same_source_claim(make,monkeypatch):
    b=make();h=b['op'].issue();original=_JointExecution.history
    def poisoned(*a):
        frame=original(*a);frame.loc[frame.index[-1],'value']+=1
        return frame
    monkeypatch.setattr(_JointExecution,'history',poisoned)
    with pytest.raises(ModelPredictionError):b['executor'].execute(h)


def test_cancellation_during_native_prevents_baseline_and_session(make,monkeypatch):
    b=make();h=b['op'].issue();predict=_Owner._predict_native
    def cancelled(*a):
        values=predict(*a);b['op'].cancel(h);return values
    monkeypatch.setattr(_Owner,'_predict_native',staticmethod(cancelled))
    monkeypatch.setattr(_BaselineOwner,'execute',lambda *a:pytest.fail('cancelled native output reached baseline'))
    with pytest.raises(ModelPredictionError):b['executor'].execute(h)


def test_postseal_original_handoffs_cannot_be_modified(make,monkeypatch):
    b=make();session=b['executor'].execute(b['op'].issue());owner=getattr(b['verifier'],'_JointH1Verifier__owner')
    original=owner.model_result
    monkeypatch.setattr(owner,'model_result',type(original).model_validate_json(original.model_dump_json()))
    monkeypatch.setattr(_Owner,'_load_native',lambda *a:pytest.fail('postseal loaded native'))
    with pytest.raises(ModelPredictionError):b['verifier'].verify(session)


def test_snapshot_has_no_retained_mutable_producer_view(make):
    b=make();session=b['executor'].execute(b['op'].issue());owner=getattr(b['verifier'],'_JointH1Verifier__owner')
    assert owner.shared.frozen is None and owner.shared.definitions is None
    assert owner.shared.receipt.capture_id==b['verifier'].verify(session).capture_id
    with pytest.raises(ValueError):owner.shared.history(pd.Timestamp(owner.declaration.coverage.periods[0].forecast_origin))
    with pytest.raises(dataclasses.FrozenInstanceError):owner.shared.receipt.capture_id='f'*64

@pytest.mark.parametrize('field,value',[('max_snapshot_bytes',1024),('max_baseline_work',100)])
def test_observed_admission_before_loading(make,monkeypatch,field,value):
    b=make(joint_budgets=JointH1Budgets(**{field:value}));h=b['op'].issue()
    monkeypatch.setattr(_Owner,'_load_native',lambda *a:pytest.fail('observed budget exhausted after loading'))
    with pytest.raises(ModelPredictionError):b['executor'].execute(h)


def test_postseal_verification_budget_is_fail_closed(make):
    b=make(joint_budgets=JointH1Budgets(max_verifications=2));h=b['op'].issue();session=b['executor'].execute(h)
    b['verifier'].verify(session)
    with pytest.raises(ModelPredictionError):b['verifier'].verify(session)
    with pytest.raises(ModelPredictionError):b['verifier'].verify(session)
    with pytest.raises(ModelPredictionError):b['executor'].execute(h)

@pytest.mark.parametrize('field',['purpose','publication','tenant','h2','training_origin','test_origin'])
def test_untrusted_eligibility_cannot_widen_scope(make,genuine,field):
    from src.data.contracts.model_evaluation import ModelEvaluationEligibility
    g,_=genuine;data=g['declaration'].model_dump(mode='json')
    if field=='purpose':data['relationship']['evaluation_purpose']='production_training'
    elif field=='publication':
        data['relationship']['evaluation']['reference']['version_id']='f'*64
        data['relationship']['evaluation']['manifest_digest']='f'*64
    elif field=='tenant':data['relationship']['evaluation']['reference']['organization_id']=str(uuid4())
    elif field=='h2':data['coverage']['periods'][0]['horizon_step']=2
    else:
        fold=g['declaration'].candidate.training_plan.folds[0]
        data['coverage']['periods'][0]['forecast_origin']=(fold.training_origins if field=='training_origin' else fold.test_origins)[0].isoformat()
    with pytest.raises(ModelPredictionError):make(eligibility=ModelEvaluationEligibility.model_construct(**data))

@pytest.mark.parametrize('which',['training','evaluation'])
def test_postseal_publication_access_is_fresh(genuine,make,monkeypatch,which):
    g,_=genuine;b=make();session=b['executor'].execute(b['op'].issue())
    provider=g['b']['provider'] if which=='training' else g['evaluation_provider']
    monkeypatch.setattr(provider,'authorize_read',lambda *a:False)
    monkeypatch.setattr(_Owner,'_load_native',lambda *a:pytest.fail('publication failure reloaded native'))
    with pytest.raises(ModelPredictionError):b['verifier'].verify(session)


def test_postseal_mutated_original_bundle_is_not_accepted(genuine,make,monkeypatch):
    g,_=genuine;b=make();session=b['executor'].execute(b['op'].issue());bundle=g['bundle'];original=bundle.payload
    object.__setattr__(bundle,'payload',original+b' ')
    try:
        monkeypatch.setattr(_Owner,'_load_native',lambda *a:pytest.fail('changed owner artifact reloaded'))
        with pytest.raises(ModelPredictionError):b['verifier'].verify(session)
    finally:object.__setattr__(bundle,'payload',original)
