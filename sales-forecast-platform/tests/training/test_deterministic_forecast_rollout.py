"""Isolated real staging/admission; synthetic predictors only, no scoring/models."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import math
import runpy
import traceback
from uuid import UUID

import numpy as np
import pandas as pd
import pytest

from src.data.contracts.backtesting import (ForecastEvaluationPlan, PredictorCapabilities, PredictionBatch,
    PredictionPoint, PredictionEvent, ForecastScope, DeterministicRolloutResult)
from src.data.contracts.dataset import (DatasetAccessContext, StoredDatasetReference, ConsistencyPolicy,
    VerifiedConsistencyEvidence, ScopedWarningAuthorization, StagingBudgets)
from src.data.contracts.temporal_preparation import TemporalFold
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
from src.repositories.local_dataset_repository import LocalDatasetRepository
from src.providers.canonical_training_data_provider import CanonicalTrainingDataProvider
from src.services.dataset_ingestion_service import DatasetIngestionService
from src.services.dataset_finalization_service import DatasetFinalizationService
from src.services.deterministic_forecast_rollout_service import (DeterministicForecastRolloutService,
    ForecastRolloutError, representation_schema_id, numeric_policy_id)


@pytest.fixture
def make_rollout(tmp_path):
    temporal=runpy.run_path('tests/training/test_temporal_training_preparation.py')
    make=temporal['make_temporal'].__wrapped__(tmp_path/'training-reference')
    reference=runpy.run_path('tests/data/adapters/test_integration_foundation.py')
    def factory(**kwargs):
        old=make(**kwargs);cfg=old['configuration'];at=old['at'];oldmeta=old['metadata'];raws=old['raws']
        now=old['provider'].clock()
        store=LocalDatasetRepository(tmp_path/str(__import__('uuid').uuid4()))
        provider=CanonicalTrainingDataProvider(store,authorize_read=lambda *_:True,clock=lambda:now)
        calls=[]
        def predict(req):
            calls.append(req)
            return PredictionBatch(request_id=req.request_id,points=tuple(PredictionPoint(scope=s,value=[-2.,0.,6.][req.prefix_length-1]) for s in req.scopes))
        service=DeterministicForecastRolloutService(provider,predictor=predict,
            authorize_rollout=lambda *_:True,verify_availability=old['service'].verify_availability)
        bindings=[];metadata=[]
        for meta in oldmeta:
            org=meta.reference.organization_id;mapping=meta.mapping
            asset=reference['asset'].__wrapped__().model_copy(update={'organization_id':org,'timezone':cfg.execution.temporal.timezone})
            manifest=DatasetIngestionService(store,authorize_ingestion=lambda *_:True).ingest(
                reference['MemoryAdapter'](raws[org.int],asset),mapping,dataset_id=UUID(int=500+org.int),batch_size=3)
            ref=StoredDatasetReference(manifest.organization_id,manifest.dataset_id,manifest.attempt_id,manifest.version_id)
            scope=DatasetAccessContext(ref,manifest.source_id,manifest.ingestion_batch_id,mapping.mapping_id,mapping.version,
                manifest.mapping_digest,mapping.target_metric,mapping.business_scope,'opaque-source','connection-1','offline_forecast_evaluation')
            evidence=VerifiedConsistencyEvidence(scope,'snapshot','snapshot_external_v1','method-1','1.0',
                now-timedelta(days=2),now-timedelta(hours=1),now-timedelta(minutes=1),now+timedelta(days=1))
            warning=ScopedWarningAuthorization(scope,'NEGATIVE_VALUE','value','keep','synthetic-warning',now-timedelta(hours=1),now+timedelta(days=1))
            DatasetFinalizationService(store,authorize=lambda *_:True,verify_consistency=lambda *_:True,
                verify_policy=lambda *_:True,verify_warning=lambda *_:True,decision_context_reference='synthetic-decision',clock=lambda:now).finalize(
                ref,mapping,connection_reference='connection-1',purpose='offline_forecast_evaluation',
                consistency_policy=ConsistencyPolicy(scope,'SALES_GENERAL','1.0.0'),consistency_evidence=evidence,warning_authorizations=(warning,))
            with provider.open_read(ref,purpose='offline_forecast_evaluation') as session:
                checked=session.metadata;list(session.batches())
            bindings.append(service.preparation._temporal_binding(checked,tuple(s.series_id for s in checked.series)))
            metadata.append(checked)
        cap=PredictorCapabilities(predictor_id='synthetic-three-step',predictor_version='1.0',strategy='recursive_one_step',
            representation_kind='tabular_step',representation_schema_id=representation_schema_id(cfg),numeric_policy_id=numeric_policy_id(cfg),
            lookback=4,supported_horizon=3,allowed_covariates=tuple(sorted(r.field for r in cfg.execution.exogenous)))
        fold=TemporalFold(fold_id='fold-1',training_origins=(at(4),),validation_origins=(at(8),),calibration_start=at(0),
            categorical_cutoff=at(4),model_fit_cutoff=at(7),assessment_cutoff=at(11))
        plan=ForecastEvaluationPlan(plan_id='synthetic-evaluation',datasets=tuple(bindings),predictor=cap,
            configuration_id=cfg.configuration_id,configuration_version=cfg.version,configuration_digest=CanonicalFeaturePipeline(cfg).digest,
            frequency=cfg.execution.temporal.frequency.value,calendar='gregorian',timezone=cfg.execution.temporal.timezone,
            period_anchor='start',folds=(fold,),forecast_horizon=3)
        return dict(service=service,provider=provider,store=store,plan=plan,configuration=cfg,calls=calls,
                    metadata=metadata,raws=raws,at=at,old=old)
    return factory


def run(b,**kwargs):return b['service'].rollout(b['plan'],b['configuration'],**kwargs)


def plan_change(b,**kwargs):
    data=b['plan'].model_dump();data.update(kwargs);b['plan']=ForecastEvaluationPlan.model_validate(data)


def test_hand_calculated_recursion_fixed_origin_prefix_and_parent_lineage(make_rollout):
    b=make_rollout(decimal=True);result=run(b)
    assert len(result.events)==12
    north=next(s.series_id for s in b['metadata'][0].series if s.dimensions['location']=='north')
    refs=[(8.,1.5,5.),(-2.,.5,math.sqrt(83/3)),(0.,1.5,math.sqrt(59/3))]
    for k,(req,expected) in enumerate(zip(b['calls'][:3],refs),1):
        row=req.matrix.loc[(UUID(int=2),north,b['at'](k+3))]
        assert row[['lag1','mean4','std4']].tolist()==pytest.approx(expected)
        assert req.compatibility.forecast_origin==b['at'](4)
        assert req.compatibility.total_horizon==3 and req.prefix_length==k
        assert req.prefix_schema_id==req.compatibility.prefix_schema_ids[k-1]
        assert not {'value','label','actual'}&set(req.matrix)
    selected=[e for e in result.events if e.scope.series_id==north and e.scope.forecast_origin==b['at'](4)]
    assert [e.value for e in selected]==[-2.,0.,6.]
    assert [e.scope.target_period for e in selected]==[b['at'](4),b['at'](5),b['at'](6)]
    assert selected[2].parent_feedback_ids==(selected[0].event_id,selected[1].event_id)
    assert b['raws'][2].reported.iloc[1]==Decimal('-4.0000')
    before=result.events[0].event_id
    with pytest.raises(Exception):result.events[0].value=9.
    detached=b['calls'][0].matrix;detached.loc[:,'lag1']=999.
    assert result.events[0].event_id==before
    assert run(b).rollout_id==result.rollout_id


def test_extreme_future_actuals_do_not_change_origin_representations(make_rollout):
    a=make_rollout();run(a)
    def changed(raw):raw.loc[raw['from']>=pd.Timestamp('2020-02-03',tz='UTC'),'reported']=999999999.
    b=make_rollout(raw_change=changed);run(b)
    for left,right in zip(a['calls'][:3],b['calls'][:3]):
        pd.testing.assert_frame_equal(left.matrix,right.matrix)
    # Content-bound IDs MUST differ for different datasets, even when causal views agree.
    assert a['calls'][0].dataset_binding!=b['calls'][0].dataset_binding


@pytest.mark.parametrize('value',[float('nan'),float('inf'),-float('inf'),True,1,Decimal('1'),np.float32(1),'1',None,[1.]])
def test_invalid_points_reject_without_partial_output(make_rollout,value):
    b=make_rollout()
    def callback(req):
        return PredictionBatch.model_construct(request_id=req.request_id,points=tuple(
            PredictionPoint.model_construct(scope=s,value=value) for s in req.scopes))
    b['service'].predictor=callback
    with pytest.raises(ForecastRolloutError):run(b)


@pytest.mark.parametrize('fault',['missing','extra','duplicate','organization_id','series_id','fold_id','forecast_origin','horizon_step','target_period','request_id'])
def test_keyed_output_scope_and_shape_fail_closed(make_rollout,fault):
    b=make_rollout();original=b['service'].predictor
    def callback(req):
        output=original(req);points=list(output.points)
        if fault=='missing':points.pop()
        elif fault=='extra':points.append(points[0])
        elif fault=='duplicate':points[1]=points[0]
        elif fault=='request_id':return output.model_copy(update={'request_id':'0'*64})
        else:
            value={'organization_id':UUID(int=9),'series_id':UUID(int=9),'fold_id':'fold-other',
                'forecast_origin':b['at'](3),'horizon_step':2,'target_period':b['at'](5)}[fault]
            points[0]=points[0].model_copy(update={'scope':points[0].scope.model_copy(update={fault:value})})
        return output.model_copy(update={'points':tuple(points)})
    b['service'].predictor=callback
    with pytest.raises(ForecastRolloutError):run(b)


@pytest.mark.parametrize('fault',['direct','sequence','statistical','schema','numeric','predictor','configuration'])
def test_unsupported_capabilities_and_bindings_reject_before_read(make_rollout,fault):
    b=make_rollout();data=b['plan'].predictor.model_dump()
    if fault=='direct':data['strategy']='direct_multi_horizon'
    elif fault in ('sequence','statistical'):data['representation_kind']='temporal_sequence' if fault=='sequence' else 'statistical'
    elif fault in ('schema','numeric'):data['representation_schema_id' if fault=='schema' else 'numeric_policy_id']='0'*64
    elif fault=='predictor':data['version']='9.0'
    if fault=='predictor':
        with pytest.raises(Exception):PredictorCapabilities.model_validate(data)
        return
    plan_change(b,predictor=PredictorCapabilities.model_validate(data))
    if fault=='configuration':plan_change(b,configuration_digest='0'*64)
    reads=[];old=b['provider'].open_read
    @contextmanager
    def tracked(*a,**kw):reads.append(1);yield from ()
    b['provider'].open_read=tracked
    with pytest.raises(ForecastRolloutError):run(b)
    assert not reads and not b['calls']


def test_evaluation_and_training_publications_never_cross_authorize(make_rollout):
    b=make_rollout()
    for meta,purpose,provider in [(b['metadata'][0],'production_training',b['provider']),
            (b['old']['metadata'][0],'offline_forecast_evaluation',b['old']['provider'])]:
        with pytest.raises(Exception):
            with provider.open_read(meta.reference,purpose=purpose) as session:list(session.batches())
    plan_change(b,datasets=b['old']['plan'].datasets)
    with pytest.raises(ForecastRolloutError):run(b)
    assert not b['calls']


@pytest.mark.parametrize('fault',['overlap','fit_cutoff','assessment_cutoff'])
def test_full_horizon_interval_validation(make_rollout,fault):
    b=make_rollout();f=b['plan'].folds[0].model_dump()
    if fault=='overlap':f.update(validation_origins=(b['at'](6),),model_fit_cutoff=b['at'](6))
    elif fault=='fit_cutoff':f['model_fit_cutoff']=b['at'](6)
    else:f['assessment_cutoff']=b['at'](10)
    plan_change(b,folds=(TemporalFold.model_validate(f),))
    with pytest.raises(ForecastRolloutError):run(b)
    assert not b['calls']


@pytest.mark.parametrize('fault',['global_availability','global_permission','read_permission'])
def test_final_global_and_read_revocations_rechecked_with_original_scope(make_rollout,fault):
    b=make_rollout();events=[];revoked=False;oldverify=b['service'].verify_availability;oldread=b['provider'].require_current
    target=b['at'](8)
    def verify(ctx,claims):
        events.append(('availability',ctx.fold_id,ctx.forecast_origin))
        return oldverify(ctx,claims) and not (revoked and fault=='global_availability' and ctx.forecast_origin==target)
    def authorize(ctx,stage):
        nonlocal revoked
        events.append((stage,ctx.fold_id,ctx.forecast_origin))
        if stage=='return' and ctx.fold_id is None and fault.startswith('global'):revoked=True
        return not (revoked and fault!='global_availability' and stage=='return' and ctx.forecast_origin==target)
    def read(*a,**kw):
        nonlocal revoked
        events.append(('read',None,None))
        value=oldread(*a,**kw)
        if len(b['calls'])==6 and fault=='read_permission':revoked=True
        return value
    b['service'].authorize=authorize;b['service'].verify_availability=verify;b['provider'].require_current=read
    with pytest.raises(ForecastRolloutError):run(b)
    assert events[-1][2]==target
    assert ('return',None,None) in events
    lastread=max(i for i,v in enumerate(events) if v[0]=='read')
    assert all(e[0] in ('availability','return') for e in events[lastread+1:])


@pytest.mark.parametrize('when',['predictor','terminal'])
@pytest.mark.parametrize('failure',['cancel','deadline','secret'])
def test_callback_cancellation_deadline_and_secret_sanitization(make_rollout,when,failure):
    b=make_rollout();flags={'cancel':False,'clock':0};b['service'].elapsed_clock=lambda:flags['clock']
    def trigger():
        if failure=='cancel':flags['cancel']=True
        elif failure=='deadline':flags['clock']=300
        else:raise RuntimeError('SYNTHETIC-TOP-SECRET')
    if when=='predictor':
        original=b['service'].predictor
        def callback(req):
            value=original(req)
            if req.prefix_length==3:trigger()
            return value
        b['service'].predictor=callback
    else:
        def authorize(ctx,stage):
            if stage=='return' and ctx.forecast_origin==b['at'](8):trigger()
            return True
        b['service'].authorize=authorize
    with pytest.raises(ForecastRolloutError) as info:run(b,cancelled=lambda:flags['cancel'])
    assert 'SYNTHETIC-TOP-SECRET' not in ''.join(traceback.format_exception(info.type,info.value,info.tb))


def test_one_complete_capture_and_released_lock_before_predictor(make_rollout):
    b=make_rollout();reads=[];active=[False];old=b['provider'].open_read;predict=b['service'].predictor
    @contextmanager
    def tracked(*a,**kw):
        reads.append(1);active[0]=True
        with old(*a,**kw) as session:yield session
        active[0]=False
    def callback(req):
        assert not active[0]
        with b['store']._publication_session(StagingBudgets()):pass
        return predict(req)
    b['provider'].open_read=tracked;b['service'].predictor=callback
    run(b);assert len(reads)==1


@pytest.mark.parametrize('fault',['late_read','cleanup'])
def test_capture_failure_returns_no_predictions(make_rollout,fault):
    b=make_rollout();old=b['provider'].open_read
    @contextmanager
    def broken(*a,**kw):
        with old(*a,**kw) as session:
            if fault=='late_read':
                original=session.batches
                def batches():
                    for i,frame in enumerate(original()):
                        if i==2:raise OSError('synthetic read error')
                        yield frame
                session.batches=batches
            yield session
        if fault=='cleanup':raise OSError('synthetic cleanup error')
    b['provider'].open_read=broken
    with pytest.raises(ForecastRolloutError):run(b)
    assert not b['calls']


@pytest.mark.parametrize('field,limit',[('max_work_cells',100),('max_output_cells',1),('max_captured_rows',1),
    ('max_decoded_bytes',1),('max_fitted_state_bytes',1),('max_assembly_bytes',1),('max_representation_bytes',1),
    ('max_feedback_bytes',1),('max_prediction_bytes',1)])
def test_aggregate_budgets_fail_closed(make_rollout,field,limit):
    b=make_rollout();d=b['plan'].model_dump();d['budgets'][field]=limit;plan_change(b,**d)
    with pytest.raises(ForecastRolloutError):run(b)


@pytest.mark.parametrize('frequency,timezone,start',[('weekly','UTC','2019-12-02'),('daily','America/New_York','2020-03-03')])
def test_calendar_iso_and_dst(make_rollout,frequency,timezone,start):
    b=make_rollout(frequency=frequency,timezone=timezone,start=start);result=run(b)
    for e in result.events:
        assert e.scope.target_period==b['at'](4+e.scope.horizon_step-1) if e.scope.forecast_origin==b['at'](4) else e.scope.target_period==b['at'](8+e.scope.horizon_step-1)
        assert e.scope.target_period_end==pd.Timestamp(e.scope.target_period)+pd.DateOffset(days=7 if frequency=='weekly' else 1)


def test_signed_zero_and_key_order_are_preserved(make_rollout):
    b=make_rollout()
    b['service'].predictor=lambda req:PredictionBatch(request_id=req.request_id,points=tuple(
        PredictionPoint(scope=s,value=-0.) for s in reversed(req.scopes)))
    result=run(b)
    assert all(math.copysign(1,e.value)==-1 for e in result.events)


def test_target_source_alias_rejected_by_existing_mapping_boundary(make_rollout):
    from src.data.processing.canonical_mapper import CanonicalMapper,CanonicalMappingError
    b=make_rollout();mapping=b['metadata'][0].mapping;data=mapping.model_dump()
    data['field_mappings']['available_at']=mapping.field_mappings[mapping.target_value_field]
    forged=type(mapping).model_validate(data)
    # CanonicalMapper rejects duplicate source aliases before any staged publication.
    with pytest.raises(CanonicalMappingError,match='source aliases'):
        CanonicalMapper(forged)


def test_org_series_fold_origin_state_and_feedback_isolation(make_rollout):
    b=make_rollout(organizations=(2,3))
    second=TemporalFold(fold_id='fold-2',training_origins=(b['at'](5),),validation_origins=(b['at'](9),),
        calibration_start=b['at'](0),categorical_cutoff=b['at'](4),model_fit_cutoff=b['at'](8),assessment_cutoff=b['at'](12))
    # Separate held-out intervals: fold-1 has only training, fold-2 has held-out.
    first=b['plan'].folds[0].model_copy(update={'validation_origins':()})
    plan_change(b,folds=(first,second));result=run(b)
    assert len(result.events)==36
    assert len({(c.fold_id,c.organization_id,c.state_id) for c in result.compatibilities})==4
    for req in b['calls']:
        if req.prefix_length==2:assert req.matrix.lag1.eq(-2.).all()
        if req.prefix_length==3:assert req.matrix.lag1.eq(0.).all()
    # Identical series IDs across tenants never share actual history or category state.
    firsts=[r for r in b['calls'] if r.prefix_length==1 and r.compatibility.fold_id=='fold-1']
    assert sorted(firsts[1].matrix.mean4)==[v+1000 for v in sorted(firsts[0].matrix.mean4)]


@pytest.mark.parametrize('scope',['later_fold','other_organization'])
def test_global_revocation_retains_exact_later_fold_and_org(make_rollout,scope):
    b=make_rollout(organizations=(2,3));seen=[];revoked=False
    first=b['plan'].folds[0].model_copy(update={'validation_origins':()})
    second=TemporalFold(fold_id='fold-2',training_origins=(b['at'](5),),validation_origins=(b['at'](8),),
        calibration_start=b['at'](0),categorical_cutoff=b['at'](4),model_fit_cutoff=b['at'](8),assessment_cutoff=b['at'](11))
    plan_change(b,folds=(first,second))
    def authorize(ctx,phase):
        nonlocal revoked
        if phase=='return' and ctx.fold_id is None:revoked=True
        seen.append((ctx.fold_id,ctx.metadata.reference.organization_id,ctx.forecast_origin,phase))
        return not (revoked and phase=='return' and ctx.forecast_origin==b['at'](8) and
            (ctx.fold_id=='fold-2' if scope=='later_fold' else ctx.metadata.reference.organization_id==UUID(int=3)))
    b['service'].authorize=authorize
    with pytest.raises(ForecastRolloutError):run(b)
    assert seen[-1][0]=='fold-2' and seen[-1][2]==b['at'](8)
    if scope=='other_organization':assert seen[-1][1]==UUID(int=3)


@pytest.mark.parametrize('position',[4,5,7])
def test_selective_first_middle_last_origin_revocation(make_rollout,position):
    b=make_rollout();fold=b['plan'].folds[0].model_copy(update={
        'training_origins':(b['at'](4),b['at'](5),b['at'](7)),'validation_origins':(),
        'model_fit_cutoff':b['at'](10)})
    plan_change(b,folds=(fold,));revoked=False;checked=[]
    def authorize(ctx,phase):
        nonlocal revoked
        if phase=='return' and ctx.fold_id is None:revoked=True
        if revoked and ctx.fold_id is not None:
            checked.append(ctx.forecast_origin)
            return ctx.forecast_origin!=b['at'](position)
        return True
    b['service'].authorize=authorize
    with pytest.raises(ForecastRolloutError):run(b)
    assert checked[-1]==b['at'](position)


@pytest.mark.parametrize('fault',['missing','delayed','late_category'])
def test_history_and_category_evidence_fail_without_silent_dropping(make_rollout,fault,monkeypatch):
    def change(raw):
        if fault=='missing':raw.drop(index=2,inplace=True);raw.reset_index(drop=True,inplace=True)
        elif fault=='delayed':raw.loc[3,'released']=pd.Timestamp('2020-02-10',tz='UTC')
        else:raw.loc[0,'promo_release']=pd.Timestamp('2020-02-10',tz='UTC')
    b=make_rollout(raw_change=change,exo=fault=='late_category');fits=[];original=CanonicalFeaturePipeline.fit_state
    def fit(self,*a,**kw):fits.append(1);return original(self,*a,**kw)
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',fit)
    with pytest.raises(ForecastRolloutError):run(b)
    assert not b['calls']
    if fault=='late_category':assert not fits


@pytest.mark.parametrize('availability',['known_in_advance','observed_only','external_forecast'])
def test_future_exogenous_uses_own_release_and_external_approval(make_rollout,availability):
    # Synthetic availability, independently checked against the published raw fixture.
    def change(raw):raw['promo_release']=pd.Timestamp('2019-01-01',tz='UTC')
    b=make_rollout(exo=True,raw_change=change)
    from src.data.contracts.features import FeatureConfiguration
    data=b['configuration'].model_dump();data['execution']['exogenous'][0].update(availability=availability,offset=None)
    b['configuration']=FeatureConfiguration.model_validate(data)
    cfg=b['configuration'];cap=b['plan'].predictor.model_copy(update={
        'representation_schema_id':representation_schema_id(cfg),'numeric_policy_id':numeric_policy_id(cfg)})
    plan_change(b,configuration_digest=CanonicalFeaturePipeline(cfg).digest,predictor=cap)
    external=[];b['service'].authorize_external=lambda ctx,req:external.append((ctx,req)) or True
    if availability=='observed_only':
        with pytest.raises(ForecastRolloutError):run(b)
        assert not b['calls']
    else:
        result=run(b);assert len(result.events)==12
        if availability=='external_forecast':
            assert external and len(external)%2==0
            half=len(external)//2
            assert all(a[0] is z[0] and a[1] is z[1] for a,z in zip(external[:half],external[half:]))


def test_external_revocation_precedes_terminal_scopes_and_checks_own_evidence(make_rollout):
    def change(raw):raw['promo_release']=pd.Timestamp('2019-01-01',tz='UTC')
    b=make_rollout(exo=True,raw_change=change)
    from src.data.contracts.features import FeatureConfiguration
    data=b['configuration'].model_dump();data['execution']['exogenous'][0].update(availability='external_forecast',offset=None)
    cfg=FeatureConfiguration.model_validate(data);b['configuration']=cfg
    cap=b['plan'].predictor.model_copy(update={'representation_schema_id':representation_schema_id(cfg),'numeric_policy_id':numeric_policy_id(cfg)})
    plan_change(b,configuration_digest=CanonicalFeaturePipeline(cfg).digest,predictor=cap)
    trace=[];revoked=False
    def external(ctx,req):
        nonlocal revoked
        trace.append(('external',ctx.forecast_origin))
        if len(b['calls'])==6:revoked=True
        return True
    def authorize(ctx,phase):
        trace.append((phase,ctx.forecast_origin))
        return not(revoked and phase=='return' and ctx.forecast_origin==b['at'](8))
    b['service'].authorize_external=external;b['service'].authorize=authorize
    with pytest.raises(ForecastRolloutError):run(b)
    assert trace[-1]==('return',b['at'](8))
    assert max(i for i,v in enumerate(trace) if v[0]=='external')<trace.index(('return',None))


def test_complete_prefix_checked_then_current_step_independently(make_rollout,monkeypatch):
    import src.services.deterministic_forecast_rollout_service as module
    b=make_rollout();verified=[];original=module.verify_feature_schema
    def check(schema,*a,**kw):
        verified.append((schema.forecast_horizon,len(a[2]),kw['forecast_origin']))
        return original(schema,*a,**kw)
    monkeypatch.setattr(module,'verify_feature_schema',check)
    run(b)
    assert verified[:3]==[(1,2,b['at'](4)),(2,4,b['at'](4)),(3,6,b['at'](4))]
    assert len({r.compatibility.compatibility_id for r in b['calls'][:3]})==1
    assert len({r.prefix_schema_id for r in b['calls'][:3]})==3


def test_late_horizon_failure_yields_no_result_and_keeps_publication(make_rollout):
    b=make_rollout();old=b['service'].predictor
    def callback(req):
        value=old(req)
        if len(b['calls'])==6:raise ValueError('last origin last horizon')
        return value
    b['service'].predictor=callback
    with pytest.raises(ForecastRolloutError):run(b)
    with b['provider'].open_read(b['metadata'][0].reference,purpose='offline_forecast_evaluation') as session:
        assert len(list(session.batches()))>0


def test_capability_magnitude_and_predictor_call_budget(make_rollout):
    b=make_rollout();plan_change(b,predictor=b['plan'].predictor.model_copy(update={'max_absolute_prediction':1.}))
    with pytest.raises(ForecastRolloutError):run(b)
    data=b['plan'].model_dump();data['budgets']['max_predictor_calls']=5
    with pytest.raises(ValueError):ForecastEvaluationPlan.model_validate(data)


def test_late_chunk_tampering_blocks_before_predictor(make_rollout):
    b=make_rollout();chunks=sorted(b['store'].root.rglob('*-canonical.parquet'),key=lambda p:int(p.name.split('-')[0]))
    assert chunks
    # Deliberate offline tampering of this synthetic sealed file only.
    chunks[-1].chmod(0o600)
    chunks[-1].write_bytes(b'corrupt-late-chunk')
    with pytest.raises(ForecastRolloutError):run(b)
    assert not b['calls']


# Historical authorities below derive expected values from independent synthetic
# source truth and publication metadata, never the current request's value/hash.
from src.services.canonical_training_preparation_service import (
    ValueAttestedAvailabilityVerifier, historical_value_identity, TrainingPreparationError)


def bind_history_authority(b):
    ledger={};seen=[];revoked=set()
    for meta in b['metadata']:
        raw=b['raws'][meta.reference.organization_id.int]
        with b['provider'].open_read(meta.reference,purpose=meta.purpose) as session:
            for frame in session.batches():
                for row in frame.to_dict('records'):
                    truth=raw.iloc[int(row['source_record_id'])]
                    definition=next(s for s in meta.series if s.series_id==row['series_id'])
                    assert definition.dimensions['location']==truth['branch']
                    value=truth['reported']
                    # Independently specified canonical identities (not copied
                    # from an attestation or its hash).
                    if type(value) is Decimal:
                        p=value.as_tuple();identity=('decimal',p.sign,tuple(p.digits),p.exponent)
                    else:
                        import struct
                        identity=('float64',struct.pack('>d',float(value)).hex())
                    ledger[meta.reference.organization_id,row['source_record_id']]=dict(
                        metadata=meta,series_id=row['series_id'],lineage_id=row['lineage_id'],
                        period_start=truth['from'],period_end=truth['to'],available_at=truth['released'],
                        evidence=truth['proof'],value_identity=identity,revision_available=truth['released'])
    def authority(ctx,claims):
        import hashlib
        seen.append((ctx,claims))
        binding=next((d for d in b['plan'].datasets if d.reference==ctx.metadata.reference),None)
        if binding is None or ctx.plan_digest!=b['plan'].digest or ctx.series_ids!=binding.series_ids:return False
        if ctx.fold_id is not None:
            fold=next((f for f in b['plan'].folds if f.fold_id==ctx.fold_id),None)
            if (fold is None or ctx.forecast_origin not in fold.origins
                    or ctx.training_cutoff!=fold.categorical_cutoff):return False
            step=1 if b['plan'].frequency=='daily' else 7
            if (ctx.target_period_end is not None and ctx.target_period_end
                    !=ctx.forecast_origin+pd.DateOffset(days=step*b['plan'].forecast_horizon)):return False
        for c in claims:
            a=c.availability;t=ledger.get((c.organization_id,a.source_record_id))
            if t is None or (c.organization_id,ctx.fold_id,ctx.forecast_origin) in revoked:return False
            m=t['metadata'];mapping=m.mapping
            if (c.protocol_version!='captured_history_value_v1' or c.dataset_reference!=m.reference
                or ctx.metadata!=m or c.manifest_digest!=m.manifest_digest or c.admission_digest!=m.admission_digest
                or c.mapping_digest!=hashlib.sha256(m.mapping_json.encode()).hexdigest()
                or c.mapping_id!=mapping.mapping_id or c.mapping_version!=mapping.version
                or c.source_id!=m.source_id or c.metric!=mapping.target_metric
                or (c.unit,c.currency)!=(mapping.measurement.unit,mapping.measurement.currency)
                or c.purpose!='offline_forecast_evaluation' or c.series_ids!=ctx.series_ids
                or a.series_id!=t['series_id'] or a.lineage_id!=t['lineage_id'] or a.field!='value'
                or a.period_start!=t['period_start'] or a.period_end!=t['period_end']
                or a.available_at!=t['available_at'] or a.evidence_reference!=t['evidence']
                or a.value_identity!=t['value_identity']
                or c.forecast_origin!=ctx.forecast_origin or c.calibration_cutoff!=ctx.training_cutoff
                or c.plan_digest!=ctx.plan_digest or c.fold_id!=ctx.fold_id
                or c.target_period_end!=ctx.target_period_end):return False
            # Claims cover the full publication, including future rows. Only
            # completed, released records can enter O's historical context.
            if (c.forecast_origin is not None and a.period_end<=c.forecast_origin
                and a.available_at<=c.forecast_origin and t['revision_available']>c.forecast_origin):return False
            if (c.calibration_cutoff is not None and a.period_end<=c.calibration_cutoff
                and a.available_at<=c.calibration_cutoff and t['revision_available']>c.calibration_cutoff):return False
        return True
    wrapped=ValueAttestedAvailabilityVerifier(verify_availability=b['service'].verify_availability,
        verify_captured_values=authority)
    b['service'].verify_availability=wrapped
    return dict(ledger=ledger,seen=seen,revoked=revoked,authority=authority,wrapped=wrapped)


def substitute_history(b,change):
    opened=b['provider'].open_read
    @contextmanager
    def detached(*args,**kwargs):
        with opened(*args,**kwargs) as session:
            batches=session.batches
            def frames():
                for frame in batches():
                    view=frame.copy(deep=True);change(view);yield view
            session.batches=frames
            yield session
    b['provider'].open_read=detached


def test_value_attested_original_eight_and_substitution_rejects_before_prediction(make_rollout):
    b=make_rollout();proof=bind_history_authority(b)
    result=run(b);assert result.events
    north=next(s.series_id for s in b['metadata'][0].series if s.dimensions['location']=='north')
    assert b['calls'][0].matrix.loc[(UUID(int=2),north,b['at'](4)),'lag1']==8.
    original=next(c for _,cs in proof['seen'] for c in cs if c.availability.series_id==north
        and c.availability.period_start==b['at'](3))
    b['calls'].clear();proof['seen'].clear()
    def change(frame):
        mask=(frame.series_id==north)&(frame.period_start==b['at'](3))
        frame.loc[mask,'value']=800.
    substitute_history(b,change)
    with pytest.raises(ForecastRolloutError):run(b)
    assert not b['calls']
    changed=next(c for _,cs in proof['seen'] for c in cs if c.availability.series_id==north
        and c.availability.period_start==b['at'](3))
    assert changed.availability.value_identity==historical_value_identity(800.)
    assert changed.availability.value_identity!=original.availability.value_identity
    assert replace(changed.availability,value_identity=original.availability.value_identity)==original.availability
    print('CONTROL_LAG=8; SUBSTITUTED_800=REJECTED; RECOMPUTED_IDENTITY=REJECTED; PREDICTOR_CALLS=0')


@pytest.mark.parametrize('fault',['missing','version','manifest','admission','mapping','metric','series','field','period',
    'revision','backdated'])
def test_value_authority_missing_mismatched_or_backdated_truth_rejects(make_rollout,fault):
    b=make_rollout();proof=bind_history_authority(b)
    key=next(k for k,t in proof['ledger'].items() if t['period_start']==b['at'](3))
    t=proof['ledger'][key];m=t['metadata']
    if fault=='missing':del proof['ledger'][key]
    elif fault in ('version','manifest','admission','mapping','metric'):
        if fault=='version':m=replace(m,reference=replace(m.reference,version_id='0'*64))
        elif fault=='manifest':m=replace(m,manifest_digest='0'*64)
        elif fault=='admission':m=replace(m,admission_digest='0'*64)
        else:
            mapping=m.mapping.model_dump()
            if fault=='metric':
                mapping['target_metric']='wrong';mapping['measurement']['metric']='wrong'
            else:mapping['version']='9.0.0'
            m=replace(m,mapping_json=type(m.mapping).model_validate(mapping).model_dump_json())
        t['metadata']=m
    elif fault=='series':t['series_id']=UUID(int=999)
    elif fault=='field':
        def bad(ctx,claims):
            return proof['authority'](ctx,tuple(replace(c,availability=replace(c.availability,field='other')) for c in claims))
        proof['wrapped']._values=bad
    elif fault=='period':t['period_start']=b['at'](2)
    else:t['revision_available']=b['at'](5)  # current value did not exist at O, despite old release declaration
    with pytest.raises(ForecastRolloutError):run(b)
    assert not b['calls']


@pytest.mark.parametrize('value,expected',[(Decimal('-0.0000'),('decimal',1,(0,),-4)),
    (Decimal('0.10'),('decimal',0,(1,0),-2)),(8,('integer','8')),
    (-0.,('float64','8000000000000000')),(0.,('float64','0000000000000000'))])
def test_historical_value_identity_is_exact_typed_and_immutable(value,expected):
    assert historical_value_identity(value)==expected
    assert historical_value_identity(Decimal('0.1'))!=historical_value_identity(Decimal('0.10'))
    assert historical_value_identity(8)!=historical_value_identity(8.)


@pytest.mark.parametrize('value',[True,'8',object(),Decimal('NaN'),Decimal('Infinity'),float('nan'),float('inf')])
def test_historical_identity_never_coerces_unsupported_values(value):
    with pytest.raises(TrainingPreparationError):historical_value_identity(value)


def test_value_attestation_requires_authority_no_fallback():
    def unavailable(*args):raise AssertionError('must not be invoked')
    with pytest.raises(TrainingPreparationError):
        ValueAttestedAvailabilityVerifier(verify_availability=unavailable,verify_captured_values=None)


def test_original_history_claims_reused_at_terminal_scope_and_decimal_conversion(make_rollout):
    b=make_rollout(decimal=True);proof=bind_history_authority(b);before=b['raws'][2].reported.tolist()
    run(b)
    scoped=[cs for ctx,cs in proof['seen'] if ctx.target_period_end is not None and ctx.forecast_origin==b['at'](4)]
    assert len(scoped)==2
    for first,last in zip(scoped[0],scoped[-1]):
        assert first==last and first.availability is last.availability
        assert first.availability.value_identity[0]=='decimal'
        assert first.availability.value_identity[-1]==-4
    with pytest.raises(Exception):scoped[0][0].availability.value_identity=()
    assert b['raws'][2].reported.tolist()==before
    # Jan27-Feb3 value is released exactly at O; no conservative off-by-one rejection.
    assert any(c.availability.available_at==b['at'](4) for c in scoped[0])


@pytest.mark.parametrize('fault',['revoke','cancel','deadline','exception'])
def test_value_authority_terminal_failure_is_all_or_nothing(make_rollout,fault):
    b=make_rollout();proof=bind_history_authority(b);seen={};cancel=[False];clock=[0.]
    b['service'].elapsed_clock=lambda:clock[0]
    def current(ctx,claims):
        if ctx.target_period_end is not None:
            key=(ctx.fold_id,ctx.forecast_origin);seen[key]=seen.get(key,0)+1
            if seen[key]==2:
                if fault=='revoke':return False
                if fault=='cancel':cancel[0]=True
                if fault=='deadline':clock[0]=b['plan'].budgets.max_elapsed_seconds
                if fault=='exception':raise RuntimeError('synthetic-history-secret')
        return proof['authority'](ctx,claims)
    proof['wrapped']._values=current
    with pytest.raises(ForecastRolloutError) as error:run(b,cancelled=lambda:cancel[0])
    assert 'synthetic-history-secret' not in ''.join(traceback.format_exception(error.value))
    assert b['calls']  # failure happened at fresh terminal verification, not initial approval


def test_value_authority_missing_identity_rejects_even_with_legacy_approval(make_rollout):
    b=make_rollout();proof=bind_history_authority(b)
    # Supported stronger adapter requires identities even when old approval succeeds.
    run(b);ctx,claims=proof['seen'][0]
    with pytest.raises(TrainingPreparationError):proof['wrapped'](ctx,(replace(claims[0].availability,value_identity=None),))


def numeric_covariate_configuration(configuration, *, availability='lagged', tolerance=None):
    """Synthetic numeric promotion input; tolerance is NOT business approval."""
    from src.data.contracts.features import FeatureConfiguration
    data=configuration.model_dump()
    data['category_encoding'].pop('promotion')
    data['execution']['categories']=tuple(c for c in data['execution']['categories'] if c['field']!='promotion')
    data['output_dtypes']['promotion']='float64'
    rule=data['execution']['exogenous'][0]
    rule.update(availability=availability,offset=1 if availability=='lagged' else None)
    if tolerance is not None:
        measurement={**data['execution']['measurement'],'unit':'synthetic_index','currency':None}
        rule['measurement']=measurement
        data['execution'].update(numeric_conversion='versioned_float64',numeric_policies=(
            dict(field='value',measurement=data['execution']['measurement'],mode='STRICT_EXACT'),
            dict(field='promotion',measurement=measurement,mode='BOUNDED_FLOAT64',absolute_tolerance=tolerance)))
    return FeatureConfiguration.model_validate(data)


def configure_numeric_rollout(bundle, *, availability='lagged', tolerance=None):
    cfg=numeric_covariate_configuration(bundle['configuration'],availability=availability,tolerance=tolerance)
    cap=bundle['plan'].predictor.model_copy(update={
        'representation_schema_id':representation_schema_id(cfg),'numeric_policy_id':numeric_policy_id(cfg)})
    bundle['configuration']=cfg
    plan_change(bundle,configuration_digest=CanonicalFeaturePipeline(cfg).digest,predictor=cap,forecast_horizon=1)
    return cfg


@pytest.mark.parametrize('availability',['lagged','known_in_advance'])
@pytest.mark.parametrize('value,accepted',[
    (2**53+1,False),(-(2**53+1),False),(2**53,True),(2**53+2,True),(1.5,True),
    (Decimal('0.125'),True),(Decimal('0.10'),False),(Decimal('1E-400'),False),(Decimal('1E400'),False),
    (Decimal('-0.0000'),True),(Decimal('0.00'),True),(-0.0,True)])
def test_numeric_covariate_reaches_exact_conversion_without_inference(make_rollout,monkeypatch,value,accepted,availability):
    def raw(frame):
        frame['offer']=pd.Series([value]*len(frame),dtype=object);frame.loc[0,'offer']=1.0
    b=make_rollout(exo=True,raw_change=raw);configure_numeric_rollout(b,availability=availability)
    seen=[];old=CanonicalFeaturePipeline.convert_numeric
    def convert(self,original,field='value'):
        if field=='promotion':seen.append(original)
        return old(self,original,field)
    monkeypatch.setattr(CanonicalFeaturePipeline,'convert_numeric',convert)
    if accepted:
        result=run(b);assert result.events and len(b['calls'])==2
        for request in b['calls']:
            for row in request.rows:
                observed=row[-1];assert observed==float(value)
                if float(value)==0:assert math.copysign(1,observed)==math.copysign(1,float(value))
    else:
        with pytest.raises(ForecastRolloutError):run(b)
        assert not b['calls']
    identities=[v for v in seen if type(v) is type(value) and v==value]
    assert identities
    if type(value) is Decimal:assert all(v.as_tuple()==value.as_tuple() for v in identities)
    print('NUMERIC_B1',availability,type(value).__name__,repr(value),'accepted=',accepted,'calls=',len(b['calls']))


@pytest.mark.parametrize('value,tolerance,accepted',[
    (2**53+1,'1',True),(2**53+1,'0.999',False),(Decimal('0.10'),'1E-16',True),
    (Decimal('19.99'),'1E-14',True),(Decimal('1E-400'),'1E-300',False),(Decimal('1E400'),'1E500',False)])
def test_numeric_covariate_bounded_conversion_is_explicit_and_scoped(make_rollout,value,tolerance,accepted):
    from fractions import Fraction
    def raw(frame):
        frame['offer']=pd.Series([value]*len(frame),dtype=object);frame.loc[0,'offer']=1.0
    b=make_rollout(exo=True,raw_change=raw);cfg=configure_numeric_rollout(b,tolerance=tolerance)
    if accepted:
        result=run(b);assert result.events and len(b['calls'])==2
        converted=b['calls'][0].rows[0][-1]
        assert abs(Fraction.from_float(converted)-Fraction(value))<=Fraction(Decimal(tolerance))
        assert cfg.execution.numeric_policies[1].field=='promotion'
        assert result.events[0].numeric_policy_id==numeric_policy_id(cfg)
    else:
        with pytest.raises(ForecastRolloutError):run(b)
        assert not b['calls']
    assert type(b['raws'][2].offer.iloc[1]) is type(value) and b['raws'][2].offer.iloc[1]==value


def test_numeric_observed_only_covariate_does_not_gain_future_availability(make_rollout):
    def raw(frame):frame['offer']=pd.Series([1.0]*len(frame),dtype=object)
    b=make_rollout(exo=True,raw_change=raw);configure_numeric_rollout(b,availability='observed_only')
    with pytest.raises(ForecastRolloutError):run(b)
    assert not b['calls']


def test_numeric_exogenous_assembly_keeps_original_types(make_rollout):
    from src.features.canonical_feature_pipeline import FeatureExecutionError
    def raw(frame):
        frame['offer']=pd.Series([2**53+1]*len(frame),dtype=object);frame.loc[0,'offer']=1.0
    b=make_rollout(exo=True,raw_change=raw);cfg=configure_numeric_rollout(b)
    frames=[]
    with b['provider'].open_read(b['metadata'][0].reference,purpose='offline_forecast_evaluation') as session:
        frames.extend(session.batches())
    frame=pd.concat(frames,ignore_index=True);frame['organization_id']=b['metadata'][0].reference.organization_id
    claims,exo=b['service'].preparation._availability(frame,CanonicalFeaturePipeline(cfg),lambda:None)
    assert claims and exo['value'].dtype==object
    assert type(exo['value'].iloc[0]) is float and exo['value'].iloc[0]==1.0
    assert type(exo['value'].iloc[1]) is int and exo['value'].iloc[1]==2**53+1
    with pytest.raises(FeatureExecutionError):CanonicalFeaturePipeline(cfg).convert_numeric(exo['value'].iloc[1],'promotion')


def variable_numeric_batch_adapter(monkeypatch):
    """Legitimate numeric-family dtype variation, serialized by real storage."""
    original_run=runpy.run_path
    def load(path,*args,**kwargs):
        module=original_run(path,*args,**kwargs)
        if str(path).endswith('tests/data/adapters/test_integration_foundation.py'):
            cls=module['MemoryAdapter'];extract=cls.extract
            def batches(self,**options):
                for batch in extract(self,**options):
                    values=batch.records['offer'].tolist()
                    dtype=('int64' if all(type(v) is int for v in values)
                        else 'float64' if all(type(v) is float for v in values) else None)
                    if dtype is not None:
                        frame=batch.records.copy()
                        frame['offer']=pd.Series(values,index=frame.index,dtype=dtype)
                        batch=replace(batch,records=frame)
                    yield batch
            cls.extract=batches
        return module
    monkeypatch.setattr(runpy,'run_path',load)
