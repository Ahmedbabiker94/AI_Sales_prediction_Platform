"""C0 original-owner joint H1 prediction, never label capture or scoring.

Trusted bootstrap retains operators and pins executors/verifiers. Python is not
an authentication sandbox. All checks are sequential; clocks/providers must be
protected. Native calls are indivisible and budgets are cooperative, not OS limits.
"""
from dataclasses import dataclass
from pathlib import Path
import subprocess
from threading import Lock
from uuid import uuid4

import pandas as pd

from src.data.contracts.backtesting import identity,ForecastScope
from src.data.contracts.forecast_evaluation import BaselineDefinition
from src.data.contracts.model_evaluation import ModelEvaluationEligibility
from src.data.contracts.model_joint_prediction import JointH1Budgets,JointValidationH1Coverage,SealedJointH1Result
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
from src.features.forecast_baselines import build_baseline_profile,_owned_stateless_h1
from src.services.canonical_forecast_comparison_service import _FrozenCapture
from src.services.canonical_model_prediction_service import _Owner,_guard,_continue
from src.services.canonical_training_preparation_service import CanonicalTrainingPreparationService


@dataclass(frozen=True,slots=True)
class JointH1Context:
    eligibility_json: str
    baseline_json: str
    joint_use_id: str | None
    capture_id: str | None
    forecast_origin: object = None
    purpose: str = 'offline_forecast_evaluation'


class JointH1Use:
    __slots__=()
    def __reduce__(self):raise TypeError('joint capabilities are process-local')


class SealedJointH1Session:
    """Original owner-issued reference. A public result or digest is not a session."""
    __slots__=()
    def __reduce__(self):raise TypeError('joint sessions are process-local')


def _fingerprint(points):return identity([p.model_dump(mode='json',warnings='error') for p in points])


class _BaselineOwner:
    def __init__(self,joint,definition,configuration,authorize):
        self.joint=joint;self.authorize=authorize
        self.profile=build_baseline_profile(definition,configuration)
        self.original=self.profile;self.snapshot=tuple(vars(self.profile).items())
        self.owner_id=identity(['c0_baseline_owner_v1',uuid4().hex,self.profile.definition.digest])
        self.record=None;self.state='READY'

    def permits(self,profile):
        return (profile is self.original and tuple(vars(profile).items())==self.snapshot
            and self.state=='RUNNING' and self.joint.phase=='BASELINE')

    def approved(self,stage,origin=None):
        self.joint.check()
        if self.authorize(self.joint.context(origin),stage) is not True:raise ValueError()
        self.joint.check()

    def execute(self,shared,model_result):
        if self.record is not None or self.state!='READY' or shared is not self.joint.shared:raise ValueError()
        self.state='RUNNING';self.approved('execute');points=[];histories=[];terminals=[]
        for period in self.joint.declaration.coverage.periods:
            origin=self.joint.model.engine._time(period.forecast_origin)
            self.joint.model._joint_verify(shared,model_result,cancelled=self.joint.cancelled,origin=origin)
            self.approved('origin',origin)
            frame=shared.history(origin)
            model=self.joint.model
            claims,_=CanonicalTrainingPreparationService._availability(None,frame,model.engine,self.joint.check)
            ctx=model._context(shared.frozen.metadata,origin,pd.Timestamp(period.target_period_end))
            if model.history(ctx,claims) is not True:raise ValueError()
            history_id=identity([self.joint.declaration.eligibility_id,origin.isoformat(),[repr(c) for c in claims]])
            events=tuple(e for e in model_result.events if e.point.scope.forecast_origin==origin)
            if any(e.history_identity!=history_id for e in events):raise ValueError()
            requests=shared.requests()
            scopes=tuple(e.point.scope for e in events)
            self.joint.charge('baseline',len(frame)*(len(frame.columns)+len(scopes)+len(frame).bit_length())+len(scopes)*self.profile.definition.lookback)
            self.joint.charge('assembly',sum(len(repr(c).encode()) for c in claims))
            self.joint.model._gate(self.joint.check,self.joint.model.record,sealed=True)
            batch=_owned_stateless_h1(self.profile,self,frame,requests,scopes,origin)
            points.extend(batch);histories.extend(history_id for _ in batch);terminals.append((ctx,claims))
        value=tuple(points);self.record=(value,_fingerprint(value),tuple(histories),tuple(terminals),shared)
        self.state='SEALED'
        return value

    def seal(self,shared,points):
        if (self.state!='SEALED' or self.record is None or points is not self.record[0]
                or shared is not self.record[4] or _fingerprint(points)!=self.record[1]
                or self.profile is not self.original or tuple(vars(self.profile).items())!=self.snapshot):raise ValueError()
        return self.record[1]

    def verify(self,shared,points,*,broad):
        self.seal(shared,points)
        if broad:self.approved('verify')
        else:
            for ctx,claims in self.record[3]:
                self.joint.check()
                if self.joint.model.history(ctx,claims) is not True:raise ValueError()
                self.approved('scoped_return',ctx.forecast_origin)


