"""C2 real synthetic FINALIZED stores and independent source/revision ledgers."""
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

from src.data.contracts.backtesting import PredictionBatch,PredictionPoint
from src.data.contracts.dataset import StagingBudgets
from src.data.contracts.forecast_comparison import (JointComparisonPlan,JointComparisonPolicy,
    JointScoringBudgets,JointComparisonResult,JointPredictionPlan)
from src.data.contracts.forecast_evaluation import ScoringArithmeticPolicy
from src.services.canonical_forecast_comparison_service import (CanonicalForecastComparisonService,
    JointPredictionError,_JointRequest,_FrozenCapture)
from src.services.canonical_baseline_evaluation_service import BaselineAuthorityBinding
from src.services.canonical_forecast_evaluation_service import LabelRevisionClaim


@pytest.fixture
def make_scoring(tmp_path,monkeypatch):
    c1=runpy.run_path('tests/training/test_forecast_comparison_shared_execution.py')
    factory=c1['make_joint'].__wrapped__(tmp_path,monkeypatch)
    def make(*,policy=None,scoring_budgets=None,**kwargs):
        b=factory(**kwargs)
        policy=policy or JointComparisonPolicy(policy_id='synthetic-common-truth',
            arithmetic=ScoringArithmeticPolicy(policy_id='synthetic-exact'))
        plan=JointComparisonPlan(plan_id='joint-scoring-synthetic',predictions=b['plan'],policy_digest=policy.digest,
            budgets=scoring_budgets or JointScoringBudgets())
        permissions=dict(comparison=True,evaluation=True,labels=True,revision=True)
        ledger={key:dict(value) for key,value in b['ledger'].items()};trace=[];label_claims=[]
        b.update(comparison=plan,policy=policy,permissions=permissions,label_ledger=ledger,scoring_trace=trace,label_claims=label_claims)
        def common(ctx):
            return (ctx.plan_digest==b['comparison'].digest and ctx.plan_json==b['comparison'].model_dump_json()
                and ctx.policy_digest==policy.digest and ctx.producer_profile_ids==tuple(p.profile_id for p in b['plan'].producers)
                and ctx.purpose=='offline_forecast_evaluation')
        def comparison(ctx,stage):
            trace.append(('comparison',stage,ctx.fold_id,ctx.forecast_origin))
            if hook:=b.get('comparison_hook'):hook(ctx,stage)
            return common(ctx) and permissions['comparison']
        def evaluation(ctx,stage):
            trace.append(('evaluation',stage,ctx.fold_id,ctx.forecast_origin,ctx.producer_plan_digest))
            if hook:=b.get('evaluation_hook'):hook(ctx,stage)
            return (permissions['evaluation'] and ctx.producer_plan_digest in {p.plan.digest for p in b['plan'].producers}
                and ctx.purpose=='offline_forecast_evaluation')
        def labels(ctx,stage):
            trace.append(('labels',stage,ctx.fold_id,ctx.forecast_origin))
            assert not b['state']['active']
            if hook:=b.get('label_hook'):hook(ctx,stage)
            return common(ctx) and permissions['labels'] and bool(ctx.forecast_keys)
        def revision(ctx,claims):
            trace.append(('revision','verify',ctx.fold_id,ctx.forecast_origin));label_claims.append((ctx,claims))
            assert not b['state']['active']
            with b['store']._publication_session(StagingBudgets()):pass
            if hook:=b.get('revision_hook'):hook(ctx,claims)
            if not common(ctx) or not permissions['revision'] or not claims:return False
            expected={(k.series_id,k.target_period,k.target_period_end) for k in ctx.forecast_keys}
            if {(c.series_id,c.period_start,c.period_end) for c in claims}!=expected:return False
            for c in claims:
                t=ledger.get((c.organization_id,c.source_record_id));m=ctx.metadata
                if t is None:return False
                if (type(c) is not LabelRevisionClaim or c.dataset_reference!=m.reference or t['metadata']!=m
                    or c.manifest_digest!=m.manifest_digest or c.admission_digest!=m.admission_digest
                    or c.source_id!=m.source_id or c.mapping_digest!=next(d.mapping_digest for d in b['plan'].producers[0].plan.datasets if d.reference==m.reference)
                    or (c.metric,c.unit,c.currency)!=(m.mapping.target_metric,m.mapping.measurement.unit,m.mapping.measurement.currency)
                    or c.series_id!=t['series'] or c.lineage_id!=t['lineage'] or c.period_start!=t['start'] or c.period_end!=t['end']
                    or c.available_at!=t['released'] or c.evidence_reference!=t['evidence'] or c.value_identity!=t['value']
                    or c.assessment_cutoff!=ctx.assessment_cutoff or t['revision']>c.assessment_cutoff):return False
            return True
        authority=BaselineAuthorityBinding('independent-assessment-ledger','synthetic-1','captured_value_as_of_E_v1',
            b['plan'].producers[0].plan.datasets,revision)
        options={**b['options'], 'comparison_policy':policy,'label_revision_authority':authority,
            'authorize_comparison':comparison,'authorize_evaluation':evaluation,'authorize_label_use':labels}
        if scoring_budgets is not None:options['scoring_budgets']=scoring_budgets
        b['scoring_options']=options;b['joint']=CanonicalForecastComparisonService(**options)
        return b
    return make


