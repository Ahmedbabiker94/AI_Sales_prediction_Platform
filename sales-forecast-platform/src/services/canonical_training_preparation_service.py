"""Bounded single/temporal-origin H1 preparation. No model fitting or CSV path."""
from dataclasses import dataclass
from contextlib import closing
from decimal import Decimal
import hashlib
import math
import struct
from time import monotonic
from uuid import UUID

import pandas as pd

from src.data.contracts.dataset import StagingBudgets, StoredDatasetReference, operational_json, _opaque
from src.data.contracts.forecast import ForecastConfig
from src.data.contracts.features import FeatureState
from src.data.contracts.temporal_preparation import TemporalPreparationPlan, TemporalDatasetBinding
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline, FeatureBudgets
from src.features.feature_schema_compatibility import describe_feature_schema, verify_feature_schema, FeatureSchemaCompatibility
from src.providers.canonical_training_data_provider import CanonicalTrainingDataProvider, VerifiedTrainingMetadata


class TrainingPreparationError(ValueError):
    """Bounded public diagnostics, never collaborator messages or customer values."""


@dataclass(frozen=True)
class PreparationContext:
    metadata: VerifiedTrainingMetadata
    configuration_json: str
    configuration_digest: str
    series_ids: tuple[UUID, ...]
    forecast_origin: pd.Timestamp
    training_cutoff: pd.Timestamp
    forecast_horizon: int


@dataclass(frozen=True)
class AvailabilityClaim:
    series_id: UUID
    source_record_id: str
    lineage_id: UUID
    field: str
    period_start: pd.Timestamp
    period_end: pd.Timestamp
    available_at: pd.Timestamp
    evidence_reference: str
    # Additive declaration, not authoritative proof. Legacy verifiers may ignore
    # it; ValueAttestedAvailabilityVerifier requires independent attestation.
    value_identity: tuple | None = None
    value_identity_version: str | None = None


@dataclass(frozen=True)
class TemporalPreparationContext:
    metadata: VerifiedTrainingMetadata
    configuration_json: str
    configuration_digest: str
    series_ids: tuple[UUID, ...]
    plan_json: str
    plan_digest: str
    fold_id: str | None = None
    forecast_origin: pd.Timestamp | None = None
    training_cutoff: pd.Timestamp | None = None
    target_period_end: pd.Timestamp | None = None


def historical_value_identity(value):
    """Exact pre-conversion identity v1; never stringify arbitrary objects.

    Decimal sign/digits/exponent (including scale and signed zero) and binary64
    bits are distinct. Integers and floating values never share an identity.
    The bounded capture already limits encoded payload size.
    """
    if type(value) is Decimal and value.is_finite():
        parts=value.as_tuple()
        return ('decimal',parts.sign,tuple(parts.digits),parts.exponent)
    if type(value) is int:return ('integer',str(value))
    if type(value) is float and math.isfinite(value):
        return ('float64',struct.pack('>d',value).hex())
    raise TrainingPreparationError('unsupported historical captured value')


@dataclass(frozen=True)
class CapturedHistoryClaim:
    """Value/revision attestation request, NOT an issued trust credential.

    All nested identities are immutable. The original availability object is
    retained through terminal rechecks; an authority must compare it with its
    independent publication-bound ledger, including revision validity at O.
    None origin denotes capture verification, never historical eligibility.
    """
    availability: AvailabilityClaim
    dataset_reference: StoredDatasetReference
    manifest_digest: str
    admission_digest: str
    source_id: UUID
    mapping_digest: str
    mapping_id: str
    mapping_version: str
    organization_id: UUID
    metric: str
    unit: str
    currency: str | None
    purpose: str
    series_ids: tuple[UUID, ...]
    forecast_origin: pd.Timestamp | None
    calibration_cutoff: pd.Timestamp | None
    plan_digest: str | None
    fold_id: str | None
    target_period_end: pd.Timestamp | None
    protocol_version: str = 'captured_history_value_v1'


