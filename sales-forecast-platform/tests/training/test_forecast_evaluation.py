"""Real synthetic admission/read gates with independently controlled revision truth."""
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
import json
import runpy
import traceback
from uuid import UUID

import pandas as pd
import pytest

from src.data.contracts.forecast_evaluation import ForecastScoringPlan,ScoringArithmeticPolicy
from src.data.contracts.backtesting import PredictionBatch,PredictionPoint,PredictionEvent
from src.data.contracts.dataset import StagingBudgets
from src.data.processing.forecast_evaluation import numeric_identity
from src.services.canonical_forecast_evaluation_service import CanonicalForecastEvaluationService,ForecastEvaluationError
from src.services.deterministic_forecast_rollout_service import ForecastRolloutError,_OwnedExecution
from src.providers.dataset_store_provider import StagingError


@pytest.fixture
def make_evaluation(tmp_path):
    factory=runpy.run_path('tests/training/test_deterministic_forecast_rollout.py')['make_rollout'].__wrapped__(tmp_path)
    def make(*,decimal=False,organizations=(2,),raw_change=None):
        def raw(raw):
            for date,value in zip(pd.date_range('2020-03-02',periods=3,freq='7D',tz='UTC'),[-4.,0.,6.]):
                raw.loc[raw['from']==date,'reported']=Decimal(str(value)) if decimal else value
            if raw_change:raw_change(raw)
        b=factory(decimal=decimal,organizations=organizations,raw_change=raw)
        b['service'].predictor=lambda req:PredictionBatch(request_id=req.request_id,points=tuple(
            PredictionPoint(scope=s,value=[-2.,1.,3.][req.prefix_length-1]) for s in req.scopes))
        # Independent source ledger: created before evaluation; never accepted
        # from request declarations. Exact value and revision identity are checked.
        ledger={}
        for meta in b['metadata']:
            org=meta.reference.organization_id.int
            with b['provider'].open_read(meta.reference,purpose='offline_forecast_evaluation') as session:
                for frame in session.batches():
                    for row in frame.to_dict('records'):
                        original=b['raws'][org].iloc[int(row['source_record_id'])]
                        ledger[org,row['source_record_id']]=dict(reference=meta.reference,lineage_id=row['lineage_id'],
                            value_identity=numeric_identity(original['reported']),available=original['released'],
                            revision_available=original['released'],reference_id=original['proof'],source=meta.source_id)
        scoring=ForecastScoringPlan(plan_id='synthetic-scoring',producer_plan_digest=b['plan'].digest,datasets=b['plan'].datasets,
            partitions=('validation',),arithmetic=ScoringArithmeticPolicy(policy_id='synthetic-exact'))
        approved={scoring.digest};permissions={'evaluation':True,'labels':True,'revision':True};trace=[]
        def authorize(ctx,stage):
            trace.append(('evaluation',stage,ctx.fold_id,ctx.forecast_origin))
            return permissions['evaluation'] and ctx.scoring_plan_digest in approved and ctx.purpose=='offline_forecast_evaluation'
        def label_authorize(ctx,stage):
            trace.append(('labels',stage,ctx.fold_id,ctx.forecast_origin))
            return (permissions['labels'] and ctx.scoring_plan_digest in approved and ctx.metadata is not None
                and ctx.metadata.purpose==ctx.purpose=='offline_forecast_evaluation' and ctx.action=='score_labels'
                and ctx.fold_id in {f.fold_id for f in b['plan'].folds} and ctx.series_ids==next(d.series_ids for d in b['plan'].datasets
                    if d.reference.organization_id==ctx.metadata.reference.organization_id))
        def revision(ctx,claims):
            trace.append(('revision','verify',ctx.fold_id,ctx.forecast_origin))
            if not permissions['revision'] or not claims:return False
            for c in claims:
                truth=ledger.get((c.organization_id.int,c.source_record_id))
                if (truth is None or c.dataset_reference!=truth['reference'] or c.dataset_reference!=ctx.metadata.reference
                    or c.manifest_digest!=ctx.metadata.manifest_digest or c.admission_digest!=ctx.metadata.admission_digest
                    or c.source_id!=truth['source'] or c.lineage_id!=truth['lineage_id'] or c.value_identity!=truth['value_identity']
                    or c.available_at!=truth['available'] or c.evidence_reference!=truth['reference_id']
                    or truth['revision_available']>c.assessment_cutoff or c.assessment_cutoff!=ctx.assessment_cutoff):return False
            return True
        evaluator=CanonicalForecastEvaluationService(b['service'],authorize_evaluation=authorize,authorize_label_use=label_authorize,
            verify_label_revision=revision)
        b.update(evaluator=evaluator,scoring=scoring,approved=approved,permissions=permissions,ledger=ledger,trace=trace)
        return b
    return make