def compare(b):return b['joint'].compare(b['comparison'],cancelled=lambda:b['state']['cancelled'])


def replan(b,**changes):
    b['comparison']=JointComparisonPlan.model_validate({**b['comparison'].model_dump(),**changes})


def signed_reference(raw):
    for index,value in ((7,8),(8,-4),(9,0),(10,6),(11,2)):
        raw.loc[raw['from']==pd.Timestamp('2020-01-06',tz='UTC')+pd.DateOffset(weeks=index),'reported']=value


def test_hand_calculated_exact_shared_truth_and_two_captures(make_scoring):
    b=make_scoring(decimal=True,raw_change=signed_reference,origins=(4,8));result=compare(b)
    assert b['state']['reads']==[0,16] and len(b['state']['calls'])==16
    assert len(result.truths)==8 and [len(p.evaluation.events) for p in result.producers]==[8,8]
    candidate,baseline=(p.evaluation for p in result.producers)
    north=next(s.series_id for s in b['metadata'][0].series if s.dimensions['location']=='north')
    ca=[e for e in candidate.events if e.scope.series_id==north]
    ba=[e for e in baseline.events if e.scope.series_id==north]
    assert [(e.statistics.sae.fraction,e.statistics.sse.fraction,e.statistics.saa.fraction) for e in ca]==[(2,4,4),(0,0,0),(0,0,6),(1,1,2)]
    assert [(e.statistics.sae.fraction,e.statistics.sse.fraction) for e in ba]==[(12,144),(8,64),(2,4),(6,36)]
    assert sum(e.statistics.sae.fraction for e in ca)==3 and sum(e.statistics.sse.fraction for e in ba)==248
    per_step={next(t.key.horizon_step for t in result.truths if t.key.key_id==d.forecast_key_ids[0]):d for d in result.deltas
        if next(t.key.series_id for t in result.truths if t.key.key_id==d.forecast_key_ids[0])==north}
    assert [per_step[k].sae.fraction for k in range(1,5)]==[-10,-8,-2,-5]
    assert [per_step[k].sse.fraction for k in range(1,5)]==[-140,-64,-4,-35]
    assert per_step[1].wape.fraction==Fraction(-5,2)
    assert per_step[2].wape is None and per_step[2].wape_status=='undefined_zero_denominator'
    assert candidate.scoring_plan.digest!=baseline.scoring_plan.digest
    assert [e.label_binding_digest for e in candidate.events]==[e.label_binding_digest for e in baseline.events]
    assert all(json.loads(t.original_value_identity_json)[0]=='decimal' for t in result.truths)
    with pytest.raises(Exception):result.truths[0].evidence_reference='changed'
    assert not {'rmse','rmse_delta','improvement','winner'}&set(type(result.deltas[0]).model_fields)


def test_private_lifecycle_all_seals_precede_labels_and_no_predictors_after(make_scoring,monkeypatch):
    b=make_scoring(origins=(4,8));trace=[];sealed=[];old=_JointRequest.seal;closed=[];close=_JointRequest.close
    def seal(self,*args):
        result=old(self,*args);sealed.append(args[3].profile_id);trace.append(('seal',self.phase));return result
    def closing(self):
        close(self);closed.append(self.phase)
    monkeypatch.setattr(_JointRequest,'seal',seal);monkeypatch.setattr(_JointRequest,'close',closing)
    opened=b['provider'].open_read
    @contextmanager
    def read(*a,**kw):
        label=len(b['state']['reads'])>=len(b['metadata'])
        if label:assert len(sealed)==2
        trace.append(('labels' if label else 'history',len(b['state']['calls'])))
        with opened(*a,**kw) as session:yield session
    b['provider'].open_read=read
    out=compare(b)
    assert trace==[('history',0),('seal','PREDICTING'),('seal','ALL_PREDICTIONS_SEALED'),('labels',16)]
    assert closed==['CLOSED'] and out.deltas
    assert not {'labels','actual','score','future_frame'}&set(type(b['state']['calls'][0][1]).model_fields)


@pytest.mark.parametrize('role',['candidate','baseline'])
def test_producer_failure_prevents_any_label_capture(make_scoring,role):
    b=make_scoring(horizon=1)
    def prediction(current,req,result):
        if current==role:raise RuntimeError('PREDICTOR-PRIVATE-SECRET')
        return result
    b['prediction_hook']=prediction
    with pytest.raises(JointPredictionError) as failure:compare(b)
    assert b['state']['reads']==[0] and 'PRIVATE-SECRET' not in ''.join(traceback.format_exception(failure.value))


