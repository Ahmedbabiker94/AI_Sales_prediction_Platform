"""Owned synthetic forecast evaluation. No external event import or operational I/O.

Private object ownership is a trusted in-process lifecycle, not a Python sandbox.
A trusted label verifier must attest the exact captured value revision at E; an
unverified source availability timestamp is never sufficient.
"""
from contextlib import closing
from dataclasses import dataclass
import math
from time import monotonic

import pandas as pd

from src.data.contracts.backtesting import ForecastEvaluationPlan,ForecastScope,identity
from src.data.contracts.dataset import StagingBudgets,_opaque
from src.data.contracts.features import FeatureConfiguration
from src.data.contracts.forecast_evaluation import ForecastScoringPlan,ScoredEvent,ForecastEvaluationResult
from src.data.processing.forecast_evaluation import numeric_identity,score_point,group_scores
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline,FeatureBudgets
from src.services.deterministic_forecast_rollout_service import DeterministicForecastRolloutService,_OwnedExecution


class ForecastEvaluationError(ValueError):
    """Fixed sanitized diagnostic, never customer rows or callback details."""


@dataclass(frozen=True)
class EvaluationContext:
    scoring_plan_json: str
    scoring_plan_digest: str
    producer_plan_json: str
    producer_plan_digest: str
    metadata: object = None
    fold_id: str | None = None
    forecast_origin: object = None
    assessment_cutoff: object = None
    series_ids: tuple = ()
    target_periods: tuple = ()
    producer_event_ids: tuple = ()
    purpose: str = 'offline_forecast_evaluation'
    action: str = 'score_labels'


@dataclass(frozen=True)
class LabelRevisionClaim:
    """Assertion requiring independent trusted verification, never self-approval."""
    dataset_reference: object
    manifest_digest: str
    admission_digest: str
    source_id: object
    mapping_digest: str
    organization_id: object
    series_id: object
    metric: str
    unit: str
    currency: str | None
    period_start: object
    period_end: object
    source_record_id: str
    lineage_id: object
    available_at: object
    evidence_reference: str
    value_identity: tuple
    assessment_cutoff: object
    policy: str = 'captured_value_as_of_E_v1'

    @property
    def digest(self):
        ref=self.dataset_reference
        return identity([str(ref.organization_id),str(ref.dataset_id),str(ref.attempt_id),ref.version_id,
            self.manifest_digest,self.admission_digest,str(self.source_id),self.mapping_digest,str(self.organization_id),
            str(self.series_id),self.metric,self.unit,self.currency,self.period_start.isoformat(),self.period_end.isoformat(),
            self.source_record_id,str(self.lineage_id),self.available_at.isoformat(),self.evidence_reference,
            self.value_identity,self.assessment_cutoff.isoformat(),self.policy])


class _EvaluationOwner:
    """No public mint/accept path; issued and consumed by one active evaluator."""
    def __init__(self,issuer,source,plan):
        self.issuer=issuer;self.source=source;self.plan=plan
        self.__state='executing';self.__handoff=None;self.__fingerprint=None

    def permits(self,source,plan):
        return self.issuer._owns(self) and self.__state=='executing' and source is self.source and plan==self.plan

    def _record(self,handoff):
        if (type(handoff) is not _OwnedExecution or not self.permits(handoff.source,handoff.result.plan)
                or handoff.owner is not self or self.__handoff is not None):
            raise ForecastEvaluationError('invalid owned execution lifecycle')
        self.__handoff=handoff;self.__fingerprint=identity(handoff.result.model_dump(mode='json'))

    def _seal_predictions(self,handoff):
        if (handoff is not self.__handoff or not self.permits(self.source,self.plan)
                or identity(handoff.result.model_dump(mode='json'))!=self.__fingerprint):
            raise ForecastEvaluationError('owned prediction origin could not be established')
        self.__state='sealed'
        return handoff


    def consume(self,handoff):
        # Legacy B2a alone advances a sealed owned prediction into label use.
        result=self._seal_predictions(handoff)
        self.__state='labels'
        return result

    def close(self):
        self.__state='closed';self.__handoff=None;self.__fingerprint=None