def evaluate(b,**kwargs):return b['evaluator'].evaluate(b['scoring'],b['plan'],b['configuration'],**kwargs)


def approve_change(b,**changes):
    data=b['scoring'].model_dump();data.update(changes);b['scoring']=ForecastScoringPlan.model_validate(data)
    b['approved'].add(b['scoring'].digest)


def test_owned_reference_and_exact_group_sufficient_statistics(make_evaluation):
    b=make_evaluation(decimal=True);before=tuple(v.as_tuple() for v in b['raws'][2].reported);out=evaluate(b)
    assert len(out.events)==6 and len(out.groups)==6
    for horizon,ae,se,saa in [(1,2,4,4),(2,1,1,0),(3,3,9,6)]:
        for event in (e for e in out.events if e.scope.horizon_step==horizon):
            assert (event.statistics.sae.fraction,event.statistics.sse.fraction,event.statistics.saa.fraction)==(ae,se,saa)
    north=b['metadata'][0].series[0].series_id
    events=[e for e in out.events if e.scope.series_id==north]
    assert sum(e.statistics.sae.fraction for e in events)==6
    assert sum(e.statistics.sse.fraction for e in events)==14
    assert sum(e.statistics.saa.fraction for e in events)==10
    assert evaluate(b).evaluation_id==out.evaluation_id
    with pytest.raises(Exception):out.events[0].scope.metric='wrong'
    assert all(t.exponent==-4 for t in before)
    assert tuple(v.as_tuple() for v in b['raws'][2].reported)==before


def test_no_external_event_or_receipt_admission(make_evaluation):
    b=make_evaluation();fake={'verified':True,'events':[]}
    with pytest.raises(ForecastEvaluationError):b['evaluator'].evaluate(fake,b['plan'],b['configuration'])
    with pytest.raises(TypeError):b['evaluator'].evaluate(b['scoring'],b['plan'],b['configuration'],events=fake)
    with pytest.raises(ForecastRolloutError):b['service']._owned_rollout(object(),b['plan'],b['configuration'])


@pytest.mark.parametrize('fault',['metric','currency','dimensions','lineage','missing','duplicate','extra','version'])
def test_recomputed_or_replaced_owned_results_cannot_be_substituted(make_evaluation,fault):
    b=make_evaluation();original=b['service']._owned_rollout
    def tampered(*a,**kw):
        handoff=original(*a,**kw);result=handoff.result;events=list(result.events)
        if fault in ('metric','currency','dimensions','version'):
            field='dataset_version' if fault=='version' else fault
            value={'metric':'fake','currency':'EUR','dimensions':(('location','fake'),),'version':'0'*64}[fault]
            events[0]=events[0].model_copy(update={'scope':events[0].scope.model_copy(update={field:value})})
        elif fault=='lineage':events[2]=events[2].model_copy(update={'parent_feedback_ids':('0'*64,)})
        elif fault=='missing':events.pop()
        elif fault=='duplicate':events[1]=events[0]
        else:events.append(events[-1])
        forged=result.model_copy(update={'events':tuple(events)})
        # All caller-computed identities are new but cannot replace the object
        # registered by the real B1 execution for this evaluator invocation.
        return replace(handoff,result=forged)
    b['service']._owned_rollout=tampered
    with pytest.raises(ForecastEvaluationError):evaluate(b)


def test_replay_of_previous_request_handoff_rejects(make_evaluation):
    b=make_evaluation();saved=[];old=b['service']._owned_rollout
    def record(*a,**kw):h=old(*a,**kw);saved.append(h);return h
    b['service']._owned_rollout=record;evaluate(b)
    b['service']._owned_rollout=lambda *a,**kw:saved[0]
    with pytest.raises(ForecastEvaluationError):evaluate(b)