@pytest.mark.parametrize('argument',['events','labels','authorities','predictors','shared','handoff','trusted'])
def test_compare_has_no_dependency_or_serialized_event_injection(make_scoring,argument):
    b=make_scoring(horizon=1)
    with pytest.raises(TypeError):b['joint'].compare(b['comparison'],**{argument:True})
    assert not b['state']['reads']


def test_c1_diagnostic_is_not_a_c2_input_and_label_free_mode_stays_unchanged(make_scoring):
    b=make_scoring(horizon=1);diagnostic=b['joint'].execute(b['plan'])
    assert diagnostic.producers and b['state']['reads']==[0] and not b['label_claims']
    b['state']['reads'].clear();b['state']['calls'].clear()
    for supplied in (diagnostic,diagnostic.model_dump(),{'predictions_sealed':True}):
        with pytest.raises(JointPredictionError):b['joint'].compare(supplied)
    assert not b['state']['reads']


@pytest.mark.parametrize('field',['authorize_comparison','authorize_evaluation','authorize_label_use','label_revision_authority','comparison_policy'])
def test_missing_trusted_c2_composition_rejects_before_reads(make_scoring,field):
    b=make_scoring(horizon=1)
    with pytest.raises(JointPredictionError):CanonicalForecastComparisonService(**{**b['scoring_options'],field:None})
    assert not b['state']['reads']


@pytest.mark.parametrize('fault',['missing','extra','duplicate','scope'])
def test_invalid_owned_producer_grid_never_opens_labels(make_scoring,monkeypatch,fault):
    from src.services.deterministic_forecast_rollout_service import DeterministicForecastRolloutService
    b=make_scoring(horizon=1);old=DeterministicForecastRolloutService._owned_shared_rollout
    def rollout(*a,**kw):
        h=old(*a,**kw);events=list(h.result.events)
        if fault=='missing':events.pop()
        elif fault=='extra':events.append(events[-1])
        elif fault=='duplicate':events[1]=events[0]
        else:events[0]=events[0].model_copy(update={'scope':events[0].scope.model_copy(update={'series_id':UUID(int=999)})})
        return replace(h,result=h.result.model_copy(update={'events':tuple(events)}))
    monkeypatch.setattr(DeterministicForecastRolloutService,'_owned_shared_rollout',rollout)
    with pytest.raises(JointPredictionError):compare(b)
    assert b['state']['reads']==[0]


def label_fault(b,change=None,cleanup=False):
    opened=b['provider'].open_read
    @contextmanager
    def read(*a,**kw):
        label=len(b['state']['reads'])>=len(b['metadata'])
        with opened(*a,**kw) as session:
            if label and change:
                batches=session.batches
                def frames():
                    for i,frame in enumerate(batches()):
                        altered=change(frame.copy(deep=True),i)
                        if altered is not None:yield altered
                session.batches=frames
            yield session
        if label and cleanup:raise OSError('LABEL-CLEANUP-PRIVATE-SECRET')
    b['provider'].open_read=read


@pytest.mark.parametrize('fault',['missing','duplicate','extra','series','period','end','metric','unit','currency','lineage','record','value','release','evidence','status','incomplete','late_io','cleanup'])
def test_label_integrity_alignment_and_cleanup_fail_closed(make_scoring,fault):
    b=make_scoring(horizon=1,origins=(4,8))
    def change(frame,index):
        if fault=='late_io' and index==2:raise OSError('LABEL-IO-PRIVATE-SECRET')
        if fault=='incomplete' and index>0:return None
        if fault=='duplicate' and index==0:frame.iloc[1]=frame.iloc[0]
        if fault=='extra' and index==0:frame=pd.concat([frame,frame.iloc[:1]],ignore_index=True)
        mask=frame.period_start==b['at'](8)
        if fault=='missing':return frame[~mask]
        replacements={'series':('series_id',UUID(int=999)),'period':('period_start',b['at'](11)),
            'end':('period_end',b['at'](11)),'metric':('metric','wrong'),'unit':('unit','wrong'),
            'currency':('currency','EUR'),'lineage':('lineage_id',UUID(int=999)),
            'record':('source_record_id','wrong'),'value':('value',800.),'release':('available_at',b['at'](11)),
            'evidence':('availability_reference','wrong'),'status':('value_status','predicted')}
        if fault in replacements:
            field,value=replacements[fault];frame.loc[mask,field]=value
        return frame
    label_fault(b,change,cleanup=fault=='cleanup')
    with pytest.raises(JointPredictionError) as failure:compare(b)
    assert b['state']['reads']==[0,4] and len(b['state']['calls'])==4
    assert 'PRIVATE-SECRET' not in ''.join(traceback.format_exception(failure.value))
    with b['store']._publication_session(StagingBudgets()):pass


