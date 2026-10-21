"""C1 trusted process-local one-shot consumption, shared labels, accepted scoring.

The bootstrap must be pinned by the operator and retain the returned operator.
Python encapsulation is not authentication. Callbacks, provider and clocks are
host-owned. Fresh sequential checks are not atomic revocation leases; elapsed,
size and arithmetic budgets are cooperative, not hard CPU/RSS enforcement.
No native producer operation is invoked by this module.
"""
from dataclasses import dataclass, replace
from threading import Lock
from uuid import uuid4

from src.data.contracts.backtesting import identity
from src.data.contracts.dataset import StagingBudgets
from src.data.contracts.temporal_preparation import TemporalDatasetBinding
from src.data.contracts.model_joint_evaluation import JointH1ScoringBudgets, JointH1EvaluationResult
from src.data.contracts.forecast_evaluation import (
    ScoringArithmeticPolicy, EvaluationBudgets, ForecastScoringPlan, ScoredEvent, ForecastEvaluationResult)
from src.data.contracts.forecast_comparison import JointScoreDelta, SignedRatio
from src.data.processing.forecast_evaluation import score_point, group_scores, grouping_key, bounded
from src.services.canonical_forecast_evaluation_service import _capture_label_rows, _verified_label_claim
from src.services.canonical_baseline_evaluation_service import BaselineAuthorityBinding, _authority
from src.services.canonical_model_prediction_service import _guard, _continue


class JointLabelUse:
    __slots__=()
    def __reduce__(self):raise TypeError('label capabilities are process-local')


@dataclass(frozen=True,slots=True)
class JointLabelContext:
    seal_id: str
    eligibility_id: str
    evaluation_publication: object
    scopes: tuple
    assessment_cutoff: object
    label_use_id: str | None
    forecast_origin: object = None
    purpose: str = 'offline_forecast_evaluation'