class CanonicalForecastEvaluationService:
    def __init__(self,rollout_service,*,authorize_evaluation,authorize_label_use,verify_label_revision,elapsed_clock=monotonic):
        if type(rollout_service) is not DeterministicForecastRolloutService:
            raise ForecastEvaluationError('trusted B1 producer required')
        if not all(callable(c) for c in (authorize_evaluation,authorize_label_use,verify_label_revision,elapsed_clock)):
            raise ForecastEvaluationError('explicit trusted scoring verifiers required')
        self.rollout=rollout_service;self.provider=rollout_service.provider
        self.authorize=authorize_evaluation;self.authorize_labels=authorize_label_use
        self.verify_revision=verify_label_revision;self.elapsed_clock=elapsed_clock;self.__owner=None

    def _owns(self,owner):return self.__owner is owner

    @staticmethod
    def _approved(callback,*args):
        try:value=callback(*args)
        except Exception:raise ForecastEvaluationError('trusted evaluation verification failed') from None
        if value is not True:raise ForecastEvaluationError('forecast evaluation is not authorized')

    def evaluate(self,scoring_plan,producer_plan,configuration,*,read_budgets=None,feature_budgets=None,cancelled=lambda:False):
        if self.__owner is not None:raise ForecastEvaluationError('evaluator lifecycle already active')
        owner=None
        try:
            if type(scoring_plan) is not ForecastScoringPlan or type(producer_plan) is not ForecastEvaluationPlan or type(configuration) is not FeatureConfiguration:
                raise ValueError()
            scoring_plan=ForecastScoringPlan.model_validate(scoring_plan.model_dump())
            producer_plan=ForecastEvaluationPlan.model_validate(producer_plan.model_dump())
            configuration=FeatureConfiguration.model_validate(configuration.model_dump())
            if scoring_plan.producer_plan_digest!=producer_plan.digest or scoring_plan.datasets!=producer_plan.datasets:
                raise ValueError()
            owner=_EvaluationOwner(self,self.rollout,producer_plan);self.__owner=owner
            return self._evaluate(owner,scoring_plan,producer_plan,configuration,read_budgets or StagingBudgets(),feature_budgets or FeatureBudgets(),cancelled)
        except Exception:
            raise ForecastEvaluationError('canonical forecast evaluation failed') from None
        finally:
            if owner is not None:owner.close();self.__owner=None

    def _evaluate(self,owner,sp,pp,cfg,rb,fb,cancelled):
        b=sp.budgets;start=self.elapsed_clock();last=start
        def checkpoint():
            nonlocal last
            stopped=cancelled();now=self.elapsed_clock()
            if (type(stopped) is not bool or stopped or type(now) not in (int,float) or type(start) not in (int,float)
                    or not math.isfinite(now) or not math.isfinite(start) or now<last or now-start>=b.max_elapsed_seconds):
                raise ForecastEvaluationError('evaluation interrupted or elapsed budget exceeded')
            last=now
        def producer_cancelled():checkpoint();return False
        checkpoint();engine=CanonicalFeaturePipeline(cfg,budgets=fb)
        sd=sp.digest;ad=sp.arithmetic.digest
        base=EvaluationContext(sp.model_dump_json(),sd,pp.model_dump_json(),pp.digest)
        if engine.digest!=pp.configuration_digest:raise ForecastEvaluationError('configuration binding mismatch')
        selected=[]
        for f in pp.folds:
            origins=tuple((part,o) for part in sp.partitions for o in getattr(f,part+'_origins'))
            selected.extend((f,part,o) for part,o in origins)
        total=sum(len(f.origins) for f in pp.folds)*sum(len(d.series_ids) for d in pp.datasets)*pp.forecast_horizon
        wanted=len(selected)*sum(len(d.series_ids) for d in pp.datasets)*pp.forecast_horizon
        if not wanted or total>b.max_prediction_events or wanted>b.max_scored_events or 2*len(pp.datasets)>b.max_capture_sessions:
            raise ForecastEvaluationError('evaluation event or capture budget exceeded')
        self._approved(self.authorize,base,'execute')
        self._approved(self.authorize,base,'policy')
        # Producer is invoked exactly once. No scoring label session has begun.
        handoff=owner.consume(self.rollout._owned_rollout(owner,pp,cfg,read_budgets=rb,feature_budgets=fb,cancelled=producer_cancelled))
        checkpoint();result=handoff.result
        used=dict(rows=0,decoded=0,intermediate=0,verification=0,work=0,captures=0,output=0)
        limits=dict(rows=b.max_captured_rows,decoded=b.max_decoded_bytes,intermediate=b.max_intermediate_bytes,
            verification=b.max_verification_bytes,work=b.max_work_cells,captures=b.max_capture_sessions,output=b.max_result_bytes)
        def charge(kind,amount):
            used[kind]+=amount
            if used[kind]>limits[kind]:raise ForecastEvaluationError('aggregate evaluation budget exceeded')
        usage=dict(handoff.usage)
        for kind,amount in [('rows',usage['rows']),('decoded',usage['decoded']),('intermediate',usage['assembly']),
                ('work',usage['work']),('captures',len(handoff.metadata)),('verification',usage['state']+usage['representation'])]:charge(kind,amount)
        # Include retained producer events/feedback, serialization/fingerprint
        # copies and original verification material, not merely the label view.
        charge('intermediate',3*usage['prediction']+usage['feedback']+2*(usage['state']+usage['representation']))
        for context,claims,categories in handoff.calibration:
            checkpoint();size=len(repr((context,claims,categories)).encode())
            charge('verification',size);charge('intermediate',2*size);charge('work',len(claims)+len(categories))
        for context,claims in handoff.origins:
            checkpoint();size=len(repr((context,claims)).encode())
            charge('verification',size);charge('intermediate',2*size);charge('work',len(claims))
        for context,request in handoff.external:
            checkpoint();size=len(repr((context,request)).encode())
            charge('verification',size);charge('intermediate',2*size);charge('work',1)
        metadata={m.reference.organization_id:m for m in handoff.metadata}
        if len(metadata)!=len(pp.datasets) or len(handoff.origins)!=sum(len(f.origins) for f in pp.folds)*len(pp.datasets):
            raise ForecastEvaluationError('incomplete producer verification coverage')
        # Validate the full producer grid independently of the returned events.
        expected=[];scoped={};event_index={}
        for f in pp.folds:
            for binding in pp.datasets:
                meta=metadata[binding.reference.organization_id]
                if self.rollout.preparation._temporal_binding(meta,binding.series_ids)!=binding or meta.purpose!=sp.purpose:
                    raise ForecastEvaluationError('verified producer metadata mismatch')
                definitions={s.series_id:s for s in meta.series};mapping=meta.mapping
                for o in f.origins:
                    origin=engine._time(o)
                    key=(f.fold_id,meta.reference.organization_id,origin)
                    scoped[key]=[]
                    for k in range(1,pp.forecast_horizon+1):
                        for sid in binding.series_ids:
                            scope=ForecastScope(plan_digest=pp.digest,dataset_version=binding.reference.version_id,
                                fold_id=f.fold_id,organization_id=meta.reference.organization_id,series_id=sid,metric=mapping.target_metric,
                                unit=mapping.measurement.unit,currency=mapping.measurement.currency,frequency=mapping.frequency.value,
                                timezone=mapping.timezone,dimensions=tuple(sorted(definitions[sid].dimensions.items())),forecast_origin=origin,
                                horizon_step=k,target_period=engine._advance(origin,k-1),target_period_end=engine._advance(origin,k))
                            charge('work',1);charge('intermediate',len(scope.model_dump_json().encode())*2)
                            expected.append((scope,binding));scoped[key].append(scope)
        if len(expected)!=len(result.events):raise ForecastEvaluationError('producer event coverage mismatch')
        for event,(scope,binding) in zip(result.events,expected):
            checkpoint()
            if (event.scope!=scope or event.dataset_binding!=binding or event.predictor_id!=pp.predictor.predictor_id
                    or event.predictor_version!=pp.predictor.predictor_version or event.predictor_digest!=pp.predictor.digest
                    or event.configuration_digest!=engine.digest or event.numeric_policy_id!=pp.predictor.numeric_policy_id
                    or event.representation_schema_id!=pp.predictor.representation_schema_id):raise ForecastEvaluationError('producer event scope mismatch')
            key=(scope.fold_id,scope.organization_id,scope.forecast_origin)
            event_index.setdefault(key,[]).append(event)
        # Revalidate recursive/state/prefix identities, including parent order.
        type(result).model_validate(result.model_dump())
        if len(handoff.origins)!=len(result.compatibilities):raise ForecastEvaluationError('incomplete compatibility coverage')
        seen_contexts=set()
        for (ctx,claims),compat in zip(handoff.origins,result.compatibilities):
            key=(ctx.fold_id,ctx.metadata.reference.organization_id,ctx.forecast_origin)
            if (key in seen_contexts or key not in scoped or ctx.metadata!=metadata[key[1]] or ctx.plan_digest!=pp.digest
                    or ctx.plan_json!=pp.model_dump_json() or ctx.configuration_digest!=engine.digest
                    or ctx.series_ids!=next(d.series_ids for d in pp.datasets if d.reference.organization_id==key[1])
                    or ctx.target_period_end!=engine._advance(ctx.forecast_origin,pp.forecast_horizon)
                    or ctx.rollout_compatibility_id!=compat.compatibility_id):raise ForecastEvaluationError('origin verification binding mismatch')
            seen_contexts.add(key)
        if seen_contexts!=set(scoped):raise ForecastEvaluationError('incomplete origin contexts')
        heldout={}
        def make_context(meta,f,origin=None,events=()):
            periods=tuple((e.scope.series_id,e.scope.horizon_step,e.scope.target_period,e.scope.target_period_end) for e in events)
            return EvaluationContext(base.scoring_plan_json,sd,base.producer_plan_json,pp.digest,meta,f.fold_id,origin,
                engine._time(f.assessment_cutoff),next(d.series_ids for d in pp.datasets if d.reference.organization_id==meta.reference.organization_id),
                periods,tuple(e.event_id for e in events))
        for meta in handoff.metadata:
            for f in pp.folds:
                for part in sp.partitions:
                    for o in getattr(f,part+'_origins'):
                        origin=engine._time(o);events=tuple(event_index[f.fold_id,meta.reference.organization_id,origin])
                        heldout[f.fold_id,meta.reference.organization_id,origin]=(make_context(meta,f,origin,events),events,part)
        # Authorization for label use is separate and precedes label read access.
        for ctx,events,part in heldout.values():self._approved(self.authorize_labels,ctx,'read')
        label_rows={};seen=set()
        for binding in pp.datasets:
            meta=metadata[binding.reference.organization_id];charge('captures',1);rows=0
            if used['rows']+meta.rows>b.max_captured_rows:raise ForecastEvaluationError('combined capture row budget exceeded')
            wanted_keys={(s.series_id,engine._time(s.target_period)) for ctx,events,part in heldout.values()
                if ctx.metadata.reference.organization_id==meta.reference.organization_id for e in events for s in (e.scope,)}
            with self.provider.open_read(binding.reference,purpose=sp.purpose,budgets=rb) as session:
                if session.metadata!=meta:raise ForecastEvaluationError('label publication metadata differs')
                with closing(session.batches()) as stream:
                    for frame in stream:
                        checkpoint();engine._frame_budget(frame);size=int(frame.memory_usage(deep=True).sum())
                        rows+=len(frame);charge('rows',len(frame));charge('decoded',size);charge('intermediate',size);charge('work',len(frame)*len(frame.columns))
                        if rows>meta.rows:raise ForecastEvaluationError('label capture count mismatch')
                        charge('intermediate',size)  # detached record assembly copy
                        for row in frame.to_dict('records'):
                            key=(meta.reference.organization_id,row['series_id'],engine._time(row['period_start']))
                            if key in seen:raise ForecastEvaluationError('ambiguous label observation')
                            seen.add(key)
                            if (row['series_id'],key[2]) in wanted_keys:
                                charge('intermediate',size)  # conservative per retained record/copy allowance
                                label_rows[key]=dict(row)
            if rows!=meta.rows:raise ForecastEvaluationError('incomplete label capture')
            checkpoint();self.provider.require_current(meta,budgets=rb)
        # All label readers exhausted and successfully closed. No predictors follow.
        aligned=[];terminal=[]
        for ctx,events,part in heldout.values():
            checkpoint();now=engine._time(self.provider.clock())
            if ctx.assessment_cutoff>now:raise ForecastEvaluationError('future label assessment cutoff')
            self._approved(self.authorize,ctx,'origin')
            self._approved(self.authorize_labels,ctx,'after_read')
            meta=ctx.metadata;mapping=meta.mapping;claims=[];pairs=[]
            for event in events:
                scope=event.scope;key=(scope.organization_id,scope.series_id,engine._time(scope.target_period))
                row=label_rows.get(key)
                if row is None:raise ForecastEvaluationError('complete eligible labels required')
                if (engine._time(row['period_end'])!=engine._time(scope.target_period_end) or row['metric']!=scope.metric
                        or row['unit']!=scope.unit or row['currency']!=scope.currency or row['frequency']!=scope.frequency
                        or row['value_status']!='actual' or engine._time(row['period_end'])>ctx.assessment_cutoff
                        or engine._time(row['available_at'])>ctx.assessment_cutoff):raise ForecastEvaluationError('label scope or cutoff mismatch')
                _opaque(row['source_record_id']);_opaque(row['availability_reference'])
                # No new revision timestamp is inferred. The trusted verifier must
                # attest the exact value identity using independently held evidence.
                claim=LabelRevisionClaim(meta.reference,meta.manifest_digest,meta.admission_digest,meta.source_id,
                    event.dataset_binding.mapping_digest,scope.organization_id,scope.series_id,scope.metric,scope.unit,scope.currency,
                    engine._time(scope.target_period),engine._time(scope.target_period_end),row['source_record_id'],row['lineage_id'],
                    engine._time(row['available_at']),row['availability_reference'],numeric_identity(row['value']),ctx.assessment_cutoff)
                charge('verification',len(claim.digest)+len(str(claim.value_identity).encode())+len(claim.evidence_reference.encode()))
                claims.append(claim);pairs.append((event,row['value'],claim))
            claims=tuple(claims)
            self._approved(self.verify_revision,ctx,claims)
            for event,value,claim in pairs:
                checkpoint();charge('work',1)
                scored=ScoredEvent(scope=event.scope,producer_event_id=event.event_id,label_binding_digest=claim.digest,
                    scoring_plan_digest=sd,arithmetic_policy_digest=ad,assessment_cutoff=ctx.assessment_cutoff,partition=part,
                    statistics=score_point(event.value,value,sp.arithmetic))
                charge('output',len(scored.model_dump_json().encode()));aligned.append(scored)
            terminal.append((ctx,claims))
        if len(aligned)!=wanted:raise ForecastEvaluationError('scored event coverage mismatch')
        groups=group_scores(tuple(aligned),sp,checkpoint)
        # Broad/revocation-capable checks finish before terminal scoped checks.
        for ctx,claims,categories in handoff.calibration:
            self.rollout._approved(self.rollout.verify_availability,ctx,claims)
            if categories:self.rollout._approved(self.rollout.verify_availability,ctx,categories)
        for ctx,request in handoff.external:self.rollout._approved(self.rollout.authorize_external,ctx,request)
        self._approved(self.authorize,base,'return')
        for meta in handoff.metadata:self.provider.require_current(meta,budgets=rb)
        value=ForecastEvaluationResult(scoring_plan=sp,producer_result_id=result.rollout_id,events=tuple(aligned),groups=groups)
        charge('output',len(value.model_dump_json().encode()))
        label_checks={(ctx.fold_id,ctx.metadata.reference.organization_id,ctx.forecast_origin):(ctx,claims) for ctx,claims in terminal}
        for ctx,claims in handoff.origins:
            self.rollout._approved(self.rollout.verify_availability,ctx,claims)
            self.rollout._approved(self.rollout.authorize,ctx,'return')
            label=label_checks.get((ctx.fold_id,ctx.metadata.reference.organization_id,ctx.forecast_origin))
            if label is not None:
                lc,proofs=label
                self._approved(self.verify_revision,lc,proofs)
                self._approved(self.authorize_labels,lc,'return')
                self._approved(self.authorize,lc,'return')
        checkpoint()
        return value
