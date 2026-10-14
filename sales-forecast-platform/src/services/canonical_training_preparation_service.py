"""Bounded, single-origin/horizon-one preparation. No model fitting or CSV path."""
from dataclasses import dataclass
from contextlib import closing
from decimal import Decimal
import hashlib
import struct
from time import monotonic
from uuid import UUID

import pandas as pd

from src.data.contracts.dataset import StagingBudgets, operational_json, _opaque
from src.data.contracts.forecast import ForecastConfig
from src.data.contracts.features import FeatureState
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
    R-04C2 owns multi-origin supervision and model-generated recursive training.
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
        chunks=[];rows=decoded=0
        with self.provider.open_read(reference,purpose=purpose,budgets=read_budgets) as session:
            metadata=session.metadata;mapping=metadata.mapping
            rules=engine.rules
            if (metadata.rows>feature_budgets.max_rows or mapping.target_metric!=configuration.target_metric
                    or mapping.measurement!=rules.measurement or mapping.frequency!=engine.temporal.frequency
                    or mapping.timezone!=engine.temporal.timezone or set(mapping.identity_dimensions)!=set(rules.dimension_fields)
                    or mapping.metric_meaning!=rules.metric_meaning or mapping.sign_semantics!=rules.sign_semantics):
                raise TrainingPreparationError('publication and feature scope mismatch')
            definitions={v.series_id:v for v in metadata.series}
            if not set(selected)<=set(definitions):raise TrainingPreparationError('requested series missing')
            context=PreparationContext(metadata,engine.configuration.model_dump_json(),engine.digest,selected,origin,cutoff,1)
            self._approved(self.authorize,context,'read')
            with closing(session.batches()) as stream:
                for frame in stream:
                    checkpoint();engine._frame_budget(frame)
                    rows+=len(frame);decoded+=int(frame.memory_usage(deep=True).sum())
                    if rows>feature_budgets.max_rows or decoded>budgets.max_decoded_bytes:
                        raise TrainingPreparationError('decoded dataset budget exceeded')
                    chunks.append(frame)
            if rows!=metadata.rows:raise TrainingPreparationError('incomplete dataset consumption')
        # Reader exhaustion, snapshot cleanup and lock release have ALL succeeded.
        checkpoint();self.provider.require_current(metadata,budgets=read_budgets)
        self._approved(self.authorize,context,'after_read')
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
        claims=[]
        def claim(row,field,available_field,evidence_field):
            if available_field not in row or evidence_field not in row:raise TrainingPreparationError('verified availability evidence required')
            available=engine._time(row[available_field]);evidence=row[evidence_field];_opaque(evidence)
            claims.append(AvailabilityClaim(row['series_id'],row['source_record_id'],row['lineage_id'],field,
                engine._time(row['period_start']),engine._time(row['period_end']),available,evidence))
            return available,evidence
        exogenous=[]
        for row in frame.to_dict('records'):
            checkpoint();claim(row,'value','available_at','availability_reference')
            for rule in rules.exogenous:
                if not rule.availability_field or not rule.evidence_field or rule.field not in row:
                    raise TrainingPreparationError('explicit exogenous availability mapping required')
                available,evidence=claim(row,rule.field,rule.availability_field,rule.evidence_field)
                exogenous.append(dict(organization_id=metadata.reference.organization_id,series_id=row['series_id'],metric=mapping.target_metric,
                    field=rule.field,value=row[rule.field],period_start=row['period_start'],period_end=row['period_end'],
                    available_at=available,evidence_reference=evidence,unit=rule.measurement.unit if rule.measurement else None,
                    currency=rule.measurement.currency if rule.measurement else None))
        self._approved(self.verify_availability,context,tuple(claims))
        engine._rows(frame,observations=True)
        fit=frame[(frame.period_end<=cutoff)&(frame.available_at<=cutoff)].copy()
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
                available,_=claim(row,category.field,rule.availability_field,rule.evidence_field)
                if available>cutoff:
                    raise TrainingPreparationError('categorical fit availability exceeds cutoff')
                category_claims.append(claims[-1])
        if category_claims:
            self._approved(self.verify_availability,context,tuple(category_claims))
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
            exogenous=pd.DataFrame(exogenous) if exogenous else None)
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
