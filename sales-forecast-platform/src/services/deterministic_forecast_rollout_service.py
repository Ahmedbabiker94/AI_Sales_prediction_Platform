"""Label-free bounded synthetic rollout, outside active training and serving.

Injected Python code is trusted application composition, not a sandbox. Sequential
checks do not create an atomic authorization snapshot or hard process preemption.
"""
from dataclasses import dataclass
import math
from time import monotonic

import pandas as pd

from src.data.contracts.backtesting import (ForecastEvaluationPlan, PredictionRequest,
    PredictionBatch, PredictionPoint, PredictionEvent, ForecastScope,
    RolloutCompatibilityBinding, DeterministicRolloutResult, identity, point)
from src.data.contracts.dataset import StagingBudgets
from src.data.contracts.forecast import ForecastConfig
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline, FeatureBudgets
from src.features.feature_schema_compatibility import describe_feature_schema, verify_feature_schema
from src.services.canonical_training_preparation_service import (
    CanonicalTrainingPreparationService, TemporalPreparationContext, PreparationBudgets)


class ForecastRolloutError(ValueError):
    """Fixed public diagnostic, without collaborator details."""


@dataclass(frozen=True)
class RolloutContext(TemporalPreparationContext):
    model_fit_cutoff: object = None
    assessment_cutoff: object = None
    capabilities_json: str = ''
    rollout_compatibility_id: str | None = None
    joint_plan_digest: str | None = None
    producer_profile_id: str | None = None
    capture_id: str | None = None


@dataclass(frozen=True)
class _OwnedExecution:
    """Internal request-owned material, never an externally authenticatable token."""
    owner: object
    source: object
    result: object
    metadata: tuple
    calibration: tuple
    external: tuple
    origins: tuple
    usage: tuple


def representation_schema_id(configuration):
    return identity(['tabular_step','1.0',configuration.model_dump(mode='json')])


def numeric_policy_id(configuration):
    data=configuration.execution.model_dump(mode='json')
    return identity([data['numeric_conversion'],data.get('numeric_policies',[]),data['measurement']])