def test_two_captures_prediction_finishes_before_labels_and_locks_release(make_evaluation):
    b=make_evaluation();reads=[];steps=[];original=b['provider'].open_read;predict=b['service'].predictor
    @contextmanager
    def read(*a,**kw):
        reads.append(len(steps))
        with original(*a,**kw) as session:yield session
    def prediction(req):
        assert len(reads)==1
        with b['store']._publication_session(StagingBudgets()):pass
        assert not {'label','actual','score'}&set(req.matrix)
        steps.append(req.prefix_length);return predict(req)
    b['provider'].open_read=read;b['service'].predictor=prediction
    assert evaluate(b).events and reads==[0,6]


@pytest.mark.parametrize('case',['after_origin','at_E','late','backdated','unverified'])
def test_label_revision_availability_is_not_inferred_from_row_timestamp(make_evaluation,case):
    def change(raw):
        if case in ('at_E','late'):
            raw.loc[raw['from']==pd.Timestamp('2020-03-02',tz='UTC'),'released']=pd.Timestamp('2020-03-23',tz='UTC')+pd.DateOffset(days=case=='late')
    b=make_evaluation(raw_change=change)
    if case=='backdated':
        for (org,record),truth in b['ledger'].items():
            if record=='8':truth['revision_available']=b['at'](12)
    if case=='unverified':b['permissions']['revision']=False
    if case in ('late','backdated','unverified'):
        with pytest.raises(ForecastEvaluationError):evaluate(b)
    else:assert evaluate(b).events


@pytest.mark.parametrize('permission',['evaluation','labels','revision'])
def test_read_permission_is_not_label_permission(make_evaluation,permission):
    b=make_evaluation();b['permissions'][permission]=False
    with pytest.raises(ForecastEvaluationError):evaluate(b)


@pytest.mark.parametrize('when',['global','origin','label_return','revision_return','read'])
def test_final_revocations_return_no_result(make_evaluation,when):
    b=make_evaluation();old=b['evaluator'].authorize;oldlabel=b['evaluator'].authorize_labels;oldrev=b['evaluator'].verify_revision
    read_count=[0];oldread=b['provider'].require_current
    def authorize(ctx,phase):
        if when=='global' and phase=='return' and ctx.fold_id is None:b['permissions']['labels']=False
        if when=='origin' and phase=='return' and ctx.fold_id is not None:return False
        return old(ctx,phase)
    def label(ctx,phase):return False if when=='label_return' and phase=='return' else oldlabel(ctx,phase)
    def revision(ctx,claims):
        read_count[0]+=1
        return False if when=='revision_return' and read_count[0]>1 else oldrev(ctx,claims)
    def read(*a,**kw):
        value=oldread(*a,**kw)
        if when=='read' and any(x[1]=='after_read' for x in b['trace']):b['permissions']['labels']=False
        return value
    b['evaluator'].authorize=authorize;b['evaluator'].authorize_labels=label;b['evaluator'].verify_revision=revision;b['provider'].require_current=read
    with pytest.raises(ForecastEvaluationError):evaluate(b)


@pytest.mark.parametrize('mode',['cancel','deadline','secret'])
@pytest.mark.parametrize('callback',['labels','revision','evaluation'])
def test_terminal_callback_failures_sanitized_and_checkpointed(make_evaluation,mode,callback):
    b=make_evaluation();state={'cancel':False,'clock':0,'final':False};b['evaluator'].elapsed_clock=lambda:state['clock']
    old=b['evaluator'].authorize;oldlabels=b['evaluator'].authorize_labels;oldrev=b['evaluator'].verify_revision
    def trigger():
        if mode=='cancel':state['cancel']=True
        elif mode=='deadline':state['clock']=300
        else:raise StagingError('B2A-UNIQUE-SYNTHETIC-SECRET')
    def authorize(ctx,phase):
        if phase=='return' and ctx.fold_id is None:state['final']=True
        if callback=='evaluation' and phase=='return' and ctx.fold_id is not None:trigger()
        return old(ctx,phase)
    def labels(ctx,phase):
        if callback=='labels' and phase=='return':trigger()
        return oldlabels(ctx,phase)
    def revision(ctx,claims):
        if callback=='revision' and state['final']:trigger()
        return oldrev(ctx,claims)
    b['evaluator'].authorize=authorize;b['evaluator'].authorize_labels=labels;b['evaluator'].verify_revision=revision
    with pytest.raises(ForecastEvaluationError) as error:evaluate(b,cancelled=lambda:state['cancel'])
    assert 'B2A-UNIQUE-SYNTHETIC-SECRET' not in ''.join(traceback.format_exception(type(error.value),error.value,error.value.__traceback__))


