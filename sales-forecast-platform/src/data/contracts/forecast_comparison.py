"""C1 label-free declarations. Neither diagnostics nor hashes confer ownership."""
from typing import Literal
from uuid import UUID
from pydantic import Field, StrictInt, model_validator
from .backtesting import Digest, ForecastEvaluationPlan, ForecastScope, DeterministicRolloutResult, identity
from .dataset import _opaque
from .temporal_preparation import _TemporalContract, TemporalBudgets, TemporalDatasetBinding, TemporalTimestamp


class JointPredictionBudgets(TemporalBudgets):
    max_work_cells: StrictInt = Field(default=4000000, ge=1)
    max_assembly_bytes: StrictInt = Field(default=512*1024*1024, ge=1)
    max_fitted_state_bytes: StrictInt = Field(default=4*1024*1024, ge=1)
    max_predictor_calls: StrictInt = Field(default=10000, ge=1, le=10000)
    max_prediction_events: StrictInt = Field(default=10000, ge=1, le=10000)
    max_prefix_cells: StrictInt = Field(default=200000, ge=1)
    max_prediction_bytes: StrictInt = Field(default=32*1024*1024, ge=1)
    max_representation_bytes: StrictInt = Field(default=64*1024*1024, ge=1)
    max_feedback_bytes: StrictInt = Field(default=8*1024*1024, ge=1)
    max_verification_bytes: StrictInt = Field(default=128*1024*1024, ge=1)
    max_verification_claims: StrictInt = Field(default=2000000, ge=1)
    max_verification_calls: StrictInt = Field(default=10000, ge=1)
    max_profile_bytes: StrictInt = Field(default=256*1024, ge=1)
    max_output_bytes: StrictInt = Field(default=32*1024*1024, ge=1)


class JointProducerPlan(_TemporalContract):
    role: Literal['candidate', 'baseline']
    profile_id: str
    plan: ForecastEvaluationPlan

    @model_validator(mode='after')
    def named(self):
        _opaque(self.profile_id)
        return self


class JointPredictionPlan(_TemporalContract):
    version: Literal['1.0'] = '1.0'
    plan_id: str
    purpose: Literal['offline_forecast_evaluation'] = 'offline_forecast_evaluation'
    producers: tuple[JointProducerPlan, ...] = Field(min_length=2, max_length=2)
    budgets: JointPredictionBudgets = Field(default_factory=JointPredictionBudgets)

    @model_validator(mode='after')
    def exact_pair(self):
        _opaque(self.plan_id)
        if tuple(p.role for p in self.producers) != ('candidate', 'baseline'):
            raise ValueError('one candidate followed by one baseline required')
        if len({p.profile_id for p in self.producers}) != 2:
            raise ValueError('distinct approved producer profiles required')
        first = self.producers[0].plan
        fields = ('datasets','folds','frequency','calendar','timezone','period_anchor','forecast_horizon','purpose')
        if any(getattr(first,k) != getattr(self.producers[1].plan,k) for k in fields):
            raise ValueError('identical common temporal/publication grid required')
        if any(p.plan.predictor.strategy != 'recursive_one_step' or
               p.plan.predictor.representation_kind != 'tabular_step' for p in self.producers):
            raise ValueError('C1 supports synthetic recursive tabular execution only')
        b = self.budgets
        origins = sum(len(f.origins) for f in first.folds)
        series = sum(len(d.series_ids) for d in first.datasets)
        if (len(first.datasets)>min(b.max_organizations,b.max_dataset_versions) or series>b.max_series
                or len(first.folds)>b.max_folds or origins*len(first.datasets)>b.max_origins
                or 2*origins*len(first.datasets)*first.forecast_horizon>b.max_predictor_calls
                or 2*origins*series*first.forecast_horizon>b.max_prediction_events):
            raise ValueError('joint declaration geometry exceeds budget')
        if len(self.model_dump_json().encode())>256*1024:
            raise ValueError('bounded joint declaration required')
        return self

    @property
    def digest(self):
        return identity(self.model_dump(mode='json'))


class JointForecastKey(_TemporalContract):
    """Semantic forecast identity; deliberately excludes producer plan/state IDs."""
    dataset: TemporalDatasetBinding
    fold_id: str
    partition: Literal['training','validation','test']
    organization_id: UUID
    series_id: UUID
    dimensions: tuple[tuple[str,str], ...]
    metric: str
    unit: str
    currency: str | None
    frequency: Literal['daily','weekly']
    calendar: Literal['gregorian']
    timezone: str
    period_anchor: Literal['start','end']
    forecast_origin: TemporalTimestamp
    horizon_step: StrictInt = Field(ge=1,le=52)
    target_period: TemporalTimestamp
    target_period_end: TemporalTimestamp
    assessment_cutoff: TemporalTimestamp

    @model_validator(mode='after')
    def scoped(self):
        fields = ('fold_id','organization_id','series_id','dimensions','metric','unit','currency','frequency',
                  'timezone','forecast_origin','horizon_step','target_period','target_period_end')
        ForecastScope(plan_digest='0'*64,dataset_version=self.dataset.reference.version_id,
                      **{k:getattr(self,k) for k in fields})
        if (self.organization_id != self.dataset.reference.organization_id
                or self.series_id not in self.dataset.series_ids
                or self.assessment_cutoff.tzinfo is None or self.target_period_end>self.assessment_cutoff):
            raise ValueError('joint key scope mismatch')
        return self

    @property
    def key_id(self):
        return identity(self.model_dump(mode='json'))


class ProducerPredictionDiagnostic(_TemporalContract):
    profile_id: str
    result_json: str

    @model_validator(mode='after')
    def valid(self):
        _opaque(self.profile_id)
        DeterministicRolloutResult.model_validate_json(self.result_json)
        return self

    @property
    def result(self):
        return DeterministicRolloutResult.model_validate_json(self.result_json)


class JointPredictionDiagnostic(_TemporalContract):
    """Detached label-free output, NEVER an accepted capture/seal/scoring input."""
    version: Literal['1.0'] = '1.0'
    plan_digest: Digest
    capture_ids: tuple[Digest, ...]
    grid: tuple[JointForecastKey, ...]
    producers: tuple[ProducerPredictionDiagnostic, ...] = Field(min_length=2,max_length=2)
    admission: tuple[tuple[str,int], ...]
    usage_phase: Literal['before_terminal'] = 'before_terminal'
    usage: tuple[tuple[str,int], ...]

    @model_validator(mode='after')
    def coverage(self):
        if not self.grid or len({k.key_id for k in self.grid})!=len(self.grid):
            raise ValueError('complete unique joint grid required')
        if len({p.profile_id for p in self.producers})!=2:
            raise ValueError('distinct diagnostic profiles required')
        return self

    @property
    def diagnostic_id(self):
        return identity(self.model_dump(mode='json'))