class ValueAttestedAvailabilityVerifier:
    """Opt-in trusted composition over the EXISTING availability callback slot.

    Both callbacks are mandatory and must return exactly True. The value
    authority must own independent expected values/revisions, not trust hashes
    recomputed from these declarations. No timestamps, matching digest or type
    of this adapter authenticate an authority. Trusted Python is not sandboxed.
    Existing callbacks keep legacy semantics until explicitly wrapped here.
    """
    protocol_version = 'captured_history_value_v1'

    def __init__(self, *, verify_availability, verify_captured_values):
        if not callable(verify_availability) or not callable(verify_captured_values):
            raise TrainingPreparationError('authoritative historical verifier required')
        self._availability=verify_availability
        self._values=verify_captured_values

    def __call__(self, context, claims):
        # Run inside the original verifier boundary: no new late callback phase.
        try:
            if (not isinstance(context,(PreparationContext,TemporalPreparationContext))
                    or type(context.metadata) is not VerifiedTrainingMetadata
                    or type(claims) is not tuple or not claims
                    or any(type(c) is not AvailabilityClaim for c in claims)):
                raise ValueError()
            if self._availability(context,claims) is not True:raise ValueError()
            meta=context.metadata;mapping=meta.mapping
            mapping_digest=hashlib.sha256(meta.mapping_json.encode()).hexdigest()
            attested=[]
            for claim in claims:
                if claim.field!='value':continue
                if (claim.series_id not in context.series_ids or claim.value_identity is None
                        or claim.value_identity_version!=self.protocol_version):raise ValueError()
                attested.append(CapturedHistoryClaim(claim,meta.reference,meta.manifest_digest,
                    meta.admission_digest,meta.source_id,mapping_digest,
                    mapping.mapping_id,mapping.version,meta.reference.organization_id,mapping.target_metric,
                    mapping.measurement.unit,mapping.measurement.currency,meta.purpose,context.series_ids,
                    context.forecast_origin,context.training_cutoff,getattr(context,'plan_digest',None),
                    getattr(context,'fold_id',None),getattr(context,'target_period_end',None)))
            # Category-only calls still require their normal field-release
            # authority, but cannot substitute for target-value attestation.
            if attested and self._values(context,tuple(attested)) is not True:raise ValueError()
            return True
        except Exception:
            raise TrainingPreparationError('authoritative historical verification failed') from None


@dataclass(frozen=True)
class TemporalOriginResult:
    fold_id: str
    partition: str
    preparation: 'TrainingPreparationResult'
    plan_digest: str
    dataset_binding_json: str
    event_id: str


@dataclass(frozen=True)
class TemporalPreparationResult:
    """Service-owned detached H1 examples; no predictor or training permission."""
    plan_json: str
    origins: tuple[TemporalOriginResult, ...]
    preparation_id: str

    @property
    def matrix(self):
        frames=[]
        for group in self.origins:
            prepared=group.preparation;frame=prepared.matrix
            frame.index=pd.MultiIndex.from_tuples([
                (group.fold_id,org,series,prepared.schema.configuration.target_metric,
                 prepared.context.forecast_origin,1,period)
                for org,series,period in frame.index],names=(
                    'fold','organization_id','series_id','metric','forecast_origin','horizon_step','target_period'))
            frames.append(frame)
        return pd.concat(frames)

    @property
    def labels(self):
        return pd.Series([value for group in self.origins for value in group.preparation.converted_labels],
                         index=self.matrix.index,dtype='float64',name='value')

    @property
    def original_labels(self):
        return tuple(value for group in self.origins for value in group.preparation.original_labels)


@dataclass(frozen=True)
class PreparationBudgets:
    max_decoded_bytes: int = 16 * 1024 * 1024
    max_elapsed_seconds: int = 300

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in vars(self).values()):
            raise ValueError('positive preparation budgets required')


@dataclass(frozen=True)
class TrainingPreparationResult:
    """Detached immutable values; DataFrame/Series properties always return copies.

    Issued by the trusted service. Constructing this declaration does not grant
    model training permission; active entry points are intentionally unchanged.
    """
    context: PreparationContext
    state_json: str
    schema_json: str
    index: tuple[tuple[UUID, UUID, pd.Timestamp], ...]
    feature_rows: tuple[tuple, ...]
    converted_labels: tuple[float, ...]
    original_labels: tuple
    preparation_id: str

    @property
    def fitted_state(self):
        return FeatureState.model_validate_json(self.state_json)

    @property
    def schema(self):
        return FeatureSchemaCompatibility.model_validate_json(self.schema_json)

    @property
    def matrix(self):
        config=self.schema.configuration
        frame=pd.DataFrame(self.feature_rows,columns=config.ordered_outputs,
            index=pd.MultiIndex.from_tuples(self.index,names=('organization_id','series_id','period_start')))
        return frame.astype(dict(config.output_dtypes))

    @property
    def labels(self):
        return pd.Series(self.converted_labels,index=self.matrix.index,dtype='float64',name='value')