@pytest.mark.parametrize('kind',['cleanup','late_read'])
def test_label_reader_failure_after_predictions_returns_nothing(make_evaluation,kind):
    b=make_evaluation();old=b['provider'].open_read;reads=[0]
    @contextmanager
    def read(*a,**kw):
        reads[0]+=1
        with old(*a,**kw) as session:
            if reads[0]==2 and kind=='late_read':
                batches=session.batches
                def broken():
                    for i,frame in enumerate(batches()):
                        if i==2:raise OSError('late label read')
                        yield frame
                session.batches=broken
            yield session
        if reads[0]==2 and kind=='cleanup':raise OSError('label cleanup')
    b['provider'].open_read=read
    with pytest.raises(ForecastEvaluationError):evaluate(b)


@pytest.mark.parametrize('field,limit',[('max_captured_rows',47),('max_decoded_bytes',1),('max_work_cells',1),
    ('max_verification_bytes',1),('max_intermediate_bytes',1),('max_result_bytes',1),('max_groups',1),('max_capture_sessions',1),('max_scored_events',5)])
def test_combined_request_budgets(make_evaluation,field,limit):
    b=make_evaluation();data=b['scoring'].budgets.model_dump();data[field]=limit;approve_change(b,budgets=data)
    with pytest.raises(ForecastEvaluationError):evaluate(b)


def test_wrong_version_training_purpose_and_unapproved_policy(make_evaluation):
    b=make_evaluation();before=b['scoring']
    data=b['scoring'].model_dump();data['datasets']=b['old']['plan'].datasets
    b['scoring']=ForecastScoringPlan.model_validate(data)
    with pytest.raises(ForecastEvaluationError):evaluate(b)
    b['scoring']=before.model_copy(update={'event_weighting':'forecast_event_weighted_v1'})
    with pytest.raises(ForecastEvaluationError):evaluate(b)


def test_multiple_orgs_and_no_cross_tenant_groups(make_evaluation):
    b=make_evaluation(organizations=(2,3));out=evaluate(b)
    assert {e.scope.organization_id for e in out.events}=={UUID(int=2),UUID(int=3)}
    assert len(out.events)==12 and len(out.groups)==12


@pytest.mark.parametrize('fault',['missing','ambiguous','wrong_period','wrong_unit'])
def test_label_grid_is_not_guessed_or_silently_dropped(make_evaluation,fault):
    b=make_evaluation();old=b['provider'].open_read;reads=[0]
    @contextmanager
    def read(*a,**kw):
        reads[0]+=1
        with old(*a,**kw) as session:
            if reads[0]==2:
                batches=session.batches
                def altered():
                    for frame in batches():
                        frame=frame.copy(deep=True)
                        selected=frame['period_start']==b['at'](8)
                        if selected.any():
                            if fault=='missing':frame=frame.loc[~selected]
                            elif fault=='ambiguous':frame=pd.concat([frame,frame.loc[selected]])
                            elif fault=='wrong_period':frame.loc[selected,'period_end']=b['at'](10)
                            else:frame.loc[selected,'unit']='incorrect'
                        yield frame
                session.batches=altered
            yield session
    b['provider'].open_read=read
    with pytest.raises(ForecastEvaluationError):evaluate(b)


def test_assessment_clock_cannot_be_backdated_by_request(make_evaluation):
    b=make_evaluation();old=b['service']._owned_rollout
    original_clock=b['provider'].clock;read=b['provider'].open_read;current=b['provider'].require_current
    inside=[False];closed=[];started=[False]
    @contextmanager
    def publication_read(*a,**kw):
        inside[0]=True
        try:
            with read(*a,**kw) as session:yield session
            closed.append(True)
        finally:inside[0]=False
    def publication_check(*a,**kw):
        inside[0]=True
        try:return current(*a,**kw)
        finally:inside[0]=False
    def prediction(*a,**kw):
        handoff=old(*a,**kw);started[0]=True;return handoff
    # Publication verification keeps its genuine clock. Only the evaluator's
    # explicit assessment-clock boundary moves, after a successful B1 run.
    b['provider'].clock=lambda:original_clock() if inside[0] or not started[0] else b['at'](10)
    b['provider'].open_read=publication_read;b['provider'].require_current=publication_check
    b['service']._owned_rollout=prediction
    with pytest.raises(ForecastEvaluationError):evaluate(b)
    assert len(closed)==2 and not any(e[0]=='revision' for e in b['trace'])


