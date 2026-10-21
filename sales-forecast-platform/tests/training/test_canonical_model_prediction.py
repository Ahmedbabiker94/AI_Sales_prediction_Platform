"""D3-B: genuine bounded native fixture, independent release/value ledger."""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import runpy
import struct
import traceback
from uuid import uuid4
import warnings

import numpy as np
import pandas as pd
import pytest

from src.data.contracts.dataset import StagingBudgets
from src.data.contracts.model_candidate import CandidateBundle,strict_float32
from src.data.contracts.model_evaluation import ModelEvaluationEligibility
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
from src.services.canonical_training_preparation_service import CanonicalTrainingPreparationService,ValueAttestedAvailabilityVerifier
from src.services.canonical_model_prediction_service import ModelPredictionError,EvaluationUse,_Owner
from src.services.training_evaluation_relationship_authority import bootstrap_relationship_authority


@pytest.fixture(scope='module')
def genuine(tmp_path_factory):
    return runpy.run_path('tests/training/test_training_evaluation_relationship_authority.py')['genuine'].__wrapped__(tmp_path_factory)


@pytest.fixture
def make_prediction(genuine):
    g=genuine;d=g['declaration'];source=g['b']['raws'][2];ledger={}
    with g['evaluation_provider'].open_read(g['evaluation'].reference,purpose='offline_forecast_evaluation') as session:
        for frame in session.batches():
            for row in frame.to_dict('records'):
                raw=source.iloc[int(row['source_record_id'])]
                assert row['value']==raw['reported']
                ledger[row['source_record_id']]=(row['series_id'],row['lineage_id'],raw['from'],raw['to'],raw['released'],raw['proof'],('float64',struct.pack('>d',float(raw['reported'])).hex()))
    def make(**changes):
        clock=[0.];control=dict(mode=None,hook=None,history_hook=None,stage=None,cancel=False);events=[]
        bo,bv=bootstrap_relationship_authority(g['b']['provider'],g['evaluation_provider'],
            authority_id=d.relationship.authority_id,authority_version=d.relationship.authority_version,
            issuance_context='d3b-independent-operator',elapsed_clock=lambda:clock[0])
        approval=bo.approve(changes.get('eligibility',d),training_metadata=g['training'],evaluation_metadata=g['evaluation'],approval_reference='decision-17')
        def authorize(ctx,stage):
            events.append(('evaluation',stage,ctx.forecast_origin));control['stage']=stage
            if control['hook'] and control['hook'](ctx,stage) is False:return False
            return not (control['mode']=='deny' and stage in ('predict','return'))
        def release(ctx,claims):
            events.append(('release',ctx.forecast_origin,len(claims)))
            if control['history_hook']:control['history_hook'](ctx,claims)
            if control['mode']=='deny_history':return False
            assert ctx.metadata==g['evaluation'] and ctx.metadata.purpose=='offline_forecast_evaluation'
            assert ctx.plan_digest==d.candidate.training_plan_digest and ctx.fold_id==d.coverage.fold_id
            assert ctx.forecast_origin in tuple(p.forecast_origin for p in d.coverage.periods)
            assert ctx.target_period_end==ctx.forecast_origin+pd.DateOffset(days=7)
            for c in claims:
                row=ledger[c.source_record_id]
                if (c.series_id,c.lineage_id,c.period_start,c.period_end,c.available_at,c.evidence_reference)!=row[:6]:return False
                if c.period_end>ctx.forecast_origin or c.available_at>ctx.forecast_origin:return False
            return True
        def values(ctx,claims):
            events.append(('values',ctx.forecast_origin,len(claims)))
            for c in claims:
                a=c.availability;row=ledger[a.source_record_id];m=g['evaluation'];mapping=m.mapping
                if (a.value_identity!=row[6] or c.dataset_reference!=m.reference or c.manifest_digest!=m.manifest_digest
                        or c.admission_digest!=m.admission_digest or c.mapping_digest!=hashlib.sha256(m.mapping_json.encode()).hexdigest()
                        or c.source_id!=m.source_id or c.mapping_id!=mapping.mapping_id or c.mapping_version!=mapping.version
                        or c.purpose!='offline_forecast_evaluation' or c.forecast_origin!=ctx.forecast_origin
                        or c.target_period_end!=ctx.target_period_end):return False
            return True
        args=dict(eligibility=d,provider=g['evaluation_provider'],relationship_verifier=bv,
            relationship_approval=approval,authorize_evaluation=authorize,
            verify_history=ValueAttestedAvailabilityVerifier(verify_availability=release,verify_captured_values=values),elapsed_clock=lambda:clock[0])
        args.update(changes)
        op,predictor=g['b']['owner'].bootstrap_validation_prediction(g['bundle'],**args)
        return dict(op=op,predictor=predictor,clock=clock,control=control,events=events,bo=bo,bv=bv,approval=approval,args=args)
    return make