@dataclass(frozen=True,slots=True)
class _CaptureReceipt:
    metadata: object
    capture_id: str


class _JointExecution:
    """One original coordinator/owner pair. Not minted from a public capture ID."""
    def __init__(self,issuer):
        self.issuer=issuer;self.frozen=None;self.record=None;self.definitions=None;self.receipt=None
    def permits_model(self,model):
        return self.issuer.shared is self and self.issuer.model is model and self.issuer.phase=='MODEL'
    def capture(self,model,record,meta,frame,definitions):
        if not self.permits_model(model) or self.receipt is not None or record is not model.record:raise ValueError()
        self.record=record
        self.frozen=_FrozenCapture.freeze(meta,frame,model.engine.budgets.max_cell_bytes,self.issuer.charge)
        self.receipt=_CaptureReceipt(meta,self.frozen.capture_id)
        self.definitions=dict(definitions)
        # Admission is complete BEFORE _Owner proceeds to native loading.
        self.issuer.admit_observed(len(frame),len(frame.columns))
        self.issuer.charge('assembly',self.frozen.decoded_bytes)
        return self.frozen.view()
    def history(self,origin):
        if self.issuer.phase!='BASELINE' or self.frozen is None:raise ValueError()
        self.issuer.charge('assembly',2*self.frozen.decoded_bytes)
        frame=self.frozen.view()
        return frame[(frame.period_end<=origin)&(frame.available_at<=origin)].copy()
    def release(self):
        self.frozen=None;self.definitions=None

    def requests(self):
        d=self.issuer.declaration;meta=self.frozen.metadata
        return pd.DataFrame([dict(organization_id=meta.reference.organization_id,series_id=s,
            metric=d.relationship.metric,unit=d.relationship.unit,currency=d.relationship.currency,
            frequency=d.relationship.frequency,**self.definitions[s].dimensions) for s in d.relationship.evaluation.series_ids])


