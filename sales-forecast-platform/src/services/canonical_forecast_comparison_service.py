"""C1 owned, label-free pair execution. No scoring or external handoff import.

Trusted bootstrap owns authentic authorities/callbacks. Private Python objects are
lifecycle guards, not cryptographic credentials or a security sandbox. Sequential
checks do not create an atomic authorization lease; cancellation is cooperative.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import math
import struct
from threading import Lock
from time import monotonic
from uuid import UUID

import pandas as pd
import pyarrow as pa

from src.data.contracts.backtesting import (ForecastEvaluationPlan, PredictorCapabilities,
    RolloutBudgets, ForecastScope, DeterministicRolloutResult, identity)
from src.data.contracts.dataset import StagingBudgets, operational_json
from src.data.contracts.features import FeatureConfiguration
from src.data.contracts.forecast_comparison import (JointPredictionPlan, JointPredictionBudgets,
    JointForecastKey, ProducerPredictionDiagnostic, JointPredictionDiagnostic)
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline, FeatureBudgets
from src.features.forecast_baselines import build_baseline_profile
from src.providers.canonical_training_data_provider import CanonicalTrainingDataProvider
from src.services.canonical_baseline_evaluation_service import _authority, _declaration
from src.services.canonical_forecast_evaluation_service import _EvaluationOwner
from src.services.canonical_training_preparation_service import (
    CanonicalTrainingPreparationService, ValueAttestedAvailabilityVerifier)
from src.services.deterministic_forecast_rollout_service import (
    DeterministicForecastRolloutService, representation_schema_id, numeric_policy_id)


class JointPredictionError(ValueError):
    """Sanitized all-or-nothing failure; never callback details or source values."""


@dataclass(frozen=True)
class JointExecutionContext:
    plan_json: str
    plan_digest: str
    metadata: object = None
    capture_id: str | None = None


@dataclass(frozen=True)
class _Profile:
    profile_id: str
    configuration_json: str
    capabilities_json: str
    callback: object


def _scalar(value,limit):
    """Freeze supported canonical scalars, preserving original Decimal/float bits."""
    if value is None:return value,['null']
    if value is pd.NA:return value,['missing']
    if value is pd.NaT:return value,['nat']
    if type(value) is bool:return value,['bool',value]
    if type(value) is str:
        if len(value.encode())>limit:raise ValueError()
        return value,['text',value]
    if type(value) is UUID:return value,['uuid',str(value)]
    if type(value) is int and value.bit_length()<=16384:return value,['integer',str(value)]
    if type(value) is float and math.isfinite(value):return value,['float64',struct.pack('>d',value).hex()]
    if type(value) is Decimal and value.is_finite():
        t=value.as_tuple()
        if len(t.digits)>limit or abs(t.exponent)>10000:raise ValueError()
        return value,['decimal',t.sign,list(t.digits),t.exponent]
    if type(value) in (datetime,pd.Timestamp) and value.tzinfo is not None:
        return value,['timestamp',value.isoformat(),str(value.tzinfo)]
    raise ValueError('unsupported shared capture scalar')


def _dtype(value):
    # Pandas cannot reconstruct parameterized Arrow decimals from str(dtype).
    # Persist immutable, explicit parameters rather than a mutable dtype alias.
    if type(value) is pd.ArrowDtype:
        arrow=value.pyarrow_dtype
        if not pa.types.is_decimal(arrow):raise ValueError('unsupported shared Arrow dtype')
        return ('arrow_decimal',arrow.bit_width,arrow.precision,arrow.scale)
    if type(value) is pd.StringDtype:return ('string',value.storage)
    if isinstance(value,pd.CategoricalDtype):raise ValueError('implicit categorical snapshot unsupported')
    return ('pandas',str(value))


def _restore_dtype(value):
    if value[0]=='arrow_decimal':
        kind=pa.decimal128 if value[1]==128 else pa.decimal256
        return pd.ArrowDtype(kind(value[2],value[3]))
    if value[0]=='string':return pd.StringDtype(storage=value[1])
    return value[1]


@dataclass(frozen=True)
class _FrozenCapture:
    metadata: object
    columns: tuple
    dtypes: tuple
    rows: tuple
    capture_id: str
    decoded_bytes: int

    @classmethod
    def freeze(cls,metadata,frame,cell_limit,charge):
        columns=tuple(frame.columns);dtypes=tuple(_dtype(v) for v in frame.dtypes)
        size=int(frame.memory_usage(deep=True).sum());charge('assembly',2*size)
        binding=CanonicalTrainingPreparationService._temporal_binding(metadata,tuple(s.series_id for s in metadata.series))
        digest=hashlib.sha256(operational_json([binding.model_dump(mode='json'),metadata.mapping_json,
            metadata.series_json,columns,dtypes]).encode())
        rows=[]
        # Column lists preserve nullable sentinels and heterogeneous object
        # scalars; record conversion can replace extension missing values.
        for record in zip(*(frame[column].tolist() for column in columns)):
            cells=[];tags=[]
            for original in record:
                value,tag=_scalar(original,cell_limit);cells.append(value);tags.append(tag)
            encoded=operational_json(tags).encode();charge('assembly',len(encoded))
            digest.update(len(encoded).to_bytes(8,'big'));digest.update(encoded);rows.append(tuple(cells))
        return cls(metadata,columns,dtypes,tuple(rows),digest.hexdigest(),size)

    def view(self):
        # Apply each verified dtype at construction. Inferring a float column
        # first can round an object integer before a later astype(object).
        return pd.DataFrame({column:pd.Series([row[i] for row in self.rows],dtype=_restore_dtype(dtype))
            for i,(column,dtype) in enumerate(zip(self.columns,self.dtypes))})


def _partition(fold,origin):
    for name in ('training','validation','test'):
        if origin in getattr(fold,name+'_origins'):return name
    raise ValueError('origin outside fold')


def _key(binding,scope,plan):
    fold=next(f for f in plan.folds if f.fold_id==scope.fold_id)
    fields=('fold_id','organization_id','series_id','dimensions','metric','unit','currency','frequency',
            'timezone','forecast_origin','horizon_step','target_period','target_period_end')
    return JointForecastKey(dataset=binding,partition=_partition(fold,scope.forecast_origin),
        calendar=plan.calendar,period_anchor=plan.period_anchor,assessment_cutoff=fold.assessment_cutoff,
        **{k:getattr(scope,k) for k in fields})


def _order(key):
    return (str(key.organization_id),key.fold_id,{'training':0,'validation':1,'test':2}[key.partition],
        str(key.series_id),key.dimensions,key.forecast_origin.astimezone(timezone.utc),key.horizon_step,
        key.target_period.astimezone(timezone.utc),key.target_period_end.astimezone(timezone.utc))


class _JointRequest:
    """Private active-request owner. Never reconstructed from diagnostics or hashes."""
    def __init__(self,issuer,plan,cancelled):
        self.issuer=issuer;self.plan=plan;self.cancelled=cancelled;self.phase='CAPTURING'
        self.captures={};self.slots=[];self.handoffs=[];self.grid=();self.last=issuer._clock();self.start=self.last
        b=plan.budgets
        self.limits=dict(rows=b.max_captured_rows,decoded=b.max_decoded_bytes,assembly=b.max_assembly_bytes,
            work=b.max_work_cells,state=b.max_fitted_state_bytes,representation=b.max_representation_bytes,
            feedback=b.max_feedback_bytes,prediction=b.max_prediction_bytes,calls=b.max_predictor_calls,
            external=b.max_verification_calls,prefix=b.max_prefix_cells,verification=b.max_verification_bytes,
            claims=b.max_verification_claims,checks=b.max_verification_calls,profile=b.max_profile_bytes,
            output=b.max_output_bytes,events=b.max_prediction_events,output_cells=b.max_output_cells)
        self.used={k:0 for k in self.limits};self.views=set();self.reservation={};self.admission_base={}

    def active(self):
        return self.issuer._active_request(self) and self.phase!='CLOSED'

    def charge(self,kind,amount):
        if not self.active() or type(amount) is not int or amount<0:raise ValueError()
        self.used[kind]+=amount
        if self.used[kind]>self.limits[kind]:raise JointPredictionError('joint resource budget exceeded')

    def checkpoint(self):
        stopped=self.cancelled();now=self.issuer._clock()
        if (type(stopped) is not bool or stopped or type(now) not in (int,float)
                or type(self.start) not in (int,float) or not math.isfinite(now) or not math.isfinite(self.start)
                or now<self.last or now-self.start>=self.plan.budgets.max_elapsed_seconds):
            raise JointPredictionError('joint execution interrupted or elapsed budget exceeded')
        self.last=now

    def verify(self,callback,*args):
        self.charge('checks',1)
        claims=sum(len(a) for a in args if type(a) is tuple)
        self.charge('claims',claims)
        self.charge('verification',len(repr(args).encode()))
        try:approved=callback(*args)
        except Exception:raise JointPredictionError('trusted joint verification failed') from None
        if approved is not True:raise JointPredictionError('joint execution is not authorized')
        return True

    def slot(self,source,owner,plan):
        if not self.active() or self.phase!='PREDICTING':raise ValueError()
        found=next((s for s in self.slots if s[0] is source and s[1] is owner),None)
        if found is None or found[2].plan!=plan or not owner.permits(source,plan):raise ValueError()
        return found

    def permits(self,source,owner,plan,configuration):
        try:
            slot=self.slot(source,owner,plan)
            return configuration.model_dump_json()==slot[3].configuration_json
        except Exception:return False

    def context_fields(self,source,owner,plan,metadata):
        slot=self.slot(source,owner,plan);capture=self.captures[metadata.reference]
        if capture.metadata!=metadata:raise ValueError()
        return dict(joint_plan_digest=self.plan.digest,producer_profile_id=slot[3].profile_id,capture_id=capture.capture_id)

    def partition_binding(self,source,owner,plan):
        slot=self.slot(source,owner,plan)
        return [self.plan.digest,slot[3].profile_id,tuple(c.capture_id for c in self.captures.values())]

    def materialize(self,source,owner,plan,configuration,binding):
        slot=self.slot(source,owner,plan)
        if not self.permits(source,owner,plan,configuration):raise ValueError()
        key=(slot[3].profile_id,binding.reference)
        if key in self.views:raise ValueError('capture view is single-use per producer')
        capture=self.captures[binding.reference]
        if CanonicalTrainingPreparationService._temporal_binding(capture.metadata,binding.series_ids)!=binding:
            raise ValueError()
        self.views.add(key)
        self.charge('assembly',capture.decoded_bytes)
        return capture.metadata,capture.view()

    def validate_context(self,profile,producer,ctx):
        p=producer.plan
        if (not self.active() or ctx.joint_plan_digest!=self.plan.digest or ctx.producer_profile_id!=profile.profile_id
                or ctx.plan_digest!=p.digest or ctx.plan_json!=p.model_dump_json()
                or ctx.configuration_json!=profile.configuration_json or ctx.configuration_digest!=p.configuration_digest
                or ctx.capabilities_json!=profile.capabilities_json):raise ValueError('producer context mismatch')
        binding=next((d for d in p.datasets if d.reference==ctx.metadata.reference),None)
        capture=self.captures.get(ctx.metadata.reference)
        if (binding is None or capture is None or ctx.metadata!=capture.metadata
                or ctx.capture_id!=capture.capture_id or ctx.series_ids!=binding.series_ids
                or CanonicalTrainingPreparationService._temporal_binding(ctx.metadata,ctx.series_ids)!=binding):raise ValueError()
        if ctx.fold_id is not None:
            fold=next(f for f in p.folds if f.fold_id==ctx.fold_id)
            if ctx.forecast_origin not in fold.origins or ctx.training_cutoff!=fold.categorical_cutoff:raise ValueError()
            if ctx.target_period_end is not None:
                engine=CanonicalFeaturePipeline(FeatureConfiguration.model_validate_json(profile.configuration_json))
                if ctx.target_period_end!=engine._advance(engine._time(ctx.forecast_origin),p.forecast_horizon):raise ValueError()

    def seal(self,source,owner,producer,profile,handoff):
        self.slot(source,owner,producer.plan)
        actual=owner._seal_predictions(handoff)
        result=actual.result
        if result.plan!=producer.plan or actual.source is not source or actual.owner is not owner:raise ValueError()
        DeterministicRolloutResult.model_validate(result.model_dump())
        expected={k.key_id for k in self.grid};seen=set()
        if len(expected)!=len(self.grid) or len(result.events)!=len(self.grid):raise ValueError('nonunique or incomplete joint grid')
        engine=CanonicalFeaturePipeline(FeatureConfiguration.model_validate_json(profile.configuration_json))
        for event in result.events:
            self.checkpoint();self.charge('work',1)
            if (event.configuration_digest!=engine.digest or event.predictor_digest!=producer.plan.predictor.digest
                    or event.representation_schema_id!=producer.plan.predictor.representation_schema_id
                    or event.numeric_policy_id!=producer.plan.predictor.numeric_policy_id):raise ValueError()
            key=_key(event.dataset_binding,event.scope,producer.plan).key_id
            if key in seen or key not in expected:raise ValueError('producer grid mismatch')
            seen.add(key)
        if seen!=expected:raise ValueError('incomplete producer grid')
        if actual.metadata!=tuple(self.captures[d.reference].metadata for d in producer.plan.datasets):raise ValueError()
        wanted={(f.fold_id,d.reference.organization_id,o) for f in producer.plan.folds
                for d in producer.plan.datasets for o in f.origins}
        observed=set()
        if len(actual.origins)!=len(wanted) or len(actual.origins)!=len(result.compatibilities):raise ValueError()
        for (ctx,claims),compat in zip(actual.origins,result.compatibilities):
            self.validate_context(profile,producer,ctx)
            key=(ctx.fold_id,ctx.metadata.reference.organization_id,ctx.forecast_origin)
            if (key not in wanted or key in observed or key!=(compat.fold_id,compat.organization_id,compat.forecast_origin)
                    or ctx.rollout_compatibility_id!=compat.compatibility_id or ctx.target_period_end is None):raise ValueError()
            observed.add(key)
        if observed!=wanted:raise ValueError()
        for ctx,claims,categories in actual.calibration:self.validate_context(profile,producer,ctx)
        for ctx,claim in actual.external:self.validate_context(profile,producer,ctx)
        self.handoffs.append((source,producer,profile,actual))
        if len(self.handoffs)==2:self.phase='ALL_PREDICTIONS_SEALED'

    def close(self):
        for _,owner,_,_ in self.slots:owner.close()
        self.phase='CLOSED';self.captures.clear();self.slots.clear();self.handoffs.clear()


class CanonicalForecastComparisonService:
    """C1 only: execute() returns detached label-free diagnostics, never scores.

    Construct only at trusted startup. Exactly one candidate and at most eight
    fixed baseline alternatives; one selected pair per request, no registry API.
    Authentic authority selection and protected bootstrap remain deployment duties.
    """
    def __init__(self,provider,*,candidate_configuration,candidate_capabilities,candidate_predictor,
                 baseline_template_configuration,baseline_definitions,release_authority,value_authority,
                 authorize_joint,authorize_rollout,authorize_external_forecast=None,
                 feature_budgets=None,read_budgets=None,rollout_budgets=None,joint_budgets=None,elapsed_clock=monotonic):
        try:
            if type(provider) is not CanonicalTrainingDataProvider:raise ValueError()
            if (not all(callable(c) for c in (candidate_predictor,authorize_joint,authorize_rollout,elapsed_clock))
                    or (authorize_external_forecast is not None and not callable(authorize_external_forecast))):raise ValueError()
            if type(baseline_definitions) is not tuple or not 1<=len(baseline_definitions)<=8:raise ValueError()
            scopes,release=_authority(release_authority,'release_availability_v1')
            value_scopes,values=_authority(value_authority,'captured_history_value_v1')
            if scopes!=value_scopes:raise ValueError()
            cfg=_declaration(candidate_configuration,FeatureConfiguration)
            cap=_declaration(candidate_capabilities,PredictorCapabilities)
            fb=feature_budgets if feature_budgets is not None else FeatureBudgets()
            rb=read_budgets if read_budgets is not None else StagingBudgets()
            if type(fb) is not FeatureBudgets or type(rb) is not StagingBudgets:raise ValueError()
            fb=FeatureBudgets(**vars(fb));rb=StagingBudgets(**vars(rb))
            profiles=[_Profile(cap.predictor_id,cfg.model_dump_json(),cap.model_dump_json(),candidate_predictor)]
            for definition in baseline_definitions:
                built=build_baseline_profile(definition,baseline_template_configuration,feature_budgets=fb)
                profiles.append(_Profile(built.capabilities.predictor_id,built.configuration_json,built.capabilities_json,built))
            if len({p.profile_id for p in profiles})!=len(profiles):raise ValueError()
            for profile in profiles:
                c=FeatureConfiguration.model_validate_json(profile.configuration_json)
                a=PredictorCapabilities.model_validate_json(profile.capabilities_json);e=CanonicalFeaturePipeline(c,budgets=fb)
                if (a.implementation_kind!='synthetic' or a.strategy!='recursive_one_step' or a.representation_kind!='tabular_step'
                        or a.representation_schema_id!=representation_schema_id(c) or a.numeric_policy_id!=numeric_policy_id(c)
                        or a.allowed_covariates!=tuple(sorted(r.field for r in e.rules.exogenous))
                        or a.lookback<max((e.temporal.minimum_history,)+c.lag_offsets+c.rolling_windows)
                        or any(r.availability=='external_forecast' for r in e.rules.exogenous) and authorize_external_forecast is None):raise ValueError()
            self.__provider=provider;self.__profiles=tuple(profiles);self.__scopes=scopes
            self.__release=release;self.__values=values;self.__authorize=authorize_joint;self.__rollout_auth=authorize_rollout
            self.__external=authorize_external_forecast;self.__fb=fb;self.__rb=rb
            self.__rollout_limits=_declaration(rollout_budgets if rollout_budgets is not None else RolloutBudgets(),RolloutBudgets).model_dump()
            self.__limits=_declaration(joint_budgets if joint_budgets is not None else JointPredictionBudgets(),JointPredictionBudgets).model_dump()
            self.__clock=elapsed_clock;self.__lock=Lock();self.__active=None
        except Exception:raise JointPredictionError('invalid operator joint composition') from None

    def _clock(self):return self.__clock()
    def _active_request(self,request):return self.__active is request
    def _owns(self,owner):
        return self.__active is not None and self.__active.phase=='PREDICTING' and any(s[1] is owner for s in self.__active.slots)

    def execute(self,plan,*,cancelled=lambda:False):
        acquired=False;request=None
        try:
            plan=_declaration(plan,JointPredictionPlan)
            if not callable(cancelled) or any(v>self.__limits[k] for k,v in plan.budgets.model_dump().items()):raise ValueError()
            selected=[]
            for i,producer in enumerate(plan.producers):
                profile=next((p for p in self.__profiles if p.profile_id==producer.profile_id),None)
                if profile is None or (i==0)!=(profile is self.__profiles[0]):raise ValueError()
                cfg=FeatureConfiguration.model_validate_json(profile.configuration_json)
                engine=CanonicalFeaturePipeline(cfg,budgets=self.__fb)
                DeterministicForecastRolloutService._validate_execution_plan(producer.plan,engine,self.__fb)
                if (producer.plan.predictor.model_dump_json()!=profile.capabilities_json
                        or producer.plan.configuration_digest!=engine.digest
                        or (producer.plan.configuration_id,producer.plan.configuration_version)!=(cfg.configuration_id,cfg.version)
                        or tuple(d.model_dump_json() for d in producer.plan.datasets)!=self.__scopes
                        or any(v>self.__rollout_limits[k] for k,v in producer.plan.budgets.model_dump().items())):raise ValueError()
                selected.append((producer,profile))
            acquired=self.__lock.acquire(blocking=False)
            if not acquired:raise ValueError()
            request=_JointRequest(self,plan,cancelled);self.__active=request;request.checkpoint()
            for _,profile in selected:request.charge('profile',len(profile.configuration_json.encode())+len(profile.capabilities_json.encode()))
            return self.__execute(request,selected)
        except Exception:raise JointPredictionError('joint prediction execution failed') from None
        finally:
            if request is not None:request.close();self.__active=None
            if acquired:self.__lock.release()

    def __execute(self,r,selected):
        base=JointExecutionContext(r.plan.model_dump_json(),r.plan.digest)
        r.verify(self.__authorize,base,'execute')
        def joint(ctx,stage):return r.verify(self.__authorize,ctx,stage)
        preparation=CanonicalTrainingPreparationService(self.__provider,authorize_preparation=joint,verify_availability=None)
        for binding in r.plan.producers[0].plan.datasets:
            r.checkpoint()
            def context(meta):
                if preparation._temporal_binding(meta,binding.series_ids)!=binding:raise ValueError()
                return JointExecutionContext(base.plan_json,base.plan_digest,meta)
            def account(kind,rows,size):
                if kind=='capture':r.charge('rows',rows);r.charge('decoded',size)
                else:r.charge(kind,size)
            meta,ctx,frame=preparation._capture_neutral(binding.reference,r.plan.purpose,context,
                row_limit=r.plan.budgets.max_captured_rows,column_limit=self.__fb.max_columns,
                byte_limit=r.plan.budgets.max_decoded_bytes,read_budgets=self.__rb,checkpoint=r.checkpoint,account=account)
            frozen=_FrozenCapture.freeze(meta,frame,self.__fb.max_cell_bytes,r.charge)
            r.captures[meta.reference]=frozen
            del frame
        self.__admit(r,selected)
        r.phase='PREDICTING'
        for producer,profile in selected:
            # Closure defaults prevent the next producer from rebinding callbacks.
            def verify(ctx,claims,producer=producer,profile=profile):
                r.validate_context(profile,producer,ctx)
                def release(c,cs):return r.verify(self.__release,c,cs)
                def values(c,cs):return r.verify(self.__values,c,cs)
                return ValueAttestedAvailabilityVerifier(verify_availability=release,verify_captured_values=values)(ctx,claims)
            def authorize(ctx,stage,producer=producer,profile=profile):
                r.validate_context(profile,producer,ctx)
                return r.verify(self.__rollout_auth,ctx,stage)
            def external(ctx,claim,producer=producer,profile=profile):
                r.validate_context(profile,producer,ctx)
                return r.verify(self.__external,ctx,claim)
            source=DeterministicForecastRolloutService(self.__provider,predictor=profile.callback,authorize_rollout=authorize,
                verify_availability=verify,authorize_external_forecast=external,elapsed_clock=self.__clock)
            owner=_EvaluationOwner(self,source,producer.plan)
            r.slots.append((source,owner,producer,profile))
            cfg=FeatureConfiguration.model_validate_json(profile.configuration_json)
            def cancelled():r.checkpoint();return False
            handoff=source._owned_shared_rollout(owner,producer.plan,cfg,r,
                feature_budgets=self.__fb,read_budgets=self.__rb,cancelled=cancelled)
            r.seal(source,owner,producer,profile,handoff)
        if r.phase!='ALL_PREDICTIONS_SEALED':raise ValueError()
        # All broad checks precede assembly and original scoped terminal checks.
        for source,producer,profile,handoff in r.handoffs:
            for ctx,claims,categories in handoff.calibration:
                source._approved(source.verify_availability,ctx,claims)
                if categories:source._approved(source.verify_availability,ctx,categories)
            for ctx,claim in handoff.external:source._approved(source.authorize_external,ctx,claim)
        r.verify(self.__authorize,base,'return')
        for capture in r.captures.values():self.__provider.require_current(capture.metadata,budgets=self.__rb)
        diagnostic=JointPredictionDiagnostic(plan_digest=r.plan.digest,capture_ids=tuple(c.capture_id for c in r.captures.values()),
            grid=r.grid,producers=tuple(ProducerPredictionDiagnostic(profile_id=profile.profile_id,result_json=h.result.model_dump_json())
                for source,producer,profile,h in r.handoffs),admission=tuple(sorted(r.reservation.items())),
            usage=tuple(sorted(r.used.items())))
        r.charge('output',len(diagnostic.model_dump_json().encode()))
        # NO result construction or broad collaborators after this terminal phase.
        for source,producer,profile,handoff in r.handoffs:
            for ctx,claims in handoff.origins:
                source._approved(source.verify_availability,ctx,claims)
                source._approved(source.authorize,ctx,'return')
        r.checkpoint()
        return diagnostic

    def __admit(self,r,selected):
        """Observed capture geometry, before either predictor. Runtime caps stay active."""
        first=selected[0][0].plan;keys=[]
        engine=CanonicalFeaturePipeline(FeatureConfiguration.model_validate_json(selected[0][1].configuration_json),budgets=self.__fb)
        for fold in first.folds:
            # Reuse the exact executor calendar; full intervals are also checked by B1.
            for binding in first.datasets:
                meta=r.captures[binding.reference].metadata;definitions={s.series_id:s for s in meta.series};mapping=meta.mapping
                for origin in fold.origins:
                    for k in range(1,first.forecast_horizon+1):
                        for sid in binding.series_ids:
                            scope=ForecastScope(plan_digest=first.digest,dataset_version=binding.reference.version_id,fold_id=fold.fold_id,
                                organization_id=meta.reference.organization_id,series_id=sid,metric=mapping.target_metric,
                                unit=mapping.measurement.unit,currency=mapping.measurement.currency,frequency=first.frequency,timezone=first.timezone,
                                dimensions=tuple(sorted(definitions[sid].dimensions.items())),forecast_origin=origin,horizon_step=k,
                                target_period=engine._advance(engine._time(origin),k-1),target_period_end=engine._advance(engine._time(origin),k))
                            keys.append(_key(binding,scope,first))
        r.grid=tuple(sorted(keys,key=_order))
        if len({k.key_id for k in r.grid})!=len(r.grid):raise ValueError()
        # Bounds use captured rows/width/bytes, declared calculations and actual H,
        # never a product of unrelated global maxima. No vocabularies are fitted.
        estimates=dict(work=0,prefix=0,calls=0,state=0,assembly=0,prediction=0,representation=0,
                       claims=0,checks=0,verification=0,output=0,events=2*len(r.grid),output_cells=0,feedback=0,decoded=0)
        for producer,profile in selected:
            p=producer.plan;cfg=FeatureConfiguration.model_validate_json(profile.configuration_json)
            e=CanonicalFeaturePipeline(cfg,budgets=self.__fb);h=p.forecast_horizon
            per=sum(c.parameters['window'] if c.operation.startswith('rolling_') else max(1,len(c.inputs)) for c in cfg.calculations)
            for binding in p.datasets:
                capture=r.captures[binding.reference]
                CanonicalTrainingPreparationService._validate_capture_scope(capture.metadata,e,binding.series_ids)
                n=len(capture.rows);width=len(capture.columns)+1+len(e.rules.dimension_fields)
                estimates['decoded']+=len(capture.metadata.mapping_json.encode())+sum(len(v.encode()) for v in capture.metadata.series_json)
                base_claims=n*(2+len(e.rules.exogenous))
                claim_count=base_claims
                check_count=5
                largest_row=max((len(repr(row).encode()) for row in capture.rows),default=0)
                context_bytes=(len(repr(capture.metadata).encode())+len(profile.configuration_json.encode())
                    +len(p.model_dump_json().encode())+len(profile.capabilities_json.encode())+2048)
                for fold in p.folds:
                    count=len(fold.origins);s=len(binding.series_ids);tri=h*(h+1)//2
                    estimates['calls']+=count*h;estimates['prefix']+=count*s*len(cfg.ordered_outputs)*tri
                    estimates['output_cells']+=count*s*len(cfg.ordered_outputs)*h
                    category_claims=n*sum(c.field not in e.rules.dimension_fields for c in e.rules.categories)
                    claim_count+=3*base_claims+3*category_claims+3*count*base_claims
                    external=count*s*tri*sum(x.availability=='external_forecast' for x in e.rules.exogenous)
                    check_count+=7+9*count+3*int(category_claims>0)+3*external
                    # Category scans/codes bounded by actual calibration row count.
                    category_work=len(e.rules.categories)**2+2*len(e.rules.categories)*n
                    estimates['work']+=n*width+count*(h*(n*width+len(cfg.calculations)+len(cfg.ordered_outputs))+
                        s*tri*(len(cfg.ordered_outputs)+per+category_work))
                    estimates['assembly']+=capture.decoded_bytes*(3+count*(h+1))*2
                    columns={name:i for i,name in enumerate(capture.columns)}
                    cutoff=e._time(fold.categorical_cutoff);begin=e._time(fold.calibration_start)
                    calibration=[row for row in capture.rows if row[columns['period_start']]>=begin
                        and row[columns['period_end']]<=cutoff and row[columns['available_at']]<=cutoff]
                    definitions={v.series_id:v for v in capture.metadata.series};category_bytes=0
                    for category in e.rules.categories:
                        for row in calibration:
                            value=(definitions[row[columns['series_id']]].dimensions[category.field]
                                if category.field in e.rules.dimension_fields else row[columns[category.field]])
                            if type(value) is str:category_bytes+=6*len(value.encode())+32
                    estimates['state']+=4096+len(profile.configuration_json.encode())+category_bytes
                    key_bytes=max(len(key.model_dump_json().encode()) for key in r.grid if key.dataset==binding)
                    estimates['prediction']+=count*s*h*(key_bytes+4096+h*128)
                    estimates['representation']+=count*h*(8192+h*256+s*(key_bytes+len(cfg.ordered_outputs)*128))
                estimates['claims']+=claim_count;estimates['checks']+=check_count
                estimates['verification']+=claim_count*(4096+8*largest_row)+check_count*(context_bytes+self.__fb.max_cell_bytes)
        estimates['feedback']=estimates['prediction']
        estimates['checks']+=1  # Final broad joint authorization.
        estimates['output']=2*estimates['prediction']+2*sum(len(k.model_dump_json().encode()) for k in r.grid)+4096
        estimates['assembly']+=estimates['output']
        r.admission_base=dict(r.used);r.reservation=dict(estimates)
        for kind,amount in estimates.items():
            if r.used[kind]+amount>r.limits[kind]:raise JointPredictionError('joint pre-prediction admission exceeded')