@pytest.mark.parametrize('fault',['revised_after_E','backdated','missing','wrong_version'])
def test_independent_label_revision_authority_rejects_substitution(make_scoring,fault):
    b=make_scoring(origins=(4,8),horizon=1)
    key=next(k for k,t in b['label_ledger'].items() if t['start']==b['at'](8))
    t=b['label_ledger'][key]
    if fault in ('revised_after_E','backdated'):t['revision']=b['at'](10)
    elif fault=='missing':del b['label_ledger'][key]
    else:t['metadata']=replace(t['metadata'],manifest_digest='0'*64)
    with pytest.raises(JointPredictionError):compare(b)
    assert b['state']['reads']==[0,4]


def test_history_value_substitution_still_rejects_before_predictors(make_scoring,monkeypatch):
    b=make_scoring(horizon=1);old=_FrozenCapture.view
    def view(self):
        frame=old(self);frame.loc[frame.period_start==b['at'](3),'value']=800.;return frame
    monkeypatch.setattr(_FrozenCapture,'view',view)
    with pytest.raises(JointPredictionError):compare(b)
    assert b['state']['reads']==[0] and not b['state']['calls']


@pytest.mark.parametrize('prediction',[8.,20.,-2.])
def test_zero_positive_negative_comparison_deltas(make_scoring,prediction):
    b=make_scoring(horizon=1,origins=(4,8),raw_change=signed_reference)
    def candidate(role,req,out):
        if role=='candidate':return PredictionBatch(request_id=req.request_id,points=tuple(PredictionPoint(scope=s,value=prediction) for s in req.scopes))
        return out
    b['prediction_hook']=candidate;result=compare(b)
    # Actual -4, baseline 8: AE=12, SE=144. Candidate AE=abs(p+4).
    for delta in result.deltas:
        assert delta.sae.fraction==abs(Fraction.from_float(prediction)+4)-12
        assert delta.sse.fraction==(Fraction.from_float(prediction)+4)**2-144


def test_all_zero_truth_keeps_undefined_wape_and_signed_zero_identity(make_scoring):
    def raw(frame):
        frame.loc[frame['from']>=pd.Timestamp('2020-03-02',tz='UTC'),'reported']=-0.0
    b=make_scoring(origins=(4,8),raw_change=raw)
    result=compare(b)
    assert all(d.wape is None and d.wape_status=='undefined_zero_denominator' for d in result.deltas)
    assert all(json.loads(t.original_value_identity_json)==['float64','8000000000000000'] for t in result.truths)


def test_permuted_prediction_points_keep_new_comparison_identity(make_scoring):
    b=make_scoring(horizon=1);a=compare(b)
    b['state']['reads'].clear();b['state']['calls'].clear()
    b['prediction_hook']=lambda role,req,out:out.model_copy(update={'points':tuple(reversed(out.points))})
    assert compare(b).comparison_id==a.comparison_id


def test_overlapping_validation_origins_keep_distinct_forecast_events(make_scoring):
    b=make_scoring(horizon=2);out=compare(b)
    keys=[t.key for t in out.truths]
    assert len(keys)==12 and len({(k.series_id,k.target_period) for k in keys})<12
    assert len({k.key_id for k in keys})==12
    assert all(d.candidate.count==1 for d in out.deltas)


def test_tenants_and_folds_do_not_share_truth_or_score_groups(make_scoring):
    b=make_scoring(horizon=1,origins=(4,8),organizations=(2,3));data=b['plan'].model_dump()
    first=data['producers'][0]['plan']['folds'][0]
    second={**first,'fold_id':'fold-2','training_origins':(b['at'](5),),'validation_origins':(b['at'](10),),
        'model_fit_cutoff':b['at'](6),'assessment_cutoff':b['at'](11)}
    for p in data['producers']:p['plan']['folds']=(first,second)
    b['plan']=JointPredictionPlan.model_validate(data);replan(b,predictions=b['plan'])
    out=compare(b)
    assert b['state']['reads']==[0,0,16,16]
    assert {(t.key.organization_id.int,t.key.fold_id) for t in out.truths}=={(2,'fold-1'),(2,'fold-2'),(3,'fold-1'),(3,'fold-2')}
    assert len(out.truths)==8 and len(out.deltas)==8


def test_test_partition_and_policy_substitution_reject_without_capture(make_scoring):
    b=make_scoring(horizon=1)
    with pytest.raises(JointPredictionError):b['joint'].compare(b['comparison'].model_copy(update={'policy_digest':'0'*64}))
    data=b['comparison'].model_dump()
    for p in data['predictions']['producers']:
        f=p['plan']['folds'][0];f['test_origins']=(b['at'](11),);f['assessment_cutoff']=b['at'](12)
    with pytest.raises(ValueError):JointComparisonPlan.model_validate(data)
    assert not b['state']['reads']