def _value_identity(value):
    if type(value) is Decimal:
        t=value.as_tuple();return ['decimal',t.sign,list(t.digits),t.exponent]
    if type(value) is pd.Timestamp:return ['timestamp',value.isoformat(),str(value.tzinfo)]
    if type(value) is bool:return ['boolean',value]
    if type(value) is int:return ['integer',str(value)]
    return ['float64',struct.pack('>d',float(value)).hex()]


class CanonicalTrainingPreparationService:
    """Trusted composition root injects verifiers; caller declarations are not trust.

    State is fitted here on a declared past partition, never accepted from callers.
    R-04C2-B owns predictor-dependent rollout; active training remains separate.
    """
    def __init__(self, provider, *, authorize_preparation, verify_availability,
                 authorize_external_forecast=None, elapsed_clock=monotonic):
        if type(provider) is not CanonicalTrainingDataProvider:
            raise TrainingPreparationError('trusted canonical provider required')
        self.provider=provider;self.authorize=authorize_preparation;self.verify_availability=verify_availability
        self.authorize_external=authorize_external_forecast;self.elapsed_clock=elapsed_clock

    @staticmethod
    def _approved(callback, *args):
        try: approved=callback(*args)
        except Exception: raise TrainingPreparationError('trusted preparation verification failed') from None
        if approved is not True:raise TrainingPreparationError('training preparation is not authorized')

    def prepare(self, reference, configuration, *, forecast_origin, training_cutoff,
                forecast_config, series_ids, purpose='production_training',
                feature_budgets=None, budgets=None, read_budgets=None, cancelled=lambda:False):
        try:
            return self._prepare(reference,configuration,forecast_origin=forecast_origin,
                training_cutoff=training_cutoff,forecast_config=forecast_config,series_ids=series_ids,
                purpose=purpose,feature_budgets=feature_budgets or FeatureBudgets(),
                budgets=budgets or PreparationBudgets(),read_budgets=read_budgets or StagingBudgets(),cancelled=cancelled)
        except Exception:
            raise TrainingPreparationError('canonical training preparation failed') from None

    @staticmethod
    def _temporal_binding(metadata,series_ids):
        """A declaration derived from verified metadata, not an access credential."""
        mapping=metadata.mapping
        return TemporalDatasetBinding(reference=metadata.reference,source_id=metadata.source_id,
            manifest_digest=metadata.manifest_digest,admission_digest=metadata.admission_digest,
            mapping_id=mapping.mapping_id,mapping_version=mapping.version,
            mapping_digest=hashlib.sha256(operational_json(mapping.model_dump(mode='json')).encode()).hexdigest(),
            quality_policy_id=metadata.policy_id,quality_policy_version=metadata.policy_version,
            series_definition_digest=hashlib.sha256(operational_json(metadata.series_json).encode()).hexdigest(),
            series_ids=series_ids)

    def prepare_temporal(self,plan,configuration,*,feature_budgets=None,read_budgets=None,cancelled=lambda:False):
        """One request-owned capture/version; one frozen state/fold/organization."""
        try:
            if type(plan) is not TemporalPreparationPlan:
                raise TrainingPreparationError('explicit temporal plan required')
            # Revalidate even model_copy/model_construct declarations; they grant no trust.
            plan=TemporalPreparationPlan.model_validate(plan.model_dump())
        except Exception:
            raise TrainingPreparationError('canonical temporal plan validation failed') from None
        if plan.capability!='supervised_h1' or plan.forecast_horizon!=1:
            raise TrainingPreparationError('temporal execution supports horizon one only; planning-only plans cannot execute')
        try:
            return self._prepare_temporal(plan,configuration,feature_budgets or FeatureBudgets(),
                                          read_budgets or StagingBudgets(),cancelled)
        except Exception:
            raise TrainingPreparationError('canonical temporal preparation failed') from None

    def _prepare_temporal(self,plan,configuration,feature_budgets,read_budgets,cancelled):
        engine=CanonicalFeaturePipeline(configuration,budgets=feature_budgets)
        b=plan.budgets;start=self.elapsed_clock();plan_json=plan.model_dump_json();digest=plan.digest
        def checkpoint():
            try: stopped=cancelled()
            except Exception:raise TrainingPreparationError('cancellation check failed') from None
            if type(stopped) is not bool or stopped or self.elapsed_clock()-start>=b.max_elapsed_seconds:
                raise TrainingPreparationError('temporal preparation interrupted or budget exhausted')
        checkpoint()
        if (plan.configuration_id!=engine.configuration.configuration_id
                or plan.configuration_version!=engine.configuration.version
                or plan.configuration_digest!=engine.digest
                or (plan.frequency,plan.calendar,plan.timezone,plan.period_anchor)!=(
                    engine.temporal.frequency.value,engine.temporal.calendar,engine.temporal.timezone,engine.temporal.period_anchor)):
            raise TrainingPreparationError('temporal configuration binding mismatch')
        origin_count=sum(len(f.origins) for f in plan.folds)
        cells=origin_count*sum(len(d.series_ids) for d in plan.datasets)*len(configuration.ordered_outputs)
        if cells>b.max_output_cells:
            raise TrainingPreparationError('aggregate feature output budget exceeded')
        # Calendar advancement is owned by the existing executor, never fixed UTC seconds.
        for fold in plan.folds:
            for date in (fold.calibration_start,fold.categorical_cutoff)+fold.origins:
                engine._aligned(engine._time(date))
            for origins,next_origins in ((fold.training_origins,fold.validation_origins+fold.test_origins),
                                         (fold.validation_origins,fold.test_origins)):
                if origins and next_origins and engine._advance(engine._time(origins[-1]),1)>engine._time(next_origins[0]):
                    raise TrainingPreparationError('partition target intervals overlap')
            if engine._advance(engine._time(fold.training_origins[-1]),1)>engine._time(fold.model_fit_cutoff):
                raise TrainingPreparationError('model-fit cutoff precedes training target completion')
        used={'rows':0,'decoded':0,'assembly':0,'work':0,'state':0}
        def account(kind,rows,size):
            if kind=='capture':
                used['rows']+=rows;used['decoded']+=size
            elif kind=='assembly':used['assembly']+=size
            elif kind=='work':used['work']+=size
            elif kind=='state':used['state']+=size
            if (used['rows']>b.max_captured_rows or used['decoded']>b.max_decoded_bytes
                    or used['assembly']>b.max_assembly_bytes or used['work']>b.max_work_cells
                    or used['state']>b.max_fitted_state_bytes):
                raise TrainingPreparationError('aggregate temporal budget exceeded')
        captures=[]
        for binding in plan.datasets:
            checkpoint()
            def context_factory(metadata):
                if self._temporal_binding(metadata,binding.series_ids)!=binding:
                    raise TrainingPreparationError('temporal dataset binding mismatch')
                if used['rows']+metadata.rows>b.max_captured_rows:
                    raise TrainingPreparationError('aggregate captured row budget exceeded')
                account('capture',0,len(metadata.mapping_json.encode())+sum(len(v.encode()) for v in metadata.series_json))
                return TemporalPreparationContext(metadata,engine.configuration.model_dump_json(),engine.digest,
                    binding.series_ids,plan_json,digest)
            metadata,context,frame,definitions=self._capture(binding.reference,engine,binding.series_ids,plan.purpose,
                context_factory,feature_budgets,PreparationBudgets(max_decoded_bytes=b.max_decoded_bytes,
                    max_elapsed_seconds=b.max_elapsed_seconds),read_budgets,checkpoint,account)
            account('assembly',0,int(frame.memory_usage(deep=True).sum()))
            account('work',0,len(frame)*len(frame.columns))
            claims,exogenous=self._availability(frame,engine,checkpoint)
            self._approved(self.verify_availability,context,claims)
            engine._rows(frame,observations=True)
            requests=pd.DataFrame([dict(organization_id=metadata.reference.organization_id,series_id=s,
                metric=metadata.mapping.target_metric,unit=metadata.mapping.measurement.unit,
                currency=metadata.mapping.measurement.currency,frequency=metadata.mapping.frequency.value,
                **{f:definitions[s].dimensions[f] for f in engine.rules.dimension_fields}) for s in binding.series_ids])
            captures.append((binding,metadata,frame,claims,exogenous,requests))
        groups=[];external=[];fold_checks=[];origin_checks=[]
        forecast=ForecastConfig(forecast_frequency=plan.frequency,forecast_horizon=1)
        for fold in plan.folds:
            checkpoint()
            for binding,metadata,frame,claims,exogenous,requests in captures:
                cutoff=engine._time(fold.categorical_cutoff)
                context=TemporalPreparationContext(metadata,engine.configuration.model_dump_json(),engine.digest,
                    binding.series_ids,plan_json,digest,fold.fold_id,engine._time(fold.origins[0]),cutoff)
                self.provider.require_current(metadata,budgets=read_budgets)
                self._approved(self.authorize,context,'fold');checkpoint()
                now=engine._time(self.provider.clock())
                if engine._time(fold.assessment_cutoff)>now or engine._time(fold.model_fit_cutoff)>now:
                    raise TrainingPreparationError('label assessment cannot claim future availability')
                self._approved(self.verify_availability,context,claims)
                # The explicitly declared prefix is validated intact; never silently
                # remove unavailable calibration rows or categories to make it fit.
                calibration_start=engine._time(fold.calibration_start)
                account('assembly',0,int(frame.memory_usage(deep=True).sum()))
                fit=frame[(frame.period_start>=calibration_start)&(frame.period_end<=cutoff)].copy()
                if fit.empty or any(engine._time(v)>cutoff for v in fit.available_at):
                    raise TrainingPreparationError('calibration observation unavailable')
                expected=[];period=calibration_start
                while period<cutoff:
                    if len(expected)>=feature_budgets.max_history_periods:
                        raise TrainingPreparationError('calibration history budget exceeded')
                    expected.append(period);period=engine._advance(period,1)
                if period!=cutoff:
                    raise TrainingPreparationError('calibration period bounds mismatch')
                for series in binding.series_ids:
                    periods=sorted(engine._time(v) for v in fit[fit.series_id==series].period_start)
                    if periods!=expected:raise TrainingPreparationError('calibration coverage incomplete')
                category_claims=self._verify_categories(fit,engine,context,cutoff,checkpoint)
                account('work',0,len(fit)*len(fit.columns))
                partition=hashlib.sha256(operational_json([digest,fold.fold_id,binding.model_dump(mode='json'),
                    engine.digest,calibration_start.isoformat(),cutoff.isoformat()]).encode()).hexdigest()
                state=engine.fit_state(fit,training_cutoff=cutoff,partition_reference=partition)
                state_json=state.model_dump_json();account('state',0,len(state_json.encode()))
                schema=describe_feature_schema(configuration,state,forecast_config=forecast,forecast_origin=engine._time(fold.origins[0]))
                schema_json=schema.model_dump_json()
                fold_checks.append((context,claims,category_claims))
                for partition_name,origins in (('training',fold.training_origins),('validation',fold.validation_origins),('test',fold.test_origins)):
                    for origin_date in origins:
                        checkpoint();origin=engine._time(origin_date)
                        current=TemporalPreparationContext(metadata,context.configuration_json,engine.digest,
                            binding.series_ids,plan_json,digest,fold.fold_id,origin,cutoff,engine._advance(origin,1))
                        self._approved(self.authorize,current,'origin')
                        self._approved(self.verify_availability,current,claims);checkpoint()
                        labels=frame[frame.period_start.eq(origin)]
                        limit=engine._time(fold.model_fit_cutoff if partition_name=='training' else fold.assessment_cutoff)
                        if (len(labels)!=len(binding.series_ids) or set(labels.series_id)!=set(binding.series_ids)
                                or any(engine._time(v)>limit for v in labels.period_end)
                                or any(engine._time(v)>limit for v in labels.available_at)):
                            raise TrainingPreparationError('complete labels unavailable at partition cutoff')
                        values={row['series_id']:row['value'] for row in labels.to_dict('records')}
                        originals=tuple(values[s] for s in binding.series_ids)
                        converted=tuple(engine.convert_numeric(v) for v in originals)
                        def verify_external(request):
                            if self.authorize_external is None:return False
                            self._approved(self.authorize_external,current,request)
                            external.append((current,request));return True
                        engine.authorize_external_forecast=verify_external
                        account('work',0,len(frame)*len(frame.columns))
                        account('assembly',0,int(frame.memory_usage(deep=True).sum()))
                        matrix=engine.prepare_training(frame,requests,state,forecast_origin=origin,
                            forecast_config=forecast,exogenous=exogenous)
                        account('assembly',0,int(matrix.memory_usage(deep=True).sum()))
                        verify_feature_schema(schema,configuration,state,matrix,requests=requests,
                            forecast_config=forecast,forecast_origin=origin)
                        index=tuple(matrix.index.tolist());feature_rows=tuple(matrix.itertuples(index=False,name=None))
                        body=[digest,fold.fold_id,partition_name,binding.model_dump(mode='json'),schema.schema_id,
                            origin.isoformat(),[[_value_identity(v) for v in row] for row in feature_rows],
                            [_value_identity(v) for v in originals]]
                        event_id=hashlib.sha256(operational_json(body).encode()).hexdigest()
                        prepared=TrainingPreparationResult(PreparationContext(metadata,current.configuration_json,engine.digest,
                            binding.series_ids,origin,cutoff,1),state_json,schema_json,index,feature_rows,converted,originals,event_id)
                        groups.append(TemporalOriginResult(fold.fold_id,partition_name,prepared,digest,binding.model_dump_json(),event_id))
                        origin_checks.append((current,claims))
        # No partial fold escapes. Every callback is rechecked against the same
        # bound plan and capture; the last operation before handoff is checkpoint.
        checkpoint()
        for context,claims,category_claims in fold_checks:
            self._approved(self.verify_availability,context,claims)
            if category_claims:self._approved(self.verify_availability,context,category_claims)
        for current,request in external:self._approved(self.authorize_external,current,request)
        for binding,metadata,frame,claims,exogenous,requests in captures:
            self._approved(self.authorize,TemporalPreparationContext(metadata,engine.configuration.model_dump_json(),
                engine.digest,binding.series_ids,plan_json,digest),'return')
            self.provider.require_current(metadata,budgets=read_budgets)
        # Retain the original immutable contexts and evidence tuples. A fold-first
        # or dataset-wide approval cannot substitute for a later origin's scope.
        if len(origin_checks)!=len(groups):
            raise TrainingPreparationError('incomplete origin verification scope')
        for (current,claims),group in zip(origin_checks,groups):
            prepared=group.preparation.context
            if (current.metadata!=prepared.metadata or current.series_ids!=prepared.series_ids
                    or current.fold_id!=group.fold_id or current.forecast_origin!=prepared.forecast_origin
                    or current.training_cutoff!=prepared.training_cutoff
                    or current.configuration_digest!=prepared.configuration_digest
                    or current.plan_digest!=digest or current.plan_json!=plan_json
                    or current.target_period_end!=engine._advance(prepared.forecast_origin,1)):
                raise TrainingPreparationError('origin verification binding mismatch')
        result=TemporalPreparationResult(plan_json,tuple(groups),hashlib.sha256(operational_json(
            [digest,[g.event_id for g in groups]]).encode()).hexdigest())
        # Broader callbacks and result assembly are complete. Only original scoped
        # availability/authorization checks, the checkpoint and return remain.
        for current,claims in origin_checks:
            self._approved(self.verify_availability,current,claims)
            self._approved(self.authorize,current,'return')
        checkpoint()
        return result

    def _prepare(self, reference, configuration, *, forecast_origin, training_cutoff,
                 forecast_config, series_ids, purpose, feature_budgets, budgets, read_budgets, cancelled):
        if type(budgets) is not PreparationBudgets or type(feature_budgets) is not FeatureBudgets:
            raise TrainingPreparationError('invalid preparation budgets')
        start=self.elapsed_clock()
        def checkpoint():
            try: stopped=cancelled()
            except Exception:raise TrainingPreparationError('cancellation check failed') from None
            if type(stopped) is not bool or stopped or self.elapsed_clock()-start>=budgets.max_elapsed_seconds:
                raise TrainingPreparationError('preparation interrupted or budget exhausted')
        checkpoint()
        engine=CanonicalFeaturePipeline(configuration,budgets=feature_budgets)
        origin=engine._time(forecast_origin);cutoff=engine._time(training_cutoff)
        engine._aligned(origin);engine._aligned(cutoff)
        if cutoff>origin:raise TrainingPreparationError('training partition exceeds origin')
        if type(forecast_config) is not ForecastConfig or type(forecast_config.forecast_horizon) is not int:
            raise TrainingPreparationError('explicit forecast configuration required')
        forecast=ForecastConfig.model_validate(forecast_config.model_dump())
        if forecast.forecast_horizon!=1 or forecast.forecast_frequency.value!=engine.temporal.frequency.value:
            raise TrainingPreparationError('only single-origin horizon-one supervision is supported')
        if (type(series_ids) is not tuple or not series_ids or len(series_ids)>feature_budgets.max_series
                or any(type(v) is not UUID for v in series_ids) or len(set(series_ids))!=len(series_ids)):
            raise TrainingPreparationError('explicit unique series scope required')
        selected=tuple(sorted(series_ids,key=str))
        metadata,context,frame,definitions=self._capture(reference,engine,selected,purpose,
            lambda metadata:PreparationContext(metadata,engine.configuration.model_dump_json(),engine.digest,selected,origin,cutoff,1),
            feature_budgets,budgets,read_budgets,checkpoint)
        mapping=metadata.mapping;rules=engine.rules
        claims,exogenous=self._availability(frame,engine,checkpoint)
        self._approved(self.verify_availability,context,tuple(claims))
        engine._rows(frame,observations=True)
        fit=frame[(frame.period_end<=cutoff)&(frame.available_at<=cutoff)].copy()
        # Retain the original single-origin final callback's complete claim tuple,
        # including the separately validated calibration category claims.
        claims+=self._verify_categories(fit,engine,context,cutoff,checkpoint)
        partition=hashlib.sha256(operational_json([metadata.reference.version_id,metadata.admission_digest,
            engine.digest,str(cutoff),[str(v) for v in selected]]).encode()).hexdigest()
        state=engine.fit_state(fit,training_cutoff=cutoff,partition_reference=partition)
        requests=pd.DataFrame([dict(organization_id=metadata.reference.organization_id,series_id=s,
            metric=mapping.target_metric,unit=mapping.measurement.unit,currency=mapping.measurement.currency,
            frequency=mapping.frequency.value,**{f:definitions[s].dimensions[f] for f in rules.dimension_fields}) for s in selected])
        now=engine._time(self.provider.clock())
        label_frame=frame[frame.period_start.eq(origin)]
        if (len(label_frame)!=len(selected) or set(label_frame.series_id)!=set(selected)
                or any(engine._time(v)>now for v in label_frame.available_at)
                or any(engine._time(v)>now for v in label_frame.period_end)):
            raise TrainingPreparationError('complete observed labels required')
        by_series={row['series_id']:row['value'] for row in label_frame.to_dict('records')}
        originals=tuple(by_series[s] for s in selected)
        labels=tuple(engine.convert_numeric(v) for v in originals)
        external_requests=[]
        def verify_external(request):
            if self.authorize_external is None:return False
            self._approved(self.authorize_external,context,request)
            external_requests.append(request)
            return True
        engine.authorize_external_forecast=verify_external
        matrix=engine.prepare_training(frame,requests,state,forecast_origin=origin,forecast_config=forecast,
            exogenous=exogenous)
        schema=describe_feature_schema(configuration,state,forecast_origin=origin,forecast_config=forecast)
        verify_feature_schema(schema,configuration,state,matrix,requests=requests,forecast_origin=origin,forecast_config=forecast)
        index=tuple(matrix.index.tolist());values=tuple(matrix.itertuples(index=False,name=None))
        body=[metadata.reference.version_id,metadata.admission_digest,state.state_id,schema.schema_id,
              [[str(o),str(s),t.isoformat()] for o,s,t in index],
              [[_value_identity(v) for v in row] for row in values],[_value_identity(v) for v in originals]]
        identity=hashlib.sha256(operational_json(body).encode()).hexdigest()
        result=TrainingPreparationResult(context,state.model_dump_json(),schema.model_dump_json(),index,values,labels,originals,identity)
        checkpoint()
        self._approved(self.verify_availability,context,tuple(claims))
        for request in external_requests:self._approved(self.authorize_external,context,request)
        self.provider.require_current(metadata,budgets=read_budgets)
        self._approved(self.authorize,context,'return')
        checkpoint()
        return result


    def _capture(self,reference,engine,selected,purpose,context_factory,
                 feature_budgets,budgets,read_budgets,checkpoint,account=None):
        """Shared owned read; no expensive transformation while store lock is held."""
        chunks=[];rows=decoded=0
        with self.provider.open_read(reference,purpose=purpose,budgets=read_budgets) as session:
            metadata=session.metadata;mapping=metadata.mapping
            rules=engine.rules
            configuration=engine.configuration
            if (metadata.rows>feature_budgets.max_rows or mapping.target_metric!=configuration.target_metric
                    or mapping.measurement!=rules.measurement or mapping.frequency!=engine.temporal.frequency
                    or mapping.timezone!=engine.temporal.timezone or set(mapping.identity_dimensions)!=set(rules.dimension_fields)
                    or mapping.metric_meaning!=rules.metric_meaning or mapping.sign_semantics!=rules.sign_semantics):
                raise TrainingPreparationError('publication and feature scope mismatch')
            definitions={v.series_id:v for v in metadata.series}
            if not set(selected)<=set(definitions):raise TrainingPreparationError('requested series missing')
            context=context_factory(metadata)
            self._approved(self.authorize,context,'read')
            with closing(session.batches()) as stream:
                for frame in stream:
                    checkpoint();engine._frame_budget(frame)
                    rows+=len(frame);decoded+=int(frame.memory_usage(deep=True).sum())
                    if rows>feature_budgets.max_rows or decoded>budgets.max_decoded_bytes:
                        raise TrainingPreparationError('decoded dataset budget exceeded')
                    if account is not None:account("capture",len(frame),int(frame.memory_usage(deep=True).sum()))
                    chunks.append(frame)
            if rows!=metadata.rows:raise TrainingPreparationError('incomplete dataset consumption')
        # Reader exhaustion, snapshot cleanup and lock release have ALL succeeded.
        checkpoint();self.provider.require_current(metadata,budgets=read_budgets)
        self._approved(self.authorize,context,'after_read')
        if account is not None:account("assembly",0,decoded)
        frame=pd.concat(chunks,ignore_index=True);chunks.clear();engine._frame_budget(frame)
        if set(frame.series_id)!=set(definitions):raise TrainingPreparationError('series metadata coverage mismatch')
        if not frame.value_status.eq('actual').all():raise TrainingPreparationError('actual observations required')
        frame=frame[frame.series_id.isin(selected)].copy()
        if 'organization_id' in frame:raise TrainingPreparationError('unverified organization shadowing')
        frame['organization_id']=metadata.reference.organization_id
        for field in rules.dimension_fields:
            if field in frame:raise TrainingPreparationError('unverified dimension shadowing')
            frame[field]=[definitions[s].dimensions[field] for s in frame.series_id]
        if frame.memory_usage(deep=True).sum()>budgets.max_decoded_bytes:
            raise TrainingPreparationError('enriched dataset budget exceeded')
        if len(frame)*(1+len(rules.exogenous))>feature_budgets.max_rows:
            raise TrainingPreparationError('availability and covariate budget exceeded')
        return metadata,context,frame,definitions

    def _availability(self,frame,engine,checkpoint):
        """Reuse field-scoped claims for single-origin and temporal preparation."""
        rules=engine.rules
        claims=[]
        def claim(row,field,available_field,evidence_field):
            if available_field not in row or evidence_field not in row:raise TrainingPreparationError('verified availability evidence required')
            available=engine._time(row[available_field]);evidence=row[evidence_field];_opaque(evidence)
            claims.append(AvailabilityClaim(row['series_id'],row['source_record_id'],row['lineage_id'],field,
                engine._time(row['period_start']),engine._time(row['period_end']),available,evidence,
                historical_value_identity(row['value']) if field=='value' else None,
                'captured_history_value_v1' if field=='value' else None))
            return available,evidence
        exogenous=[]
        for row in frame.to_dict('records'):
            checkpoint();claim(row,'value','available_at','availability_reference')
            for rule in rules.exogenous:
                if not rule.availability_field or not rule.evidence_field or rule.field not in row:
                    raise TrainingPreparationError('explicit exogenous availability mapping required')
                available,evidence=claim(row,rule.field,rule.availability_field,rule.evidence_field)
                exogenous.append(dict(organization_id=row['organization_id'],series_id=row['series_id'],metric=engine.configuration.target_metric,
                    field=rule.field,value=row[rule.field],period_start=row['period_start'],period_end=row['period_end'],
                    available_at=available,evidence_reference=evidence,unit=rule.measurement.unit if rule.measurement else None,
                    currency=rule.measurement.currency if rule.measurement else None))
        return tuple(claims),pd.DataFrame(exogenous) if exogenous else None

    def _verify_categories(self,fit,engine,context,cutoff,checkpoint):
        """Validate all categorical releases before any vocabulary fitting."""
        rules=engine.rules
        # Static identity dimensions were resolved from verified SeriesDefinition
        # metadata above. Every other categorical input needs its own verified
        # release evidence; target availability cannot authorize vocabulary fit.
        category_rules={rule.field:rule for rule in rules.exogenous}
        category_claims=[]
        for category in rules.categories:
            if category.field in rules.dimension_fields:
                continue
            rule=category_rules.get(category.field)
            if rule is None or not rule.availability_field or not rule.evidence_field:
                raise TrainingPreparationError('categorical availability mapping required')
            for row in fit.to_dict('records'):
                checkpoint()
                if category.field not in row:
                    raise TrainingPreparationError('categorical availability evidence required')
                if rule.availability_field not in row or rule.evidence_field not in row:
                    raise TrainingPreparationError('categorical availability evidence required')
                available=engine._time(row[rule.availability_field]);evidence=row[rule.evidence_field];_opaque(evidence)
                if available>cutoff:
                    raise TrainingPreparationError('categorical fit availability exceeds cutoff')
                category_claims.append(AvailabilityClaim(row['series_id'],row['source_record_id'],row['lineage_id'],category.field,
                    engine._time(row['period_start']),engine._time(row['period_end']),available,evidence))
        if category_claims:
            self._approved(self.verify_availability,context,tuple(category_claims))
        return tuple(category_claims)