class _EvaluationOwner:
    def __init__(self,joint,session,*,authorize_labels,label_revision_authority,budgets=None):
        from src.services.canonical_joint_model_prediction_service import _JointOwner,SealedJointH1Session
        if (type(joint) is not _JointOwner or type(session) is not SealedJointH1Session
                or session is not joint.session or not callable(authorize_labels)):
            raise ValueError()
        # Guard typed bootstrap declarations BEFORE the legacy authority helper's
        # plain serialization. Never let malformed model_construct() values warn.
        if (type(label_revision_authority) is not BaselineAuthorityBinding
                or type(label_revision_authority.datasets) is not tuple or len(label_revision_authority.datasets)!=1):raise ValueError()
        binding=label_revision_authority.datasets[0]
        if type(binding) is not TemporalDatasetBinding:raise ValueError()
        binding=TemporalDatasetBinding.model_validate(binding.model_dump(warnings='error'))
        label_revision_authority=replace(label_revision_authority,datasets=(binding,))
        scopes,revision=_authority(label_revision_authority,'captured_value_as_of_E_v1')
        if scopes!=(joint.declaration.relationship.evaluation.model_dump_json(),):raise ValueError()
        self.joint=joint;self.session=session;self.authorize=authorize_labels;self.revision=revision
        self.authority=label_revision_authority.authority_id
        self.authority_version=label_revision_authority.authority_version
        self.authority_digest=identity([self.authority,self.authority_version,'captured_value_as_of_E_v1',scopes])
        self.lock=Lock();self.status='BOUND';self.handle=None;self.use_id=None
        self.budgets=JointH1ScoringBudgets.model_validate(budgets if budgets is not None else JointH1ScoringBudgets())
        # Provider, metadata, engine and clock come ONLY from the original owner.
        self.provider=joint.model.provider;self.engine=joint.model.engine;self.meta=joint.shared.receipt.metadata
        self.result=joint.verify(session);self.cancelled=_continue;self.expires=None;self.start=None
        self.used={k:0 for k in ('captures','rows','decoded','intermediate','work','arithmetic')}

    def check(self):
        stop=self.cancelled();now=self.joint.model._now()
        if (type(stop) is not bool or stop or self.status in ('FAILED','REVOKED','CANCELLED','TERMINAL')
                or self.expires is not None and now>=self.expires
                or self.start is not None and now-self.start>=self.budgets.max_elapsed_seconds):raise ValueError()
        self.joint.check()

    def context(self,origin=None):
        scopes=self.result.coverage.scopes
        if origin is not None:
            scopes=tuple(s for s in scopes if s.forecast_origin==origin)
            if not scopes:raise ValueError()
        return JointLabelContext(self.result.seal_id,self.result.eligibility.eligibility_id,
            self.result.eligibility.relationship.evaluation,scopes,
            self.result.eligibility.candidate.training_plan.folds[0].assessment_cutoff,self.use_id,origin)

    def approved(self,stage,origin=None):
        self.check()
        if self.authorize(self.context(origin),stage) is not True:raise ValueError()
        self.check()

    def fresh(self):
        self.check()
        from pathlib import Path
        import subprocess
        root=Path(__file__).resolve().parents[3]
        for name in ('src/services/canonical_joint_model_evaluation_service.py','src/data/contracts/model_joint_evaluation.py'):
            path='sales-forecast-platform/'+name
            committed=subprocess.check_output(['git','cat-file','blob',self.joint.model.runtime.source_commit+':'+path],
                cwd=root,timeout=5,stderr=subprocess.PIPE)
            if committed!=(root/path).read_bytes():raise ValueError()
        if self.joint.verify(self.session).result_id!=self.result.result_id:raise ValueError()
        self.provider.require_current(self.meta,budgets=self.read_budgets())
        self.check()

    def gate(self):
        # Clock/cancellation callbacks precede the original B0 liveness gate.
        # After it, inspect only local state; this remains sequential, not a lease.
        self.joint.model._gate(self.check,self.joint.model.record,sealed=True)
        if self.status not in ('READY','RUNNING') or self.joint.phase!='SEALED':raise ValueError()

    def read_budgets(self):
        return StagingBudgets(max_rows=self.budgets.max_label_rows,max_elapsed_seconds=self.budgets.max_elapsed_seconds)

    def charge(self,kind,amount):
        if kind not in self.used or type(amount) is not int or amount<0:raise ValueError()
        self.used[kind]+=amount
        limits={'captures':1,'rows':self.budgets.max_label_rows,'decoded':self.budgets.max_label_bytes,
            'intermediate':self.budgets.max_representation_bytes,'work':self.budgets.max_work,
            'arithmetic':self.budgets.max_arithmetic_units}
        if self.used[kind]>limits[kind]:raise ValueError()
        self.check()

    def issue(self,*,cancelled=_continue):
        if not self.lock.acquire(blocking=False):raise ValueError()
        try:
            if self.status!='BOUND' or not callable(cancelled):raise ValueError()
            self.cancelled=cancelled;self.start=self.joint.model._now()
            self.expires=self.start+self.budgets.max_elapsed_seconds
            self.fresh();self.approved('issue')
            self.handle=JointLabelUse();self.use_id=identity(['joint_label_use_v1',self.result.seal_id,self.authority_digest,uuid4().hex])
            self.status='READY';return self.handle
        except Exception:
            self.status='FAILED';raise
        finally:self.lock.release()

    def stop(self,handle,status):
        if type(handle) is not JointLabelUse or handle is not self.handle or self.status not in ('READY','RUNNING'):raise ValueError()
        self.status=status

    def revoke(self,handle):self.stop(handle,'REVOKED')
    def cancel(self,handle):self.stop(handle,'CANCELLED')

    def evaluate(self,handle):
        if not self.lock.acquire(blocking=False):raise ValueError()
        claimed=False;admitted=False
        try:
            if type(handle) is not JointLabelUse or handle is not self.handle or self.status!='READY':raise ValueError()
            admitted=True;self.status='RUNNING';self.check()
            # Atomic against ALL consumers, including separately bootstrapped ones.
            self.joint.claim_consumption(self.session,self);claimed=True
            self.fresh()
            for period in self.result.eligibility.coverage.periods:self.approved('read',period.forecast_origin)
            self.fresh();self.gate()
            r=self.result;b=self.budgets;n=len(r.coverage.scopes)
            if n>b.max_points or n>b.max_groups or self.meta.rows>b.max_label_rows:raise ValueError()
            arithmetic=ScoringArithmeticPolicy(policy_id='canonical-exact-c1-v1')
            bits=arithmetic.max_accumulator_bits
            # Reserve cumulative arithmetic before label access, bounded integer work.
            self.charge('arithmetic',1024*n*bits*bits)
            self.joint.consumption_transition(self,'CONSUMPTION_CLAIMED','LABELS_AUTHORIZED')
            ctx=self.context();cutoff=self.engine._time(ctx.assessment_cutoff)
            if self.engine._time(self.provider.clock())<cutoff:raise ValueError()
            binding=r.eligibility.relationship.evaluation
            wanted={(s.organization_id,s.series_id,self.engine._time(s.target_period)) for s in r.coverage.scopes}
            if len(wanted)!=n:raise ValueError()
            self.gate()
            rows=_capture_label_rows(self.provider,(binding,),{self.meta.reference.organization_id:self.meta},
                wanted,self.engine,self.read_budgets(),self.check,self.charge)
            self.joint.consumption_transition(self,'LABELS_AUTHORIZED','LABELS_CAPTURED')
            self.fresh();self.approved('after_read')
            claims=tuple(_verified_label_claim(rows[(s.organization_id,s.series_id,self.engine._time(s.target_period))],
                s,self.meta,binding,cutoff,self.engine) for s in r.coverage.scopes)
            self.verify_revision(claims)
            # The independent revision collaborator may revoke another authority.
            self.fresh();self.gate()
            evaluations=[];maps=[]
            for handoff,points in ((r.model_handoff_id,tuple(e.point for e in r.model.events)),
                                   (r.baseline_handoff_id,r.baseline_points)):
                plan=ForecastScoringPlan(plan_id='joint-h1-c1',producer_plan_digest=handoff,datasets=(binding,),
                    partitions=('validation',),arithmetic=arithmetic,
                    budgets=EvaluationBudgets(max_scored_events=b.max_points,max_groups=b.max_groups))
                events=[];members={}
                for i,(point,claim) in enumerate(zip(points,claims)):
                    self.charge('work',1)
                    actual=rows[(point.scope.organization_id,point.scope.series_id,self.engine._time(point.scope.target_period))]['value']
                    event=ScoredEvent(scope=point.scope,producer_event_id=identity([handoff,i,point.model_dump(mode='json',warnings='error')]),
                        label_binding_digest=claim.digest,scoring_plan_digest=plan.digest,arithmetic_policy_digest=arithmetic.digest,
                        assessment_cutoff=cutoff,partition='validation',statistics=score_point(point.value,actual,arithmetic))
                    self.charge('intermediate',len(event.model_dump_json().encode()));events.append(event)
                    members.setdefault(grouping_key(event,plan),[]).append(identity(point.scope.model_dump(mode='json')))
                groups=group_scores(tuple(events),plan,lambda:self.charge('work',1))
                mapping={}
                for group in groups:
                    key=(group.fold_id,str(group.organization_id),str(group.series_id),group.horizon_step,group.metric,
                        group.unit,group.currency or '',group.partition,group.assessment_cutoff.isoformat(),group.forecast_origin.isoformat())
                    mapping[key]=(group,tuple(sorted(members[key])))
                evaluation=ForecastEvaluationResult(scoring_plan=plan,producer_result_id=handoff,events=tuple(events),groups=groups)
                self.charge('intermediate',len(evaluation.model_dump_json().encode()))
                evaluations.append(evaluation);maps.append(mapping)
            if tuple(maps[0])!=tuple(maps[1]):raise ValueError()
            def signed(value):
                bounded(value,arithmetic)
                return SignedRatio(numerator=str(value.numerator),denominator=str(value.denominator))
            deltas=[]
            for key in maps[0]:
                a,ids=maps[0][key];bb,other=maps[1][key]
                if ids!=other:raise ValueError()
                sa,sb=a.statistics,bb.statistics
                deltas.append(JointScoreDelta(semantic_group_id=identity(['joint_h1_c1',arithmetic.digest,key,ids]),
                    forecast_key_ids=ids,candidate=sa,baseline=sb,
                    **{field:signed(getattr(sa,field).fraction-getattr(sb,field).fraction) for field in ('sae','sse','mae','mse')},
                    wape_status=sa.wape_status,wape=None if sa.wape is None else signed(sa.wape.fraction-sb.wape.fraction)))
            output=JointH1EvaluationResult(sealed=r,label_use_id=self.use_id,label_authority_id=self.authority,label_authority_version=self.authority_version,
                label_authority_digest=self.authority_digest,label_revision_ids=tuple(c.digest for c in claims),model_scores=evaluations[0],baseline_scores=evaluations[1],deltas=tuple(deltas))
            encoded=output.model_dump_json(warnings='error')
            self.charge('intermediate',len(encoded.encode()))
            if len(encoded.encode())>b.max_output_bytes:raise ValueError()
            self.joint.consumption_transition(self,'LABELS_CAPTURED','SCORED')
            # Broad callbacks first, then exact label scopes and C0's original
            # per-origin return scopes. None of these reexecutes a producer.
            self.fresh();self.approved('return');self.verify_revision(claims)
            if self.engine._time(self.provider.clock())<cutoff:raise ValueError()
            for period in self.result.eligibility.coverage.periods:self.approved('scoped_return',period.forecast_origin)
            self.fresh();self.gate()
            self.joint.consumption_transition(self,'SCORED','TERMINAL')
            self.status='TERMINAL';return output
        except Exception:
            if admitted:
                if claimed:
                    self.joint.consumption_transition(self,self.joint.consumption_state,'TERMINAL')
                self.status='FAILED'
            raise
        finally:self.lock.release()

    def verify_revision(self,claims):
        self.check()
        if self.revision(self.context(),claims) is not True:raise ValueError()
        self.charge('intermediate',sum(len(repr(c).encode()) for c in claims))


class JointLabelOperator:
    __slots__=('__owner',)
    def __init__(self,owner):self.__owner=owner
    def issue(self,*a,**kw):return _guard(self.__owner.issue,*a,**kw)
    def revoke(self,*a,**kw):return _guard(self.__owner.revoke,*a,**kw)
    def cancel(self,*a,**kw):return _guard(self.__owner.cancel,*a,**kw)


class JointH1Evaluator:
    __slots__=('__owner',)
    def __init__(self,owner):self.__owner=owner
    def evaluate(self,*a,**kw):return _guard(self.__owner.evaluate,*a,**kw)


def _bootstrap_evaluation(joint,session,**composition):
    owner=_EvaluationOwner(joint,session,**composition)
    return JointLabelOperator(owner),JointH1Evaluator(owner)