@pytest.mark.parametrize('role',['candidate','baseline'])
@pytest.mark.parametrize('origin',[4,8,10])
def test_final_global_revocation_reaches_every_original_producer_scope(make_scoring,role,origin):
    b=make_scoring(horizon=1);profile=b['plan'].producers[role=='baseline'].profile_id
    def comparison(ctx,stage):
        if stage=='return':b['revoked'].add((profile,UUID(int=2),'fold-1',b['at'](origin)))
    b['comparison_hook']=comparison
    with pytest.raises(JointPredictionError):compare(b)
    assert b['state']['reads']==[0,8] and len(b['state']['calls'])==8
    context=b['state']['checks'][-1][0]
    assert (context.producer_profile_id,context.fold_id,context.forecast_origin)==(profile,'fold-1',b['at'](origin))


@pytest.mark.parametrize('permission',['comparison','evaluation','labels','revision'])
def test_late_joint_scoring_permissions_fail_closed(make_scoring,permission):
    b=make_scoring(horizon=1)
    def broad(ctx,stage):
        if stage=='return':b['permissions'][permission]=False
    b['comparison_hook']=broad
    with pytest.raises(JointPredictionError):compare(b)
    assert b['state']['reads']==[0,8]


@pytest.mark.parametrize('permission',['labels','revision','historical','read'])
def test_final_read_can_revoke_scoped_permission_but_cannot_bypass_terminal_checks(make_scoring,permission):
    b=make_scoring(horizon=1);armed=[False];old=b['provider'].require_current
    b['comparison_hook']=lambda ctx,stage:armed.__setitem__(0,stage=='return')
    def current(*a,**kw):
        result=old(*a,**kw)
        if armed[0]:
            if permission in ('labels','revision'):b['permissions'][permission]=False
            elif permission=='historical':b['revoked'].add((b['plan'].producers[1].profile_id,UUID(int=2),'fold-1',b['at'](10)))
        return result
    if permission=='read':
        b['provider'].authorize_read=lambda *a:not armed[0]
    else:b['provider'].require_current=current
    with pytest.raises(JointPredictionError):compare(b)
    assert b['state']['reads']==[0,8]


@pytest.mark.parametrize('point',['predictor','label_read','score','final_comparison','final_revision','final_label','final_evaluation'])
@pytest.mark.parametrize('kind',['cancel','deadline','secret'])
def test_cancellation_deadline_and_callback_secrets_at_all_stages(make_scoring,monkeypatch,point,kind):
    import src.services.canonical_forecast_comparison_service as module
    b=make_scoring(horizon=1,origins=(4,8));armed=[False]
    def trigger():
        if kind=='cancel':b['state']['cancelled']=True
        elif kind=='deadline':b['state']['clock']=300.
        else:
            try:raise OSError('C2-ROOT-PRIVATE-SECRET')
            except OSError as error:raise ValueError('C2-WRAPPER-PRIVATE-SECRET') from error
    b['comparison_hook']=lambda ctx,stage:armed.__setitem__(0,stage=='return')
    if point=='predictor':b['prediction_hook']=lambda role,req,out:(trigger(),out)[1]
    elif point=='label_read':
        def read(frame,i):trigger();return frame
        label_fault(b,read)
    elif point=='score':
        old=module.score_point
        def score(*a):trigger();return old(*a)
        monkeypatch.setattr(module,'score_point',score)
    elif point=='final_comparison':
        def hook(ctx,stage):
            if stage=='return':trigger()
        b['comparison_hook']=hook
    else:
        name={'final_revision':'revision_hook','final_label':'label_hook','final_evaluation':'evaluation_hook'}[point]
        def hook(*args):
            if armed[0]:trigger()
        b[name]=hook
    with pytest.raises(JointPredictionError) as failure:compare(b)
    assert 'PRIVATE-SECRET' not in ''.join(traceback.format_exception(failure.value))
    with b['store']._publication_session(StagingBudgets()):pass


@pytest.mark.parametrize('elapsed,accepted',[(299.999,True),(300.,False),(301.,False)])
def test_last_scoped_callback_exact_deadline_boundary(make_scoring,elapsed,accepted):
    b=make_scoring(horizon=1,origins=(4,8));armed=[False];last=b['plan'].producers[1].plan.digest
    b['comparison_hook']=lambda ctx,stage:armed.__setitem__(0,stage=='return')
    def last_callback(ctx,stage):
        if armed[0] and ctx.forecast_origin==b['at'](8) and ctx.producer_plan_digest==last:
            b['state']['clock']=elapsed
    b['evaluation_hook']=last_callback
    if accepted:assert compare(b).deltas
    else:
        with pytest.raises(JointPredictionError):compare(b)