class _JointOwner:
    def __init__(self,current,material,*,baseline,authorize_joint,authorize_baseline,joint_budgets=None,**composition):
        if not all(callable(c) for c in (authorize_joint,authorize_baseline)):raise ValueError()
        if type(baseline) is not BaselineDefinition:raise ValueError()
        definition=BaselineDefinition.model_validate(baseline.model_dump(warnings='error'))
        if definition.supported_horizon!=1:raise ValueError()
        self.definition_json=definition.model_dump_json(warnings='error')
        value=composition.get('eligibility')
        if type(value) is not ModelEvaluationEligibility:raise ValueError()
        checked=ModelEvaluationEligibility.model_validate(value)
        self.declaration=ModelEvaluationEligibility.model_validate_json(checked.model_dump_json(warnings='error'))
        self.encoded=self.declaration.model_dump_json(warnings='error')
        limits=joint_budgets if joint_budgets is not None else JointH1Budgets()
        if type(limits) is not JointH1Budgets:raise ValueError()
        self.budgets=JointH1Budgets.model_validate(limits)
        self.authorize=authorize_joint;self.phase='BOUND';self.busy=Lock();self.shared=None
        self.use=None;self.use_id=None;self.model_use=None;self.session=None;self.result=None;self.encoded_result=None
        self.model_result=None;self.baseline_points=None
        self.used={'assembly':0,'baseline':0};self.verifications=0;self.cancelled=_continue;self.start=None
        self.baseline=_BaselineOwner(self,definition,self.declaration.candidate.representation.configuration,authorize_baseline)
        self.admit_declared()
        original=composition.get('authorize_evaluation')
        if not callable(original):raise ValueError()
        def authorize(ctx,stage):
            self.source_current(material[4]);self.approved('model_'+stage,ctx.forecast_origin)
            return original(ctx,stage)
        self.model=_Owner(current,material,**{**composition,'authorize_evaluation':authorize})

    @staticmethod
    def source_current(runtime):
        root=Path(__file__).resolve().parents[3]
        for name in ('src/services/canonical_joint_model_prediction_service.py','src/data/contracts/model_joint_prediction.py',
                     'src/features/forecast_baselines.py'):
            path='sales-forecast-platform/'+name
            if subprocess.check_output(['git','cat-file','blob',runtime.source_commit+':'+path],cwd=root,timeout=5,stderr=subprocess.PIPE)!=(root/path).read_bytes():raise ValueError()

    def context(self,origin=None):
        return JointH1Context(self.encoded,self.definition_json,self.use_id,
            self.shared.receipt.capture_id if self.shared is not None and self.shared.receipt is not None else None,origin)

    def approved(self,stage,origin=None):
        # During trusted constructor model/clock do not yet exist.
        if hasattr(self,'model'):self.check()
        if self.authorize(self.context(origin),stage) is not True:raise ValueError()
        if hasattr(self,'model'):self.check()

    def charge(self,kind,amount):
        if type(amount) is not int or amount<0 or kind not in self.used:raise ValueError()
        self.used[kind]+=amount
        limit=self.budgets.max_snapshot_bytes if kind=='assembly' else self.budgets.max_baseline_work
        if self.used[kind]>limit:raise ValueError()
        self.check()

    def check(self):
        if self.phase in ('FAILED','REVOKED','CANCELLED'):raise ValueError()
        stop=self.cancelled();now=self.model._now()
        if (type(stop) is not bool or stop or self.start is not None and now-self.start>=self.declaration.resources.max_elapsed_seconds
                or self.model.record is not None and now>=self.model.record.expires):raise ValueError()

    def admit_declared(self):
        d=self.declaration;n=len(d.coverage.periods)*len(d.relationship.series)
        # Bound baseline/calendar work and both ordered outputs before any load.
        lag=BaselineDefinition.model_validate_json(self.definition_json).lookback
        if n*lag>self.budgets.max_baseline_work or n>512:raise ValueError()
        estimate=len(self.encoded.encode())+4096
        for period in d.coverage.periods:
            for series in d.relationship.series:
                scope=ForecastScope(plan_digest=d.candidate.training_plan_digest,dataset_version=d.relationship.evaluation.reference.version_id,
                    fold_id=d.coverage.fold_id,organization_id=d.relationship.evaluation.reference.organization_id,series_id=series.series_id,
                    metric=d.relationship.metric,unit=d.relationship.unit,currency=d.relationship.currency,frequency=d.relationship.frequency,
                    timezone=d.relationship.timezone,dimensions=series.dimensions,forecast_origin=period.forecast_origin,horizon_step=1,
                    target_period=period.target_period,target_period_end=period.target_period_end)
                estimate+=3*len(scope.model_dump_json().encode())+2048
        if estimate>self.budgets.max_output_bytes:raise ValueError()

    def admit_observed(self,rows,columns):
        periods=len(self.declaration.coverage.periods)
        if rows*(columns+len(self.declaration.relationship.series)+rows.bit_length())*periods+periods*len(self.declaration.relationship.series)*self.baseline.profile.definition.lookback>self.budgets.max_baseline_work:
            raise ValueError()

    def issue(self,*,cancelled=_continue):
        if not self.busy.acquire(blocking=False):raise ValueError()
        admitted=False
        try:
            if self.phase!='BOUND':raise ValueError()
            admitted=True
            if not callable(cancelled):raise ValueError()
            self.cancelled=cancelled;self.start=self.model._now();self.check();self.approved('issue')
            self.model_use=self.model.issue(cancelled=self.cancelled)
            self.use=JointH1Use();self.use_id=identity(['joint_h1_use_v1',uuid4().hex,self.declaration.eligibility_id])
            self.phase='READY';self.check();return self.use
        except Exception:
            if admitted:self.fail()
            raise
        finally:self.busy.release()

    def fail(self,status='FAILED'):
        self.phase=status
        if self.shared is not None:self.shared.release()
        self.model._joint_stop(self.shared,status)

    def stop(self,handle,status):
        if (type(handle) not in (JointH1Use,SealedJointH1Session) or handle is not self.use and handle is not self.session
                or self.phase in ('FAILED','REVOKED','CANCELLED')):raise ValueError()
        self.fail(status)

    def revoke(self,handle):self.stop(handle,'REVOKED')
    def cancel(self,handle):self.stop(handle,'CANCELLED')

    def execute(self,handle,*,cancelled=_continue):
        if not self.busy.acquire(blocking=False):raise ValueError()
        admitted=False
        try:
            if type(handle) is not JointH1Use or handle is not self.use or self.phase!='READY':raise ValueError()
            admitted=True
            if not callable(cancelled):raise ValueError()
            initial=self.cancelled
            def combined():
                first=initial();second=cancelled()
                if type(first) is not bool or type(second) is not bool:raise ValueError()
                return first or second
            self.cancelled=combined
            self.check();self.approved('execute');self.admit_declared()
            self.phase='MODEL';self.shared=_JointExecution(self)
            model_result=self.model._joint_predict(self.shared,self.model_use,cancelled=self.cancelled)
            self.model_result=model_result
            model_id=self.model._joint_seal(self.shared,model_result)
            self.phase='BASELINE';points=self.baseline.execute(self.shared,model_result)
            self.baseline_points=points
            baseline_id=self.baseline.seal(self.shared,points)
            coverage=JointValidationH1Coverage(scopes=tuple(e.point.scope for e in model_result.events))
            seal=identity(['joint_h1_seal_v1',self.use_id,self.shared.receipt.capture_id,model_id,baseline_id,self.baseline.owner_id])
            self.result=SealedJointH1Result(eligibility=self.declaration,joint_use_id=self.use_id,capture_id=self.shared.receipt.capture_id,
                seal_id=seal,model_handoff_id=model_id,baseline_handoff_id=baseline_id,baseline_owner_id=self.baseline.owner_id,
                baseline_configuration_digest=CanonicalFeaturePipeline(self.baseline.profile.configuration).digest,
                baseline_numeric_policy_id=self.baseline.profile.capabilities.numeric_policy_id,
                baseline_definition=self.baseline.profile.definition,baseline_capabilities=self.baseline.profile.capabilities,
                coverage=coverage,model=model_result,baseline_points=points,history_ids=self.baseline.record[2])
            self.encoded_result=self.result.model_dump_json(warnings='error')
            if len(self.encoded_result.encode())>self.budgets.max_output_bytes:raise ValueError()
            self.shared.release()
            self.phase='SEALED';self.session=SealedJointH1Session()
            self.verify_internal()
            return self.session
        except Exception:
            if admitted:self.fail()
            raise
        finally:self.busy.release()

    def verify_internal(self):
        if self.phase!='SEALED' or self.result is None or self.result.model_dump_json(warnings='error')!=self.encoded_result:raise ValueError()
        self.verifications+=1
        if self.verifications>self.budgets.max_verifications:raise ValueError()
        self.check();self.source_current(self.model.runtime)
        self.model._joint_verify(self.shared,self.model_result,cancelled=self.cancelled)
        self.baseline.verify(self.shared,self.baseline_points,broad=True)
        self.approved('verify')
        result=SealedJointH1Result.model_validate_json(self.encoded_result)
        # All construction/broad callbacks precede original per-origin checks.
        self.model._joint_verify(self.shared,self.model_result,cancelled=self.cancelled,broad=False)
        self.baseline.verify(self.shared,self.baseline_points,broad=False)
        for period in self.declaration.coverage.periods:self.approved('scoped_return',period.forecast_origin)
        self.check()
        self.model._gate(lambda:self.check(),self.model.record,sealed=True)
        return result

    def verify(self,session,*,cancelled=_continue):
        if not self.busy.acquire(blocking=False):raise ValueError()
        admitted=False
        try:
            if type(session) is not SealedJointH1Session or session is not self.session or self.phase!='SEALED':raise ValueError()
            admitted=True
            if not callable(cancelled):raise ValueError()
            original=self.cancelled
            def combined():
                first=original();second=cancelled()
                if type(first) is not bool or type(second) is not bool:raise ValueError()
                return first or second
            self.cancelled=combined
            return self.verify_internal()
        except Exception:
            if admitted:self.fail()
            raise
        finally:
            if admitted and 'original' in locals():self.cancelled=original
            self.busy.release()


class JointH1Operator:
    __slots__=('__owner',)
    def __init__(self,owner):
        if type(owner) is not _JointOwner:raise ValueError('invalid joint composition')
        self.__owner=owner
    def issue(self,*a,**kw):return _guard(self.__owner.issue,*a,**kw)
    def revoke(self,*a,**kw):return _guard(self.__owner.revoke,*a,**kw)
    def cancel(self,*a,**kw):return _guard(self.__owner.cancel,*a,**kw)


class JointH1Executor:
    __slots__=('__owner',)
    def __init__(self,owner):
        if type(owner) is not _JointOwner:raise ValueError('invalid joint composition')
        self.__owner=owner
    def execute(self,*a,**kw):return _guard(self.__owner.execute,*a,**kw)


class JointH1Verifier:
    __slots__=('__owner',)
    def __init__(self,owner):
        if type(owner) is not _JointOwner:raise ValueError('invalid joint composition')
        self.__owner=owner
    def verify(self,*a,**kw):return _guard(self.__owner.verify,*a,**kw)


def _bootstrap_joint_owned(current,material,**composition):
    owner=_JointOwner(current,material,**composition)
    return JointH1Operator(owner),JointH1Executor(owner),JointH1Verifier(owner)