def test_origin_weighting_is_explicit_and_retains_distinct_event_ids(make_evaluation):
    from src.data.contracts.backtesting import ForecastEvaluationPlan
    b=make_evaluation();f=b['plan'].folds[0]
    f=f.model_copy(update={'validation_origins':(b['at'](8),b['at'](9)),'assessment_cutoff':b['at'](12)})
    b['plan']=ForecastEvaluationPlan.model_validate(b['plan'].model_copy(update={'folds':(f,)}).model_dump())
    approve_change(b,producer_plan_digest=b['plan'].digest)
    separated=evaluate(b)
    assert len(separated.events)==12 and len(separated.groups)==12
    assert all(g.statistics.count==1 and g.forecast_origin is not None for g in separated.groups)
    approve_change(b,event_weighting='forecast_event_weighted_v1')
    pooled=evaluate(b)
    assert len(pooled.events)==12 and len(pooled.groups)==6
    assert all(g.statistics.count==2 and g.forecast_origin is None for g in pooled.groups)
    assert {e.producer_event_id for e in separated.events}=={e.producer_event_id for e in pooled.events}
    assert all(len(set(g.event_ids))==2 for g in pooled.groups)
    for g in pooled.groups:
        es=[e for e in pooled.events if e.event_id in g.event_ids]
        assert {e.scope.horizon_step for e in es}=={g.horizon_step}
        assert len({(e.scope.organization_id,e.scope.series_id,e.scope.unit,e.scope.currency) for e in es})==1
        assert g.statistics.sae.fraction==sum(e.statistics.sae.fraction for e in es)


def test_training_publication_does_not_become_evaluation_truth(make_evaluation):
    from src.data.contracts.backtesting import ForecastEvaluationPlan
    b=make_evaluation();datasets=b['old']['plan'].datasets
    b['plan']=ForecastEvaluationPlan.model_validate(b['plan'].model_copy(update={'datasets':datasets}).model_dump())
    approve_change(b,datasets=datasets,producer_plan_digest=b['plan'].digest)
    with pytest.raises(ForecastEvaluationError):evaluate(b)


@pytest.mark.parametrize('org',[2,3])
def test_selective_terminal_tenant_denial_aborts_combined_result(make_evaluation,org):
    b=make_evaluation(organizations=(2,3));old=b['evaluator'].authorize_labels
    def authorize(ctx,phase):
        if phase=='return' and ctx.metadata.reference.organization_id.int==org:return False
        return old(ctx,phase)
    b['evaluator'].authorize_labels=authorize
    with pytest.raises(ForecastEvaluationError):evaluate(b)


def test_final_callback_trace_and_original_claim_identity(make_evaluation):
    b=make_evaluation();claims={};old=b['evaluator'].verify_revision
    def revision(ctx,proofs):
        key=(ctx.fold_id,ctx.metadata.reference,ctx.forecast_origin)
        if key in claims:assert proofs is claims[key]
        else:claims[key]=proofs
        with b['store']._publication_session(StagingBudgets()):pass
        return old(ctx,proofs)
    b['evaluator'].verify_revision=revision
    result=evaluate(b);assert result.events
    final=b['trace'].index(('evaluation','return',None,None))
    assert [event[:2] for event in b['trace'][final+1:]]==[
        ('revision','verify'),('labels','return'),('evaluation','return')]
    assert b['trace'][-1][2:]==('fold-1',b['at'](8))


def test_late_physical_corruption_after_predictions_aborts_label_capture(make_evaluation):
    b=make_evaluation();old=b['evaluator'].authorize_labels;damaged=[False]
    def labels(ctx,phase):
        if phase=='read' and not damaged[0]:
            paths=sorted(b['store'].root.rglob('*-canonical.parquet'),key=lambda p:int(p.name.split('-')[0]))
            paths[-1].chmod(0o600);paths[-1].write_bytes(b'synthetic-b2a-corruption');damaged[0]=True
        return old(ctx,phase)
    b['evaluator'].authorize_labels=labels
    with pytest.raises(ForecastEvaluationError):evaluate(b)
    assert damaged[0] and not any(e[1]=='after_read' for e in b['trace'])