def test_exact_terminal_order_claim_identity_lock_release_and_no_callbacks_after(make_scoring,monkeypatch):
    import src.services.canonical_forecast_comparison_service as module
    b=make_scoring(horizon=1,origins=(4,8));trace=[]
    b['comparison_hook']=lambda ctx,stage:trace.append(('comparison',stage))
    b['evaluation_hook']=lambda ctx,stage:trace.append(('evaluation',stage,ctx.forecast_origin))
    b['label_hook']=lambda ctx,stage:trace.append(('labels',stage,ctx.forecast_origin))
    b['revision_hook']=lambda ctx,claims:trace.append(('revision',ctx.forecast_origin,id(claims)))
    b['release_hook']=lambda ctx,claims:trace.append(('history',ctx.forecast_origin,id(claims)))
    b['authorization_hook']=lambda ctx,stage:trace.append(('rollout',ctx.forecast_origin,stage))
    old=b['provider'].require_current
    def read(*a,**kw):trace.append(('read',));return old(*a,**kw)
    b['provider'].require_current=read
    constructor=module.JointComparisonResult
    def assemble(*a,**kw):
        result=constructor(*a,**kw);trace.append(('assembled',));return result
    monkeypatch.setattr(module,'JointComparisonResult',assemble)
    def cancel():trace.append(('checkpoint',));return False
    assert b['joint'].compare(b['comparison'],cancelled=cancel).deltas
    at=trace.index(('assembled',));tail=trace[at+1:]
    assert tail[-1]==('checkpoint',)
    assert all(t[0] in ('history','rollout','revision','labels','evaluation') for t in tail[:-1])
    assert ('read',) not in tail and ('comparison','return') not in tail
    assert len([t for t in tail if t[0]=='history'])==4
    first,last=b['label_claims'][0],b['label_claims'][-1]
    assert first[0] is last[0] and first[1] is last[1]
    assert all(a is z for a,z in zip(first[1],last[1]))


@pytest.mark.parametrize('field',['max_capture_sessions','max_truth_points','max_scored_events','max_score_groups','max_arithmetic_work'])
def test_joint_scoring_admission_rejects_before_any_predictor(make_scoring,field):
    b=make_scoring(horizon=1);data=b['comparison'].model_dump();data['budgets'][field]=1
    with pytest.raises((JointPredictionError,ValueError)):
        b['comparison']=JointComparisonPlan.model_validate(data);compare(b)
    assert not b['state']['calls']


def test_second_full_read_is_reserved_before_prediction(make_scoring):
    b=make_scoring(horizon=1);data=b['plan'].model_dump();data['budgets']['max_captured_rows']=47
    b['plan']=JointPredictionPlan.model_validate(data);replan(b,predictions=b['plan'])
    with pytest.raises(JointPredictionError):compare(b)
    assert b['state']['reads']==[0] and not b['state']['calls']


@pytest.mark.parametrize('point',['second_producer','labels','arithmetic','assembly'])
def test_runtime_exhaustion_discards_all_partial_work(make_scoring,monkeypatch,point):
    b=make_scoring(horizon=1,origins=(4,8));old=_JointRequest.charge
    def charge(self,kind,amount):
        fail=((point=='second_producer' and len(self.handoffs)==1 and kind=='work')
            or (point=='labels' and self.phase=='LABELS' and kind=='decoded')
            or (point=='arithmetic' and self.phase=='SCORING' and kind=='arithmetic')
            or (point=='assembly' and self.phase=='SCORING' and kind=='output'))
        return old(self,kind,amount+self.limits[kind] if fail else amount)
    monkeypatch.setattr(_JointRequest,'charge',charge)
    with pytest.raises(JointPredictionError):compare(b)
    assert len(b['state']['calls'])==(2 if point=='second_producer' else 4)


def test_reservations_cover_measured_whole_request_and_terminal_callbacks(make_scoring,monkeypatch):
    b=make_scoring(origins=(4,8));records=[];old=_JointRequest.close
    def close(self):records.append((self.phase,dict(self.admission_base),dict(self.reservation),dict(self.used)));old(self)
    monkeypatch.setattr(_JointRequest,'close',close)
    out=compare(b);phase,base,reserved,used=records[0]
    assert phase=='TERMINAL'
    for name,estimate in reserved.items():assert used[name]-base[name]<=estimate,(name,used[name],base[name],estimate)
    assert used['captures']==2 and used['rows']==48 and used['truth']==8 and used['scores']==16
    assert used['calls']==16 and out.usage_phase=='before_terminal'
    print('C2_ACCOUNTING',used)


@pytest.mark.parametrize('numeric,variable,accepted',[(2**53+1,False,False),(-(2**53+1),False,False),(2**53+1,True,False),(2**53+2,True,True)])
def test_numeric_fidelity_regression_before_c2_predictors(tmp_path,monkeypatch,numeric,variable,accepted):
    helper=runpy.run_path('tests/training/test_deterministic_forecast_rollout.py')
    if variable:helper['variable_numeric_batch_adapter'](monkeypatch)
    def raw(frame):frame['offer']=pd.Series([1.0]*3+[numeric]*(len(frame)-3),dtype=object)
    b=make_scoring.__wrapped__(tmp_path,monkeypatch)(exo=True,horizon=1,raw_change=raw)
    c1=runpy.run_path('tests/training/test_forecast_comparison_shared_execution.py')
    cfg=c1['configure_numeric_joint'](b);replan(b,predictions=b['plan'])
    options={**b['scoring_options'],'candidate_configuration':cfg,'candidate_capabilities':b['plan'].producers[0].plan.predictor}
    b['joint']=CanonicalForecastComparisonService(**options)
    if accepted:
        result=compare(b);assert result.deltas and b['state']['reads']==[0,8]
        assert all(row[-1]==float(numeric) for role,request in b['state']['calls'] if role=='candidate' for row in request.rows)
    else:
        with pytest.raises(JointPredictionError):compare(b)
        assert not b['state']['calls'] and b['state']['reads']==[0]