def test_genuine_frozen_h1_native_and_roundtrip(genuine,make_prediction,monkeypatch):
    g=genuine;b=make_prediction();handle=b['op'].issue();features=[];loads=[];labels_before=sum(e[0]=='label' for e in g['b']['trace'])
    original=CanonicalFeaturePipeline.prepare_inference;native_load=_Owner._load_native
    def execute(engine,history,requests,state,**kw):
        assert state.model_dump_json()==g['bundle'].manifest.provenance.states[0].state_json
        assert all(history.period_end<=kw['forecast_origin']) and all(history.available_at<=kw['forecast_origin'])
        with g['evaluation_store']._publication_session(StagingBudgets()):pass
        result=original(engine,history,requests,state,**kw);features.append(result.copy());return result
    def load(*a):
        with g['evaluation_store']._publication_session(StagingBudgets()):pass
        loads.append(1);return native_load(*a)
    def forbidden(*a,**kw):pytest.fail('D3-B must not fit or replay training predictions')
    monkeypatch.setattr(CanonicalFeaturePipeline,'prepare_inference',execute)
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',forbidden)
    monkeypatch.setattr(g['b']['owner'],'verify_candidate',forbidden)
    monkeypatch.setattr(_Owner,'_load_native',staticmethod(load))
    import xgboost as xgb
    monkeypatch.setattr(xgb,'train',forbidden)
    result=b['predictor'].predict(handle)
    assert loads==[1] and len(features)==1 and len(result.events)==2
    assert tuple(features[0].columns)==('location','lag1','mean4')
    for (_,sid,_),row in features[0].iterrows():
        series=next(s for s in g['evaluation'].series if s.series_id==sid);delta=0 if series.dimensions['location']=='north' else 100
        assert row.lag1==32+delta and row.mean4==14+delta
    matrix=strict_float32(features[0].to_numpy())
    model=native_load(g['bundle'].validate()[1],tuple(features[0].columns),tuple(g['bundle'].manifest.native_binding.items()),8)
    expected=_Owner._predict_native(model,matrix,tuple(features[0].columns))
    assert [struct.pack('<f',e.point.value).hex() for e in result.events]==[v.tobytes().hex() for v in expected]
    assert labels_before==sum(e[0]=='label' for e in g['b']['trace'])
    assert result.implementation_kind=='fitted_candidate' and result.status=='PREDICTED_NOT_SCORED'
    assert result.fitted_state_digest==g['declaration'].candidate.fitted_state_digest
    assert result.model_validate_json(result.model_dump_json())==result
    assert b['events'][-3:]==[('release',g['declaration'].coverage.periods[0].forecast_origin,12),('values',g['declaration'].coverage.periods[0].forecast_origin,12),('evaluation','scoped_return',g['declaration'].coverage.periods[0].forecast_origin)]
    with pytest.raises(ModelPredictionError):b['predictor'].predict(handle)


def test_future_actuals_do_not_enter_features_or_predictions(genuine,make_prediction,monkeypatch):
    first=make_prediction();before=first['predictor'].predict(first['op'].issue())
    original=CanonicalTrainingPreparationService._capture;origin=genuine['declaration'].coverage.periods[0].forecast_origin
    def poisoned(*a,**kw):
        meta,ctx,frame,defs=original(*a,**kw);frame.loc[frame.period_start>=origin,'value']=999999999999.
        return meta,ctx,frame,defs
    monkeypatch.setattr(CanonicalTrainingPreparationService,'_capture',poisoned)
    second=make_prediction();after=second['predictor'].predict(second['op'].issue())
    assert [e.point for e in before.events]==[e.point for e in after.events]
    assert [e.history_identity for e in before.events]==[e.history_identity for e in after.events]