def test_last_prediction_failure_never_opens_label_session(make_evaluation):
    b=make_evaluation();old=b['service'].predictor;calls=[0];reads=[0];open_read=b['provider'].open_read
    @contextmanager
    def read(*a,**kw):
        reads[0]+=1
        with open_read(*a,**kw) as session:yield session
    def prediction(request):
        calls[0]+=1
        if calls[0]==6:raise RuntimeError('SYNTHETIC-PREDICTOR-SECRET')
        return old(request)
    b['service'].predictor=prediction;b['provider'].open_read=read
    with pytest.raises(ForecastEvaluationError) as error:evaluate(b)
    assert reads[0]==1 and calls[0]==6
    assert 'SYNTHETIC-PREDICTOR-SECRET' not in ''.join(traceback.format_exception(error.value))
    assert not any(e[0]=='labels' for e in b['trace'])


def test_canonical_decimal_payload_unchanged_by_scoring(make_evaluation):
    b=make_evaluation(decimal=True)
    def values():
        output=[]
        for m in b['metadata']:
            with b['provider'].open_read(m.reference,purpose='offline_forecast_evaluation') as session:
                for frame in session.batches():output.extend(numeric_identity(v) for v in frame['value'])
        return tuple(output)
    before=values();evaluate(b);assert values()==before


def test_original_rollout_scope_revoked_by_scoring_global_callback(make_evaluation):
    b=make_evaluation();old=b['evaluator'].authorize;oldorigin=b['service'].authorize;revoked=[False]
    def global_check(ctx,phase):
        if phase=='return' and ctx.fold_id is None:revoked[0]=True
        return old(ctx,phase)
    def origin(ctx,phase):
        if revoked[0] and ctx.fold_id=='fold-1' and ctx.forecast_origin==b['at'](8):return False
        return oldorigin(ctx,phase)
    b['evaluator'].authorize=global_check;b['service'].authorize=origin
    with pytest.raises(ForecastEvaluationError):evaluate(b)


def test_value_attested_history_preserves_exact_b2a_label_scores(make_evaluation):
    b=make_evaluation(decimal=True)
    bind=runpy.run_path('tests/training/test_deterministic_forecast_rollout.py')['bind_history_authority']
    proof=bind(b)
    result=evaluate(b)
    assert len(result.events)==6
    assert sum(e.statistics.sae.fraction for e in result.events)==12
    assert sum(e.statistics.sse.fraction for e in result.events)==28
    assert sum(e.statistics.saa.fraction for e in result.events)==20
    scoped=[(ctx,claims) for ctx,claims in proof['seen'] if ctx.target_period_end is not None]
    # B1 and B2a terminal verification use the same retained availability objects.
    first_ctx,first=scoped[0]
    same=[cs for ctx,cs in scoped if ctx==first_ctx]
    assert len(same)==3
    assert all(c.availability is first[n].availability for cs in same for n,c in enumerate(cs))
    assert all(c.availability.value_identity[0]=='decimal' for c in first)


def test_value_attested_b2a_rejects_substituted_history_before_predictor_or_labels(make_evaluation):
    b=make_evaluation();helpers=runpy.run_path('tests/training/test_deterministic_forecast_rollout.py')
    helpers['bind_history_authority'](b);calls=[];labels=[]
    original=b['service'].predictor
    def predictor(req):calls.append(req);return original(req)
    b['service'].predictor=predictor
    previous=b['evaluator'].verify_revision
    def revision(ctx,claims):labels.append(claims);return previous(ctx,claims)
    b['evaluator'].verify_revision=revision
    def changed(frame):
        frame.loc[frame.period_start==b['at'](3),'value']=800.
    helpers['substitute_history'](b,changed)
    with pytest.raises(ForecastEvaluationError):evaluate(b)
    assert not calls and not labels


def test_value_authority_rechecked_after_b2a_global_approval_revocation(make_evaluation):
    b=make_evaluation();bind=runpy.run_path('tests/training/test_deterministic_forecast_rollout.py')['bind_history_authority']
    proof=bind(b);old=b['evaluator'].authorize
    def approve(ctx,stage):
        result=old(ctx,stage)
        if stage=='return' and ctx.metadata is None:
            proof['revoked'].add((UUID(int=2),'fold-1',b['at'](4)))
        return result
    b['evaluator'].authorize=approve
    with pytest.raises(ForecastEvaluationError):evaluate(b)
    assert any(e[0]=='revision' for e in b['trace'])  # B1 finished; B2a made the late fresh check
    assert (UUID(int=2),'fold-1',b['at'](4)) in proof['revoked']