def test_seasonal_baseline_scored_against_identical_truth(make_scoring):
    b=make_scoring(season=2,origins=(4,8),raw_change=signed_reference)
    out=compare(b);north=next(s.series_id for s in b['metadata'][0].series if s.dimensions['location']=='north')
    base=[e for e in out.producers[1].evaluation.events if e.scope.series_id==north]
    assert [e.statistics.sae.fraction for e in base]==[2,8,8,6]
    assert sum(e.statistics.sse.fraction for e in base)==168


def test_event_weighted_grouping_requires_fixed_bootstrap_policy(make_scoring):
    policy=JointComparisonPolicy(policy_id='synthetic-approved-event-weighting',event_weighting='forecast_event_weighted_v1',
        arithmetic=ScoringArithmeticPolicy(policy_id='synthetic-exact'))
    b=make_scoring(policy=policy,horizon=2);out=compare(b)
    assert len(out.deltas)==4 and all(d.candidate.count==3 for d in out.deltas)
    assert all(g.forecast_origin is None for p in out.producers for g in p.evaluation.groups)
    assert sum(d.candidate.count for d in out.deltas)==len(out.truths)==12


def test_scoring_operand_limits_fail_without_partial_output(make_scoring):
    policy=JointComparisonPolicy(policy_id='synthetic-tight-operand',
        arithmetic=ScoringArithmeticPolicy(policy_id='synthetic-small-arithmetic',max_operand_bits=2,max_accumulator_bits=2))
    b=make_scoring(policy=policy,horizon=1)
    with pytest.raises(JointPredictionError):compare(b)
    assert b['state']['reads']==[0,8]


def test_assessment_cutoff_must_be_in_trusted_past_before_label_read(make_scoring):
    b=make_scoring(horizon=1);data=b['plan'].model_dump()
    for producer in data['producers']:producer['plan']['folds'][0]['assessment_cutoff']=pd.Timestamp('2100-01-01',tz='UTC')
    b['plan']=JointPredictionPlan.model_validate(data);replan(b,predictions=b['plan'])
    with pytest.raises(JointPredictionError):compare(b)
    assert not b['state']['reads'] and not b['state']['calls']


@pytest.mark.parametrize('authority',['calibration','external'])
def test_calibration_and_external_evidence_are_reverified_before_handoff(make_scoring,authority):
    b=make_scoring(horizon=1,exo=authority=='external',external=authority=='external')
    revoked=[False]
    def predict(role,request,result):
        if role=='baseline' and request.compatibility.forecast_origin==b['at'](10):revoked[0]=True
        return result
    if authority=='external':
        b['prediction_hook']=predict;b['external_hook']=lambda *args:not revoked[0]
    else:
        b['label_hook']=lambda ctx,stage:revoked.__setitem__(0,stage=='after_read')
        def release(ctx,claims):
            if revoked[0] and ctx.target_period_end is None:raise ValueError('PRIVATE-CALIBRATION-SECRET')
        b['release_hook']=release
    with pytest.raises(JointPredictionError):compare(b)
    assert b['state']['reads']==[0,8]


@pytest.mark.parametrize('scope',['organization','fold'])
def test_later_scope_revocation_rejects_all_comparison_results(make_scoring,scope):
    b=make_scoring(horizon=1,origins=(4,8),organizations=(2,3))
    if scope=='fold':
        data=b['plan'].model_dump();first=data['producers'][0]['plan']['folds'][0]
        second={**first,'fold_id':'fold-2','training_origins':(b['at'](5),),'validation_origins':(b['at'](10),),
            'model_fit_cutoff':b['at'](6),'assessment_cutoff':b['at'](11)}
        for p in data['producers']:p['plan']['folds']=(first,second)
        b['plan']=JointPredictionPlan.model_validate(data);replan(b,predictions=b['plan'])
    profile=b['plan'].producers[1].profile_id;fold='fold-2' if scope=='fold' else 'fold-1';origin=b['at'](10 if scope=='fold' else 8)
    def broad(ctx,stage):
        if stage=='return':b['revoked'].add((profile,UUID(int=3),fold,origin))
    b['comparison_hook']=broad
    with pytest.raises(JointPredictionError):compare(b)
    ctx,claims=b['state']['checks'][-1]
    assert ctx.metadata.reference.organization_id==UUID(int=3) and ctx.fold_id==fold and ctx.forecast_origin==origin
    assert len(b['state']['reads'])==4