@pytest.mark.parametrize('claim',['clone','foreign','forged'])
def test_candidate_ownership_before_native(genuine,make_prediction,monkeypatch,claim):
    b=make_prediction();g=genuine
    monkeypatch.setattr(_Owner,'_load_native',lambda *_:pytest.fail('unauthorized load'))
    owner=g['b']['owner'];bundle=g['bundle']
    if claim=='clone':bundle=CandidateBundle(bytes(bytearray(bundle.payload)))
    elif claim=='foreign':
        from src.services.canonical_xgboost_training_service import CanonicalXGBoostTrainingService
        owner=CanonicalXGBoostTrainingService(g['b']['provider'],**g['b']['options'])
    else:bundle=type('Forged',(),{'bundle_id':bundle.bundle_id})()
    with pytest.raises(ModelPredictionError):owner.bootstrap_validation_prediction(bundle,**b['args'])


@pytest.mark.parametrize('claim',['new','receipt','foreign','revoked','expired'])
def test_b0_not_a_model_credential(genuine,make_prediction,monkeypatch,claim):
    b=make_prediction();args=dict(b['args']);g=genuine
    if claim=='new':args['relationship_approval']=object()
    elif claim=='receipt':args['relationship_approval']=b['bv'].verify(g['declaration'],b['approval'])
    elif claim=='foreign':args['relationship_approval']=make_prediction()['approval']
    elif claim=='revoked':b['bo'].revoke(b['approval'])
    else:b['clock'][0]=300.
    monkeypatch.setattr(_Owner,'_load_native',lambda *_:pytest.fail('unauthorized load'))
    with pytest.raises(ModelPredictionError):g['b']['owner'].bootstrap_validation_prediction(g['bundle'],**args)


@pytest.mark.parametrize('mode',['revoke','cancel','cancel-callback','deadline','deny','deny_history'])
def test_terminal_failures_and_no_reuse(make_prediction,mode):
    b=make_prediction();h=b['op'].issue()
    if mode in ('revoke','cancel'):getattr(b['op'],mode)(h)
    elif mode=='deadline':b['clock'][0]=60.
    elif mode in ('deny','deny_history'):b['control']['mode']=mode
    with pytest.raises(ModelPredictionError):b['predictor'].predict(h,cancelled=lambda:mode=='cancel-callback')
    with pytest.raises(ModelPredictionError):b['predictor'].predict(h)


@pytest.mark.parametrize('stage',['capture','load','origin','predict','return'])
@pytest.mark.parametrize('revocation',['handle','b0','cancel','deadline'])
def test_revocation_at_each_boundary(make_prediction,stage,revocation):
    b=make_prediction();h=b['op'].issue()
    def hook(ctx,action):
        if action!=stage:return
        if revocation=='handle':b['op'].revoke(h)
        elif revocation=='b0':b['bo'].revoke(b['approval'])
        elif revocation=='cancel':b['control']['cancel']=True
        else:b['clock'][0]=60.
    b['control']['hook']=hook
    with pytest.raises(ModelPredictionError):b['predictor'].predict(h,cancelled=lambda:b['control']['cancel'])
    with pytest.raises(ModelPredictionError):b['predictor'].predict(h)


@pytest.mark.parametrize('which',['b0','handle'])
def test_terminal_history_callback_revocation_is_caught(make_prediction,which):
    b=make_prediction();h=b['op'].issue()
    def hook(*_):
        if b['control']['stage']=='return':
            (b['bo'].revoke(b['approval']) if which=='b0' else b['op'].revoke(h))
    b['control']['history_hook']=hook
    with pytest.raises(ModelPredictionError):b['predictor'].predict(h)


@pytest.mark.parametrize('value',[np.nan,np.inf,-np.inf,1e9])
def test_native_nonfinite_or_out_of_range(make_prediction,monkeypatch,value):
    b=make_prediction();h=b['op'].issue()
    monkeypatch.setattr(_Owner,'_predict_native',staticmethod(lambda *a:np.full(2,value,dtype='float32')))
    with pytest.raises(ModelPredictionError):b['predictor'].predict(h)


@pytest.mark.parametrize('value',[np.array([1.,2.],dtype='float64'),np.array([[1.,2.]],dtype='float32'),np.array([True,False]),[1.,2.]])
def test_native_wrong_dtype_shape_and_type(make_prediction,monkeypatch,value):
    b=make_prediction();h=b['op'].issue();monkeypatch.setattr(_Owner,'_predict_native',staticmethod(lambda *a:value))
    with pytest.raises(ModelPredictionError):b['predictor'].predict(h)