class DeterministicForecastRolloutService:
    def __init__(self, provider, *, predictor, authorize_rollout, verify_availability,
                 authorize_external_forecast=None, elapsed_clock=monotonic):
        self.provider=provider; self.predictor=predictor; self.authorize=authorize_rollout
        self.verify_availability=verify_availability; self.authorize_external=authorize_external_forecast
        self.elapsed_clock=elapsed_clock
        self.preparation=CanonicalTrainingPreparationService(provider,
            authorize_preparation=lambda *a:self.authorize(*a),
            verify_availability=lambda *a:self.verify_availability(*a))

    @staticmethod
    def _approved(callback,*args):
        try: result=callback(*args)
        except Exception: raise ForecastRolloutError('trusted rollout callback failed') from None
        if result is not True: raise ForecastRolloutError('forecast rollout is not authorized')

    def rollout(self, plan, configuration, *, feature_budgets=None, read_budgets=None, cancelled=lambda:False):
        return self._execute(plan,configuration,feature_budgets=feature_budgets,read_budgets=read_budgets,cancelled=cancelled)

    def _owned_rollout(self, owner, plan, configuration, **kwargs):
        # Supported interfaces require an active evaluator-owned request. Private
        # names and Python object identity are not an adversarial code sandbox.
        from src.services.canonical_forecast_evaluation_service import _EvaluationOwner
        if type(owner) is not _EvaluationOwner or not owner.permits(self,plan):
            raise ForecastRolloutError('active evaluator lifecycle required')
        return self._execute(plan,configuration,_owner=owner,**kwargs)

    def _owned_shared_rollout(self,owner,plan,configuration,shared,**kwargs):
        """Private C1 route. A caller-provided frame or serialized seal is never accepted."""
        from src.services.canonical_forecast_comparison_service import _JointRequest
        from src.services.canonical_forecast_evaluation_service import _EvaluationOwner
        if (type(shared) is not _JointRequest or type(owner) is not _EvaluationOwner
                or not shared.permits(self,owner,plan,configuration) or not owner.permits(self,plan)):
            raise ForecastRolloutError('active joint capture lifecycle required')
        return self._execute(plan,configuration,_owner=owner,_shared=shared,**kwargs)

    def _execute(self,plan,configuration,*,feature_budgets=None,read_budgets=None,cancelled=lambda:False,_owner=None,_shared=None):
        try:
            if type(plan) is not ForecastEvaluationPlan: raise ValueError()
            plan=ForecastEvaluationPlan.model_validate(plan.model_dump())
            if plan.predictor.strategy!='recursive_one_step' or plan.predictor.representation_kind!='tabular_step':
                raise ValueError()
            return self._rollout(plan,configuration,feature_budgets or FeatureBudgets(),
                                 read_budgets or StagingBudgets(),cancelled,_owner,_shared)
        except Exception:
            raise ForecastRolloutError('deterministic forecast rollout failed') from None

    @staticmethod
    def _validate_execution_plan(p,engine,fb):
        """Shared declared-geometry/calendar checks, including the full H interval."""
        b=p.budgets;h=p.forecast_horizon;cap=p.predictor;cfg=engine.configuration
        if (p.configuration_id!=cfg.configuration_id or p.configuration_version!=cfg.version
                or p.configuration_digest!=engine.digest
                or (p.frequency,p.calendar,p.timezone,p.period_anchor)!=(engine.temporal.frequency.value,
                    engine.temporal.calendar,engine.temporal.timezone,engine.temporal.period_anchor)
                or cap.representation_schema_id!=representation_schema_id(cfg)
                or cap.numeric_policy_id!=numeric_policy_id(cfg)
                or cap.allowed_covariates!=tuple(sorted(r.field for r in engine.rules.exogenous))
                or cap.lookback<max((engine.temporal.minimum_history,)+cfg.lag_offsets+cfg.rolling_windows)
                or h>fb.max_horizon):
            raise ForecastRolloutError('rollout configuration mismatch')
        origins=sum(len(f.origins) for f in p.folds);series=sum(len(d.series_ids) for d in p.datasets)
        cells=origins*series*len(cfg.ordered_outputs)
        if cells*h>b.max_output_cells or cells*h*(h+1)//2>b.max_work_cells:
            raise ForecastRolloutError('aggregate prefix budget exceeded')
        # Full half-open target intervals, not merely H1 origin ordering.
        heldout_end=None
        for fold in p.folds:
            for date in (fold.calibration_start,fold.categorical_cutoff)+fold.origins:
                engine._aligned(engine._time(date))
            for left,right in ((fold.training_origins,fold.validation_origins+fold.test_origins),
                               (fold.validation_origins,fold.test_origins)):
                if left and right and engine._advance(engine._time(left[-1]),h)>engine._time(right[0]):
                    raise ForecastRolloutError('full-horizon partition overlap')
            if engine._advance(engine._time(fold.training_origins[-1]),h)>engine._time(fold.model_fit_cutoff):
                raise ForecastRolloutError('training interval exceeds model-fit cutoff')
            held=fold.validation_origins+fold.test_origins
            if held:
                if heldout_end is not None and engine._time(held[0])<heldout_end:
                    raise ForecastRolloutError('held-out fold target intervals overlap')
                heldout_end=engine._advance(engine._time(held[-1]),h)
                if heldout_end>engine._time(fold.assessment_cutoff):
                    raise ForecastRolloutError('target interval exceeds assessment cutoff')

    def _rollout(self,p,configuration,fb,rb,cancelled,_owner=None,_shared=None):
        engine=CanonicalFeaturePipeline(configuration,budgets=fb)
        b=p.budgets;h=p.forecast_horizon;cap=p.predictor;cfg=engine.configuration
        start=self.elapsed_clock();last=start
        def checkpoint():
            nonlocal last
            stopped=cancelled();now=self.elapsed_clock()
            if (type(stopped) is not bool or stopped or type(now) not in (int,float)
                    or type(start) not in (int,float) or not math.isfinite(now) or not math.isfinite(start)
                    or now<last or now-start>=b.max_elapsed_seconds):
                raise ForecastRolloutError('rollout interrupted or elapsed budget exhausted')
            last=now
        checkpoint()
        self._validate_execution_plan(p,engine,fb)
        used=dict(rows=0,decoded=0,assembly=0,work=0,state=0,representation=0,feedback=0,prediction=0,calls=0,external=0)
        limits=dict(rows=b.max_captured_rows,decoded=b.max_decoded_bytes,assembly=b.max_assembly_bytes,
            work=b.max_work_cells,state=b.max_fitted_state_bytes,representation=b.max_representation_bytes,
            feedback=b.max_feedback_bytes,prediction=b.max_prediction_bytes,calls=b.max_predictor_calls,
            external=b.max_external_requests)
        def charge(kind,amount):
            used[kind]+=amount
            if used[kind]>limits[kind]:raise ForecastRolloutError('aggregate rollout budget exceeded')
            if _shared is not None:_shared.charge(kind,amount)
        def account(kind,rows,size):
            if kind=='capture':charge('rows',rows);charge('decoded',size)
            else:charge(kind,size)
        captures=[];pj=p.model_dump_json();pdigest=p.digest
        for binding in p.datasets:
            checkpoint()
            def factory(meta):
                if self.preparation._temporal_binding(meta,binding.series_ids)!=binding or meta.purpose!=p.purpose:
                    raise ForecastRolloutError('verified publication binding mismatch')
                if used['rows']+meta.rows>b.max_captured_rows:raise ForecastRolloutError('capture row budget exceeded')
                charge('decoded',len(meta.mapping_json.encode())+sum(len(v.encode()) for v in meta.series_json))
                return RolloutContext(meta,cfg.model_dump_json(),engine.digest,binding.series_ids,pj,pdigest,
                                      capabilities_json=cap.model_dump_json(),
                    **(_shared.context_fields(self,_owner,p,meta) if _shared is not None else {}))
            if _shared is None:
                meta,base,frame,definitions=self.preparation._capture(binding.reference,engine,binding.series_ids,p.purpose,
                    factory,fb,PreparationBudgets(b.max_decoded_bytes,b.max_elapsed_seconds),rb,checkpoint,account)
            else:
                meta,frame=_shared.materialize(self,_owner,p,cfg,binding)
                base=factory(meta)
                self._approved(self.authorize,base,'read')
                checkpoint();self.provider.require_current(meta,budgets=rb)
                self._approved(self.authorize,base,'after_read')
                size=int(frame.memory_usage(deep=True).sum())
                used['rows']+=len(frame);used['decoded']+=size
                if used['rows']>limits['rows'] or used['decoded']>limits['decoded']:
                    raise ForecastRolloutError('producer capture-view budget exceeded')
                charge('assembly',2*size)
                frame,definitions=self.preparation._project_capture(meta,frame,engine,binding.series_ids,b.max_decoded_bytes)
            claims,exo=self.preparation._availability(frame,engine,checkpoint)
            self._approved(self.verify_availability,base,claims)
            engine._rows(frame,observations=True)
            requests=pd.DataFrame([dict(organization_id=meta.reference.organization_id,series_id=s,
                metric=meta.mapping.target_metric,unit=meta.mapping.measurement.unit,currency=meta.mapping.measurement.currency,
                frequency=meta.mapping.frequency.value,**{f:definitions[s].dimensions[f] for f in engine.rules.dimension_fields})
                for s in binding.series_ids])
            captures.append((binding,meta,base,frame,claims,exo,requests))
        events=[];compatibilities=[];calibration=[];externals=[];origin_checks=[]
        for fold in p.folds:
            for binding,meta,base,frame,claims,exo,requests in captures:
                checkpoint();cutoff=engine._time(fold.categorical_cutoff)
                def context(origin=None,end=None,compatibility=None):
                    return RolloutContext(meta,base.configuration_json,engine.digest,binding.series_ids,pj,pdigest,
                        fold.fold_id,origin,cutoff,end,engine._time(fold.model_fit_cutoff),
                        engine._time(fold.assessment_cutoff),cap.model_dump_json(),compatibility,
                        **(_shared.context_fields(self,_owner,p,meta) if _shared is not None else {}))
                calibration_context=context(engine._time(fold.origins[0]))
                self.provider.require_current(meta,budgets=rb)
                self._approved(self.authorize,calibration_context,'fold')
                now=engine._time(self.provider.clock())
                if engine._time(fold.assessment_cutoff)>now:raise ForecastRolloutError('future assessment declaration')
                self._approved(self.verify_availability,calibration_context,claims)
                charge('assembly',int(frame.memory_usage(deep=True).sum()))
                fit=frame[(frame.period_start>=engine._time(fold.calibration_start))&(frame.period_end<=cutoff)].copy()
                if fit.empty or any(engine._time(v)>cutoff for v in fit.available_at):raise ForecastRolloutError('calibration unavailable')
                expected=[];date=engine._time(fold.calibration_start)
                while date<cutoff:
                    if len(expected)>=fb.max_history_periods:raise ForecastRolloutError('calibration too large')
                    expected.append(date);date=engine._advance(date,1)
                if date!=cutoff:raise ForecastRolloutError('calibration alignment mismatch')
                for s in binding.series_ids:
                    if sorted(fit[fit.series_id==s].period_start)!=expected:raise ForecastRolloutError('incomplete calibration')
                categories=self.preparation._verify_categories(fit,engine,calibration_context,cutoff,checkpoint)
                charge('work',len(fit)*len(fit.columns))
                partition=[pdigest,fold.fold_id,binding.model_dump(mode='json'),cutoff.isoformat(),fold.calibration_start.isoformat()]
                if _shared is not None:partition.append(_shared.partition_binding(self,_owner,p))
                state=engine.fit_state(fit,training_cutoff=cutoff,partition_reference=identity(partition))
                charge('state',len(state.model_dump_json().encode()))
                calibration.append((calibration_context,claims,categories))
                for od in fold.origins:
                    checkpoint();origin=engine._time(od)
                    charge('work',h*(len(cfg.calculations)+len(cfg.ordered_outputs)))
                    forecasts=tuple(ForecastConfig(forecast_frequency=p.frequency,forecast_horizon=k) for k in range(1,h+1))
                    schemas=tuple(describe_feature_schema(cfg,state,forecast_config=f,forecast_origin=origin) for f in forecasts)
                    compatibility=RolloutCompatibilityBinding(plan_digest=pdigest,fold_id=fold.fold_id,
                        organization_id=meta.reference.organization_id,forecast_origin=origin,total_horizon=h,
                        predictor_digest=cap.digest,representation_schema_id=cap.representation_schema_id,
                        configuration_digest=engine.digest,state_id=state.state_id,numeric_policy_id=cap.numeric_policy_id,
                        prefix_schema_ids=tuple(s.schema_id for s in schemas))
                    compatibilities.append(compatibility)
                    current=context(origin,engine._advance(origin,h),compatibility.compatibility_id)
                    self._approved(self.authorize,current,'origin');self._approved(self.verify_availability,current,claims)
                    charge('assembly',int(frame.memory_usage(deep=True).sum()))
                    history=frame[(frame.period_end<=origin)&(frame.available_at<=origin)].copy()
                    for sid in binding.series_ids:
                        if len(history[history.series_id==sid])<cap.lookback:raise ForecastRolloutError('lookback unavailable')
                    feedback=[];parents={s:[] for s in binding.series_ids}
                    per_target=sum(c.parameters['window'] if c.operation.startswith('rolling_') else max(1,len(c.inputs))
                        for c in cfg.calculations)
                    per_target+=len(engine.rules.categories)**2+2*sum(len(v) for v in state.vocabularies.values())
                    def external(request):
                        charge('external',1)
                        self._approved(self.authorize_external,current,request)
                        externals.append((current,request));return True
                    engine.authorize_external_forecast=external
                    for k,(forecast,schema) in enumerate(zip(forecasts,schemas),1):
                        checkpoint()
                        charge('work',len(history)*len(history.columns)+len(requests)*k*(len(cfg.ordered_outputs)+per_target))
                        charge('assembly',int(history.memory_usage(deep=True).sum()))
                        matrix=engine.prepare_inference(history,requests,state,forecast_origin=origin,forecast_config=forecast,
                            exogenous=exo,prior_predictions=pd.DataFrame(feedback) if feedback else None)
                        verify_feature_schema(schema,cfg,state,matrix,requests=requests,forecast_config=forecast,forecast_origin=origin)
                        if _shared is not None:_shared.charge('prefix',len(matrix)*len(matrix.columns))
                        target=engine._advance(origin,k-1);end=engine._advance(origin,k)
                        step=matrix.loc[matrix.index.get_level_values('period_start')==target].copy()
                        if _shared is not None:_shared.charge('output_cells',len(step)*len(step.columns))
                        scopes=tuple(ForecastScope(plan_digest=pdigest,dataset_version=binding.reference.version_id,
                            fold_id=fold.fold_id,organization_id=meta.reference.organization_id,series_id=s,
                            metric=meta.mapping.target_metric,unit=meta.mapping.measurement.unit,currency=meta.mapping.measurement.currency,
                            frequency=p.frequency,timezone=p.timezone,dimensions=tuple(sorted((f,definitions_value) for f,definitions_value
                                in zip(engine.rules.dimension_fields,requests.iloc[j][list(engine.rules.dimension_fields)].tolist()))),
                            forecast_origin=origin,horizon_step=k,target_period=target,target_period_end=end)
                            for j,s in enumerate(binding.series_ids))
                        request=PredictionRequest(dataset_binding=binding,capabilities=cap,compatibility=compatibility,
                            prefix_length=k,prefix_schema_id=schema.schema_id,scopes=scopes,feature_names=tuple(step.columns),
                            feature_dtypes=tuple(str(d) for d in step.dtypes),rows=tuple(step.itertuples(index=False,name=None)))
                        pd.testing.assert_frame_equal(step,request.matrix)
                        charge('representation',len(request.model_dump_json().encode()))
                        charge('calls',1)
                        output=self.predictor(request);checkpoint()
                        # Reject forged/subclassed children before serialization or calling methods.
                        if (type(output) is not PredictionBatch or type(output.points) is not tuple
                                or len(output.points)!=len(scopes) or type(output.request_id) is not str
                                or output.request_id!=request.request_id):raise ForecastRolloutError('prediction batch mismatch')
                        for v in output.points:
                            if type(v) is not PredictionPoint or type(v.scope) is not ForecastScope:raise ForecastRolloutError('invalid point')
                            if abs(point(v.value))>cap.max_absolute_prediction:raise ForecastRolloutError('prediction magnitude exceeded')
                            if not any(v.scope==s for s in scopes):raise ForecastRolloutError('prediction scope mismatch')
                        output=PredictionBatch.model_validate(output.model_dump())
                        keyed={identity(v.scope.model_dump(mode='json')):v for v in output.points}
                        if len(keyed)!=len(scopes):raise ForecastRolloutError('incomplete prediction keys')
                        for s in scopes:
                            value=keyed[identity(s.model_dump(mode='json'))].value
                            event=PredictionEvent(dataset_binding=binding,scope=s,request_id=request.request_id,
                                predictor_id=cap.predictor_id,predictor_version=cap.predictor_version,predictor_digest=cap.digest,
                                representation_schema_id=cap.representation_schema_id,configuration_digest=engine.digest,
                                state_id=state.state_id,prefix_schema_id=schema.schema_id,rollout_compatibility_id=compatibility.compatibility_id,
                                numeric_policy_id=cap.numeric_policy_id,parent_feedback_ids=tuple(parents[s.series_id]),value=value)
                            charge('prediction',len(event.model_dump_json().encode()))
                            if _shared is not None:_shared.charge('events',1)
                            events.append(event)
                            parents[s.series_id].append(event.event_id)
                            row=dict(organization_id=s.organization_id,series_id=s.series_id,metric=s.metric,unit=s.unit,
                                currency=s.currency,frequency=s.frequency,period_start=target,period_end=end,available_at=origin,
                                value=value,**dict(s.dimensions))
                            feedback.append(row);charge('feedback',len(event.model_dump_json().encode()))
                    origin_checks.append((current,claims))
        checkpoint()
        for ctx,claims,categories in calibration:
            self._approved(self.verify_availability,ctx,claims)
            if categories:self._approved(self.verify_availability,ctx,categories)
        for ctx,request in externals:self._approved(self.authorize_external,ctx,request)
        for binding,meta,base,frame,claims,exo,requests in captures:
            self._approved(self.authorize,base,'return');self.provider.require_current(meta,budgets=rb)
        if len(origin_checks)!=len(compatibilities):raise ForecastRolloutError('origin verification coverage mismatch')
        result=DeterministicRolloutResult(plan=p,events=tuple(events),compatibilities=tuple(compatibilities))
        handoff=None
        if _owner is not None:
            handoff=_OwnedExecution(_owner,self,result,tuple(c[1] for c in captures),
                tuple(calibration),tuple(externals),tuple(origin_checks),tuple(sorted(used.items())))
            _owner._record(handoff)
        # Only exact original origin-scoped callbacks and checkpoint may follow assembly.
        for ctx,claims in origin_checks:
            self._approved(self.verify_availability,ctx,claims)
            self._approved(self.authorize,ctx,'return')
        checkpoint()
        return result if handoff is None else handoff