def test_result_binding_rejects_cross_period_truth_substitution(make_scoring):
    b=make_scoring(horizon=1);out=compare(b);data=out.model_dump()
    truth=data['truths'];truth[0]['label_binding_digest'],truth[1]['label_binding_digest']=truth[1]['label_binding_digest'],truth[0]['label_binding_digest']
    with pytest.raises(ValueError):JointComparisonResult.model_validate(data)


def test_zero_new_authorities_cannot_enable_compare_on_legacy_c1(make_scoring):
    b=make_scoring(horizon=1);legacy=CanonicalForecastComparisonService(**b['options'])
    with pytest.raises(JointPredictionError):legacy.compare(b['comparison'])
    assert not b['state']['reads']


def test_missing_future_labels_are_not_fabricated(make_scoring):
    # The C1 fixture has observations only through period 11. H4 at origin 10
    # legitimately predicts periods 12/13, but cannot score absent truth.
    b=make_scoring()
    with pytest.raises(JointPredictionError):compare(b)
    assert b['state']['reads']==[0,32] and len(b['state']['calls'])==32


def test_causal_rollout_does_not_feed_validation_actuals_into_feedback(make_scoring):
    b=make_scoring(origins=(4,8),raw_change=signed_reference)
    compare(b)
    requests=[req for role,req in b['state']['calls'] if role=='candidate' and req.compatibility.forecast_origin==b['at'](8)]
    for req,want in zip(requests,[8.,-2.,0.,6.]):
        assert all(row[0]==want for row in req.rows)


def test_decimal_signed_zero_claim_identity_is_exact_but_not_authority(make_scoring):
    from src.services.canonical_forecast_evaluation_service import _verified_label_claim
    from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
    b=make_scoring(horizon=1,origins=(4,8));out=compare(b)
    ctx,claims=b['label_claims'][0];old=claims[0];meta=ctx.metadata
    event=next(e for e in out.producers[0].evaluation.events if e.scope.series_id==old.series_id)
    row={'period_end':old.period_end,'metric':old.metric,'unit':old.unit,'currency':old.currency,'frequency':event.scope.frequency,
        'value_status':'actual','available_at':old.available_at,'availability_reference':old.evidence_reference,
        'source_record_id':old.source_record_id,'lineage_id':old.lineage_id,'value':Decimal('-0.0000')}
    claim=_verified_label_claim(row,event.scope,meta,b['plan'].producers[0].plan.datasets[0],ctx.assessment_cutoff,
        CanonicalFeaturePipeline(b['configuration']))
    assert claim.value_identity==('decimal',1,(0,),-4)
    # A correct arithmetic identity cannot authorize substituted canonical truth.
    replaced=tuple(claim if c is old else c for c in claims)
    assert b['scoring_options']['label_revision_authority'].verify(ctx,replaced) is False


def test_common_truth_denominator_discrepancy_is_integrity_failure(make_scoring,monkeypatch):
    import src.services.canonical_forecast_comparison_service as module
    from src.data.processing.forecast_evaluation import statistics
    b=make_scoring(horizon=1,origins=(4,8));old=module.group_scores;calls=[]
    def group(events,plan,checkpoint):
        out=old(events,plan,checkpoint);calls.append(True)
        if len(calls)==1:
            a=out[0];s=a.statistics
            changed=statistics(s.count,s.sae.fraction,s.sse.fraction,s.saa.fraction+1,plan.arithmetic)
            out=(a.model_copy(update={'statistics':changed}),)+out[1:]
        return out
    monkeypatch.setattr(module,'group_scores',group)
    with pytest.raises(JointPredictionError):compare(b)
    assert len(calls)==2


def test_label_release_after_E_rejects_without_silent_filtering(make_scoring):
    def raw(frame):frame.loc[frame['from']==pd.Timestamp('2020-03-02',tz='UTC'),'released']=pd.Timestamp('2020-03-16',tz='UTC')
    b=make_scoring(horizon=1,origins=(4,8),raw_change=raw)
    with pytest.raises(JointPredictionError):compare(b)
    assert b['state']['reads']==[0,4]


@pytest.mark.parametrize('frequency,timezone,start,expected',[
    ('daily','America/New_York','2020-02-29',('2020-03-08T00:00:00-05:00','2020-03-09T00:00:00-04:00')),
    ('weekly','UTC','2020-11-02',('2020-12-28T00:00:00+00:00','2021-01-04T00:00:00+00:00'))])
def test_scored_grid_retains_exact_dst_and_iso_periods(make_scoring,frequency,timezone,start,expected):
    b=make_scoring(horizon=2,origins=(4,8),frequency=frequency,timezone=timezone,start=start)
    out=compare(b)
    north=next(s.series_id for s in b['metadata'][0].series if s.dimensions['location']=='north')
    assert tuple(t.key.target_period.isoformat() for t in out.truths if t.key.series_id==north)==expected