def test_owned_signed_zero_widening(make_prediction,monkeypatch):
    b=make_prediction();h=b['op'].issue();monkeypatch.setattr(_Owner,'_predict_native',staticmethod(lambda *a:np.array([-0.,0.],dtype='float32')))
    result=b['predictor'].predict(h)
    assert [e.native_float32_hex for e in result.events]==['00000080','00000000']
    assert np.signbit(result.events[0].point.value) and not np.signbit(result.events[1].point.value)


@pytest.mark.parametrize('field',['fitted_state_id','configuration_digest','artifact_digest','candidate_bundle_id'])
def test_eligibility_state_substitution(genuine,make_prediction,field):
    b=make_prediction();data=genuine['declaration'].model_dump();data['candidate'][field]='f'*64
    with pytest.raises(ModelPredictionError):genuine['b']['owner'].bootstrap_validation_prediction(genuine['bundle'],
        **{**b['args'],'eligibility':ModelEvaluationEligibility.model_construct(**data)})


@pytest.mark.parametrize('field,value',[('horizon_step',2),('horizon_step',3),('forecast_origin','2020-02-03T00:00:00Z'),('forecast_origin','2020-02-24T00:00:00Z'),('target_period','2020-02-24T00:00:00Z')])
def test_wrong_temporal_scope(genuine,make_prediction,field,value):
    b=make_prediction();data=genuine['declaration'].model_dump(mode='json');data['coverage']['periods'][0][field]=value
    with pytest.raises(ModelPredictionError):genuine['b']['owner'].bootstrap_validation_prediction(genuine['bundle'],
        **{**b['args'],'eligibility':ModelEvaluationEligibility.model_construct(**data)})


@pytest.mark.parametrize('entry',['bootstrap','predict','issue','revoke','cancel'])
def test_supported_public_diagnostics(genuine,make_prediction,capsys,caplog,entry):
    b=make_prediction();h=b['op'].issue();secret='D3B_SECRET_'+uuid4().hex
    operations={'bootstrap':lambda:genuine['b']['owner'].bootstrap_validation_prediction(genuine['bundle'],**{secret:secret}),
        'predict':lambda:b['predictor'].predict(h,**{secret:secret}),'issue':lambda:b['op'].issue(**{secret:secret}),
        'revoke':lambda:b['op'].revoke(**{secret:secret}),'cancel':lambda:b['op'].cancel(**{secret:secret})}
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        try:raise ValueError(secret)
        except ValueError:
            with pytest.raises(ModelPredictionError) as error:operations[entry]()
    e=error.value;out=capsys.readouterr()
    assert secret not in str(e)+repr(e)+repr(e.args)+''.join(traceback.format_exception(e))+out.err+out.out+caplog.text
    assert e.__context__ is None and e.__cause__ is None and not seen
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always');warnings.warn('unrelated d3b warning')
    assert len(seen)==1


def test_capture_budget_before_native(make_prediction,genuine,monkeypatch):
    wire=genuine['declaration'].model_dump();wire['resources']['max_captured_rows']=1
    small=ModelEvaluationEligibility.model_validate(wire);b=make_prediction(eligibility=small);h=b['op'].issue()
    monkeypatch.setattr(_Owner,'_load_native',lambda *_:pytest.fail('resource denial must precede native load'))
    with pytest.raises(ModelPredictionError):b['predictor'].predict(h)
    with pytest.raises(ModelPredictionError):b['predictor'].predict(h)


@pytest.mark.parametrize('value',[True,np.nan,np.inf,0.1,1_000_001.])
def test_owned_invalid_feature_values(make_prediction,monkeypatch,value):
    b=make_prediction();h=b['op'].issue();original=CanonicalFeaturePipeline.prepare_inference
    def changed(*a,**kw):
        frame=original(*a,**kw)
        if type(value) is bool:frame['lag1']=pd.Series([True]*len(frame),index=frame.index,dtype=bool)
        else:frame.iloc[0,1]=value
        return frame
    monkeypatch.setattr(CanonicalFeaturePipeline,'prepare_inference',changed)
    monkeypatch.setattr(_Owner,'_predict_native',lambda *_:pytest.fail('invalid features must never enter prediction'))
    with pytest.raises(ModelPredictionError):b['predictor'].predict(h)


