"""M8 test-only declarations. Neither construction nor a digest grants authority."""
from fractions import Fraction
from typing import Literal
from uuid import UUID
from pydantic import Field, StrictInt, model_validator
from src.data.contracts.backtesting import ForecastScope, identity
from src.data.contracts.candidate_package import Digest
from src.data.contracts.model_representation import RepresentationContract
from src.data.contracts.temporal_preparation import TemporalPreparationPlan, TemporalDatasetBinding
from src.data.contracts.training_adapter import Family, FiniteFloat
from src.data.contracts.forecast_evaluation import ExactRatio, ScoringStatistics
from src.data.contracts.forecast_comparison import SignedRatio


class EvaluationKey(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    scope: ForecastScope
    partition: Literal['validation', 'test']
    transformation: Literal['identity_target_v1'] = 'identity_target_v1'

    @model_validator(mode='after')
    def h1(self):
        if self.scope.horizon_step != 1 or self.scope.target_period != self.scope.forecast_origin:
            raise ValueError('H1 origin target required')
        return self

    @property
    def key_id(self):return identity(self.model_dump(mode='json', warnings='error'))


def key_order(key):
    from datetime import timezone
    s=key.scope
    return (s.fold_id, str(s.organization_id), s.forecast_origin.astimezone(timezone.utc).isoformat(),
            str(s.series_id), key.key_id)


class CandidateEvidence(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    candidate_id: Digest
    family: Family
    package_id: Digest
    representation_binding_id: Digest
    representation_compatibility_id: Digest
    fitted_state_id: Digest
    preprocessing_schema_id: Digest
    evidence: Literal['TEST_ONLY'] = 'TEST_ONLY'
    deployability: Literal['TEST_ONLY_NON_DEPLOYABLE'] = 'TEST_ONLY_NON_DEPLOYABLE'


class PredictionObservation(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    source: CandidateEvidence
    key: EvaluationKey
    value: FiniteFloat


class GroundTruthObservation(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    key: EvaluationKey
    value: FiniteFloat
    revision_claim_id: Digest
    evidence: Literal['TEST_ONLY_AUTHORIZED_PUBLICATION'] = 'TEST_ONLY_AUTHORIZED_PUBLICATION'


class CandidatePredictionBatch(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    source: CandidateEvidence
    observations: tuple[PredictionObservation, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode='after')
    def ordered(self):
        keys=tuple(p.key for p in self.observations)
        if (any(p.source!=self.source for p in self.observations) or len({k.key_id for k in keys})!=len(keys)
                or keys!=tuple(sorted(keys,key=key_order))):raise ValueError('exact ordered producer keys required')
        return self

    @property
    def batch_id(self):return identity(self.model_dump(mode='json', warnings='error'))


class EnsembleConfiguration(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    mode: Literal['equal_mean_v1', 'fixed_mean_v1']
    members: tuple[Digest, ...] = Field(min_length=2, max_length=8)
    weights: tuple[ExactRatio, ...] = Field(default=(), max_length=8)
    output_policy: Literal['exact_rational_v1'] = 'exact_rational_v1'

    @model_validator(mode='after')
    def valid(self):
        if self.members!=tuple(sorted(set(self.members))):raise ValueError('explicit sorted unique membership required')
        if self.mode=='equal_mean_v1':
            if self.weights:raise ValueError('equal mode has derived weights')
        elif len(self.weights)!=len(self.members) or sum((w.fraction for w in self.weights),Fraction())!=1:
            raise ValueError('exact nonnegative weights summing to one required')
        return self

    @property
    def effective_weights(self):
        return ((Fraction(1,len(self.members)),)*len(self.members) if self.mode=='equal_mean_v1' else
                tuple(w.fraction for w in self.weights))

    @property
    def configuration_id(self):return identity(self.model_dump(mode='json', warnings='error'))


class EvaluationResources(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    max_keys: StrictInt = Field(default=256, ge=1, le=256)
    max_capture_rows: StrictInt = Field(default=1024, ge=1, le=4096)
    max_decoded_bytes: StrictInt = Field(default=4*1024*1024, ge=1, le=8*1024*1024)
    max_intermediate_bytes: StrictInt = Field(default=8*1024*1024, ge=1, le=16*1024*1024)
    max_work: StrictInt = Field(default=500000, ge=1, le=1000000)
    max_groups: StrictInt = Field(default=128, ge=1, le=256)
    max_output_bytes: StrictInt = Field(default=1024*1024, ge=1, le=2*1024*1024)
    max_elapsed_seconds: StrictInt = Field(default=30, ge=1, le=60)


class CandidateEvaluationRequest(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    plan: TemporalPreparationPlan
    evaluation: TemporalDatasetBinding
    partition: Literal['validation', 'test']
    coverage: Literal['exact_grid_v1', 'declared_common_subset_v1'] = 'exact_grid_v1'
    keys: tuple[EvaluationKey, ...] = Field(min_length=1, max_length=256)
    batches: tuple[CandidatePredictionBatch, ...] = Field(min_length=2, max_length=8)
    ensemble: EnsembleConfiguration
    resources: EvaluationResources = Field(default_factory=EvaluationResources)
    evidence: Literal['TEST_ONLY'] = 'TEST_ONLY'
    genuine_model_evidence: Literal['UNAVAILABLE'] = 'UNAVAILABLE'

    @model_validator(mode='after')
    def aligned(self):
        ids=tuple(b.source.candidate_id for b in self.batches)
        if ids!=tuple(sorted(set(ids))) or self.ensemble.members!=ids:raise ValueError('exact ensemble membership required')
        if (len(self.keys)>self.resources.max_keys or self.keys!=tuple(sorted(self.keys,key=key_order))
                or len({k.key_id for k in self.keys})!=len(self.keys)
                or any(k.partition!=self.partition for k in self.keys)
                or any(tuple(p.key for p in b.observations)!=self.keys for b in self.batches)):
            raise ValueError('complete exact ordered common keys required')
        if len(self.plan.folds)!=1 or len(self.plan.datasets)!=1 or self.plan.forecast_horizon!=1:
            raise ValueError('single-fold single-publication H1 required')
        return self

    @property
    def request_id(self):return identity(self.model_dump(mode='json', warnings='error'))


class MetricResult(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    producer_id: Digest
    population_id: Digest
    statistics: ScoringStatistics
    smape: ExactRatio
    mean_signed_error: SignedRatio
    policy: Literal['m8_exact_metrics_v1'] = 'm8_exact_metrics_v1'

    @model_validator(mode='after')
    def ranges(self):
        if self.smape.fraction>2 or abs(self.mean_signed_error.fraction)>self.statistics.mae.fraction:
            raise ValueError('invalid metric range')
        return self


class SeriesMetric(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    series_id: UUID
    metric: MetricResult


class EnsemblePoint(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    key: EvaluationKey
    value: SignedRatio


class EnsembleResult(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    configuration: EnsembleConfiguration
    points: tuple[EnsemblePoint, ...] = Field(min_length=1, max_length=256)
    metric: MetricResult
    evidence: Literal['TEST_ONLY'] = 'TEST_ONLY'

    @model_validator(mode='after')
    def bound(self):
        keys=tuple(p.key for p in self.points)
        if (self.metric.producer_id!=self.configuration.configuration_id or self.metric.statistics.count!=len(keys)
                or keys!=tuple(sorted(keys,key=key_order)) or len({k.key_id for k in keys})!=len(keys)):
            raise ValueError('exact ensemble result required')
        return self


class MultiCandidateComparison(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    status: Literal['TEST_ONLY_NOT_MODEL_APPROVAL'] = 'TEST_ONLY_NOT_MODEL_APPROVAL'
    evidence: Literal['TEST_ONLY'] = 'TEST_ONLY'
    deployability: Literal['TEST_ONLY_NON_DEPLOYABLE'] = 'TEST_ONLY_NON_DEPLOYABLE'
    genuine_model_evidence: Literal['UNAVAILABLE'] = 'UNAVAILABLE'
    request_id: Digest
    evaluation: TemporalDatasetBinding
    coverage: Literal['exact_grid_v1', 'declared_common_subset_v1']
    eligible_count: StrictInt = Field(ge=1, le=131072)
    evaluated_count: StrictInt = Field(ge=1, le=256)
    excluded_count: StrictInt = Field(ge=0, le=131072)
    keys: tuple[EvaluationKey, ...] = Field(min_length=1, max_length=256)
    sources: tuple[CandidateEvidence, ...] = Field(min_length=2, max_length=8)
    prediction_batch_ids: tuple[Digest, ...] = Field(min_length=2, max_length=8)
    truth_revision_id: Digest
    population_id: Digest
    metrics: tuple[MetricResult, ...] = Field(min_length=2, max_length=8)
    series_metrics: tuple[SeriesMetric, ...] = Field(min_length=1, max_length=256)
    ranking: tuple[Digest, ...] = Field(min_length=2, max_length=8)
    ensemble: EnsembleResult

    @model_validator(mode='after')
    def consistent(self):
        ids=tuple(s.candidate_id for s in self.sources)
        if (ids!=tuple(sorted(set(ids))) or tuple(m.producer_id for m in self.metrics)!=ids
                or len(self.prediction_batch_ids)!=len(ids) or self.evaluated_count!=len(self.keys)
                or self.eligible_count!=self.evaluated_count+self.excluded_count
                or self.coverage=='exact_grid_v1' and self.excluded_count
                or any(m.population_id!=self.population_id or m.statistics.count!=self.evaluated_count for m in self.metrics)
                or self.ensemble.configuration.members!=ids or tuple(p.key for p in self.ensemble.points)!=self.keys
                or self.ensemble.metric.population_id!=self.population_id):raise ValueError('consistent comparison required')
        if self.population_id!=identity([[k.key_id for k in self.keys],self.truth_revision_id]):
            raise ValueError('exact population identity required')
        series=tuple(sorted({k.scope.series_id for k in self.keys},key=str))
        producers=tuple(self.metrics)+(self.ensemble.metric,)
        expected_groups=tuple((m.producer_id,sid) for m in producers for sid in series)
        if tuple((g.metric.producer_id,g.series_id) for g in self.series_metrics)!=expected_groups:
            raise ValueError('complete deterministic series groups required')
        for overall in producers:
            groups=tuple(g for g in self.series_metrics if g.metric.producer_id==overall.producer_id)
            for group in groups:
                keys=tuple(k for k in self.keys if k.scope.series_id==group.series_id)
                if (group.metric.population_id!=identity([self.population_id,str(group.series_id),[k.key_id for k in keys]])
                        or group.metric.statistics.count!=len(keys)):raise ValueError('exact group population required')
            for field in ('sae','sse','saa'):
                if sum((getattr(g.metric.statistics,field).fraction for g in groups),Fraction())!=getattr(overall.statistics,field).fraction:
                    raise ValueError('exact group accumulation required')
            if (sum((g.metric.smape.fraction*g.metric.statistics.count for g in groups),Fraction())/self.evaluated_count!=overall.smape.fraction
                    or sum((g.metric.mean_signed_error.fraction*g.metric.statistics.count for g in groups),Fraction())/self.evaluated_count!=overall.mean_signed_error.fraction):
                raise ValueError('exact group means required')
        expected=tuple(m.producer_id for m in sorted(self.metrics,key=lambda m:(m.statistics.mae.fraction,m.producer_id)))
        if self.ranking!=expected:raise ValueError('exact MAE stable ranking required')
        return self

    @property
    def report_id(self):return identity(self.model_dump(mode='json',warnings='error'))