def test_missing_history_and_bad_revision_reject(make_prediction,monkeypatch):
    b=make_prediction();h=b['op'].issue();original=CanonicalTrainingPreparationService._capture
    def changed(*a,**kw):
        meta,ctx,frame,defs=original(*a,**kw)
        frame=frame.drop(frame.index[2]);return meta,ctx,frame,defs
    monkeypatch.setattr(CanonicalTrainingPreparationService,'_capture',changed)
    monkeypatch.setattr(_Owner,'_predict_native',lambda *_:pytest.fail('incomplete history must not predict'))
    with pytest.raises(ModelPredictionError):b['predictor'].predict(h)


@pytest.mark.parametrize('field',['artifact','state','schema'])
def test_private_material_corruption_fails_closed(make_prediction,monkeypatch,field):
    b=make_prediction();h=b['op'].issue();owner=getattr(b['predictor'],'_ValidationH1Predictor__owner')
    if field=='artifact':monkeypatch.setattr(owner,'artifact',b'corrupt ubjson')
    elif field=='state':monkeypatch.setattr(owner,'state',owner.state.model_copy(update={'training_partition_reference':'f'*64}))
    else:monkeypatch.setattr(owner,'schema',owner.schema.model_copy(update={'forecast_horizon':2}))
    monkeypatch.setattr(_Owner,'_load_native',lambda *_:pytest.fail('corrupt private material must reject before loading'))
    with pytest.raises(ModelPredictionError):b['predictor'].predict(h)


def test_opaque_handle_forgery_and_public_result_graph(make_prediction):
    import dataclasses
    from pydantic import BaseModel
    b=make_prediction();h=b['op'].issue()
    for fake in (EvaluationUse(),object(),True,{'use_id':'matching-looking'},'f'*64):
        with pytest.raises(ModelPredictionError):b['predictor'].predict(fake)
    result=b['predictor'].predict(h);pending=[result];seen=set()
    forbidden={'manifest','labels','provenance_json','matrix_float32_hex','labels_float32_hex','predictions_float32_hex','state_json','artifact_base64','payload','booster'}
    while pending:
        obj=pending.pop()
        if id(obj) in seen:continue
        seen.add(id(obj))
        assert type(obj) not in (bytes,bytearray,memoryview,np.ndarray,pd.DataFrame)
        if isinstance(obj,BaseModel):
            assert not forbidden.intersection(type(obj).model_fields)
            pending.extend(getattr(obj,k) for k in type(obj).model_fields)
        elif isinstance(obj,dict):
            assert not forbidden.intersection(obj);pending.extend(obj.values())
        elif isinstance(obj,(tuple,list)):pending.extend(obj)
    detached=result.model_dump();detached['events']=()
    assert len(result.events)==2 and len(seen)>30


@pytest.mark.parametrize('source',['evaluation','history','provider'])
def test_callback_exceptions_are_detached(make_prediction,genuine,monkeypatch,capsys,caplog,source):
    b=make_prediction();h=b['op'].issue();secret='CALLBACK_'+uuid4().hex
    def fail(*a,**kw):raise RuntimeError(secret)
    if source=='evaluation':b['control']['hook']=fail
    elif source=='history':b['control']['history_hook']=fail
    else:monkeypatch.setattr(genuine['evaluation_provider'],'authorize_read',fail)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        with pytest.raises(ModelPredictionError) as error:b['predictor'].predict(h)
    e=error.value;out=capsys.readouterr()
    assert secret not in str(e)+repr(e)+repr(e.args)+''.join(traceback.format_exception(e))+out.err+out.out+caplog.text
    assert e.__context__ is None and e.__cause__ is None and not caught
    with pytest.raises(ModelPredictionError):b['predictor'].predict(h)


@pytest.mark.parametrize('which',['training','evaluation','owner'])
def test_fresh_owner_and_publication_denial_before_load(genuine,make_prediction,monkeypatch,which):
    b=make_prediction();h=b['op'].issue()
    if which=='owner':
        owner=genuine['b']['owner'];args=list(getattr(owner,'_CanonicalXGBoostTrainingService__bootstrap'))
        args[10]=lambda *_:False
        monkeypatch.setattr(owner,'_CanonicalXGBoostTrainingService__bootstrap',tuple(args))
    else:
        provider=genuine['b']['provider'] if which=='training' else genuine['evaluation_provider']
        monkeypatch.setattr(provider,'authorize_read',lambda *_:False)
    monkeypatch.setattr(_Owner,'_load_native',lambda *_:pytest.fail('denied request attempted model load'))
    with pytest.raises(ModelPredictionError):b['predictor'].predict(h)


@pytest.mark.parametrize('phase',['composition','execution'])
def test_original_object_content_is_privately_sealed(genuine,make_prediction,monkeypatch,phase):
    import base64
    from src.data.contracts.dataset import operational_json
    import src.services.canonical_model_prediction_service as module
    g=genuine;b=make_prediction();h=b['op'].issue();bundle=g['bundle'];original=bundle.payload
    data=json.loads(original);replacement=b'coherently replaced native bytes'
    data['artifact_base64']=base64.b64encode(replacement).decode()
    data['manifest']['artifact_size']=len(replacement)
    data['manifest']['artifact_digest']=hashlib.sha256(replacement).hexdigest()
    changed=operational_json(data).encode()
    CandidateBundle(changed).validate()  # integrity-valid metadata is not issuance
    object.__setattr__(bundle,'payload',changed)
    try:
        monkeypatch.setattr(_Owner,'_load_native',lambda *_:pytest.fail('mutated candidate performed native load'))
        if phase=='composition':
            monkeypatch.setattr(module,'_bootstrap_owned',lambda *a,**kw:pytest.fail('unsealed original object accepted'))
            with pytest.raises(ModelPredictionError):g['b']['owner'].bootstrap_validation_prediction(bundle,**b['args'])
        else:
            with pytest.raises(ModelPredictionError):b['predictor'].predict(h)
    finally:object.__setattr__(bundle,'payload',original)


def test_relationship_approval_alone_does_not_authorize_use(genuine,make_prediction,monkeypatch):
    b=make_prediction();monkeypatch.setattr(_Owner,'_load_native',lambda *_:pytest.fail('relationship approval loaded a model'))
    for authorization in (True,lambda *_:False):
        with pytest.raises(ModelPredictionError):
            genuine['b']['owner'].bootstrap_validation_prediction(genuine['bundle'],
                **{**b['args'],'authorize_evaluation':authorization})


def test_shallow_copied_issuer_is_not_the_original_live_owner(genuine,make_prediction,monkeypatch):
    import copy
    import src.services.canonical_model_prediction_service as module
    b=make_prediction();copied=copy.copy(genuine['b']['owner'])
    assert copied is not genuine['b']['owner']
    monkeypatch.setattr(module,'_bootstrap_owned',lambda *a,**kw:pytest.fail('copied issuer reached composition'))
    with pytest.raises(ModelPredictionError):copied.bootstrap_validation_prediction(genuine['bundle'],**b['args'])


def test_bootstrap_and_result_detach_nested_publication_references(genuine,make_prediction):
    b=make_prediction();private=getattr(b['predictor'],'_ValidationH1Predictor__owner')
    assert private.declaration is not genuine['declaration']
    assert private.declaration.relationship.evaluation.reference is not genuine['declaration'].relationship.evaluation.reference
    result=b['predictor'].predict(b['op'].issue())
    assert result.evaluation_publication.reference is not private.declaration.relationship.evaluation.reference
    original=result.model_dump_json()
    dumped=result.model_dump(mode='json');dumped['evaluation_publication']['reference']['dataset_id']='changed'
    assert result.model_dump_json()==original


def test_calculation_work_admission_precedes_native_loading(make_prediction,monkeypatch):
    import src.services.canonical_model_prediction_service as module
    budgets=module.FeatureBudgets
    monkeypatch.setattr(module,'FeatureBudgets',lambda **kwargs:budgets(max_calculation_cells=1,**kwargs))
    monkeypatch.setattr(_Owner,'_load_native',lambda *_:pytest.fail('calculation admission reached native load'))
    with pytest.raises(ModelPredictionError):make_prediction()


def test_global_model_permission_callback_cannot_substitute_final_origin_permission(make_prediction):
    b=make_prediction();h=b['op'].issue();revoked=set();scopes=[]
    def policy(ctx,stage):
        if stage=='return':
            from src.data.contracts.model_evaluation import ModelEvaluationEligibility
            d=ModelEvaluationEligibility.model_validate_json(ctx.eligibility_json)
            revoked.add(d.coverage.periods[0].forecast_origin)
        if stage=='scoped_return':
            scopes.append(ctx.forecast_origin);return ctx.forecast_origin not in revoked
    b['control']['hook']=policy
    with pytest.raises(ModelPredictionError):b['predictor'].predict(h)
    assert len(scopes)==1 and scopes[0] in revoked
    with pytest.raises(ModelPredictionError):b['predictor'].predict(h)
