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


# C2 is opt-in and validation-only. None of these declarations grants ownership.
from fractions import Fraction
from math import gcd
from .forecast_evaluation import (ScoringArithmeticPolicy, ScoringStatistics,
                                  ForecastEvaluationResult)


class SignedRatio(_TemporalContract):
    """Canonical reduced signed rational; bounded before gcd/Fraction work."""
    numerator: str = Field(pattern=r'^(0|-?[1-9][0-9]*)$', max_length=2501)
    denominator: str = Field(pattern=r'^[1-9][0-9]*$', max_length=2500)

    @model_validator(mode='after')
    def reduced(self):
        n, d = int(self.numerator), int(self.denominator)
        if max(abs(n).bit_length(), d.bit_length()) > 8192 or gcd(n, d) != 1:
            raise ValueError('bounded reduced signed ratio required')
        return self

    @property
    def fraction(self):
        return Fraction(int(self.numerator), int(self.denominator))


class JointComparisonPolicy(_TemporalContract):
    version: Literal['1.0'] = '1.0'
    policy_id: str
    partitions: tuple[Literal['validation'], ...] = ('validation',)
    event_weighting: Literal['origin_scoped_v1', 'forecast_event_weighted_v1'] = 'origin_scoped_v1'
    label_revision_policy: Literal['captured_value_as_of_E_v1'] = 'captured_value_as_of_E_v1'
    arithmetic: ScoringArithmeticPolicy

    @model_validator(mode='after')
    def named(self):
        _opaque(self.policy_id)
        if self.partitions != ('validation',):
            raise ValueError('validation-only comparison required')
        return self

    @property
    def digest(self):
        return identity(self.model_dump(mode='json'))


class JointScoringBudgets(_TemporalContract):
    """Additional caps; C1's request-wide counters and clock also remain active."""
    max_capture_sessions: StrictInt = Field(default=16, ge=1, le=16)
    max_truth_points: StrictInt = Field(default=10000, ge=1, le=10000)
    max_scored_events: StrictInt = Field(default=20000, ge=1, le=20000)
    max_score_groups: StrictInt = Field(default=20000, ge=1, le=20000)
    max_arithmetic_work: StrictInt = Field(default=10000000000000, ge=1)


class JointComparisonPlan(_TemporalContract):
    version: Literal['1.0'] = '1.0'
    plan_id: str
    predictions: JointPredictionPlan
    policy_digest: Digest
    budgets: JointScoringBudgets = Field(default_factory=JointScoringBudgets)

    @model_validator(mode='after')
    def validation_only(self):
        _opaque(self.plan_id)
        first = self.predictions.producers[0].plan
        if any(not f.validation_origins or f.test_origins for f in first.folds):
            raise ValueError('explicit validation origins and no test partition required')
        count = sum(len(f.validation_origins) for f in first.folds) * sum(len(d.series_ids) for d in first.datasets) * first.forecast_horizon
        if (count > self.budgets.max_truth_points or 2*count > self.budgets.max_scored_events
                or 2*len(first.datasets) > self.budgets.max_capture_sessions):
            raise ValueError('joint scoring geometry exceeds budget')
        if len(self.model_dump_json().encode()) > 256*1024:
            raise ValueError('bounded comparison declaration required')
        return self

    @property
    def digest(self):
        return identity(self.model_dump(mode='json'))


class JointTruthPoint(_TemporalContract):
    key: JointForecastKey
    label_binding_digest: Digest
    original_value_identity_json: str = Field(max_length=16384)
    source_record_id: str
    lineage_id: UUID
    available_at: TemporalTimestamp
    evidence_reference: str

    @model_validator(mode='after')
    def scoped(self):
        _opaque(self.source_record_id); _opaque(self.evidence_reference)
        if (self.key.partition != 'validation' or self.available_at.tzinfo is None
                or self.available_at > self.key.assessment_cutoff):
            raise ValueError('eligible validation truth required')
        return self

    @property
    def truth_id(self):
        return identity(self.model_dump(mode='json'))


class JointProducerScore(_TemporalContract):
    role: Literal['candidate', 'baseline']
    profile_id: str
    evaluation_json: str

    @property
    def evaluation(self):
        return ForecastEvaluationResult.model_validate_json(self.evaluation_json)

    @model_validator(mode='after')
    def valid(self):
        _opaque(self.profile_id)
        self.evaluation
        return self


class JointScoreDelta(_TemporalContract):
    """Candidate minus baseline, never percentage improvement or RMSE delta."""
    semantic_group_id: Digest
    forecast_key_ids: tuple[Digest, ...] = Field(min_length=1, max_length=10000)
    candidate: ScoringStatistics
    baseline: ScoringStatistics
    sae: SignedRatio
    sse: SignedRatio
    mae: SignedRatio
    mse: SignedRatio
    wape_status: Literal['defined', 'undefined_zero_denominator']
    wape: SignedRatio | None

    @model_validator(mode='after')
    def exact_difference(self):
        a, b = self.candidate, self.baseline
        if (tuple(sorted(set(self.forecast_key_ids))) != self.forecast_key_ids
                or a.count != b.count or a.count != len(self.forecast_key_ids)
                or a.saa != b.saa or a.wape_status != b.wape_status):
            raise ValueError('common truth/group denominator mismatch')
        for field in ('sae', 'sse', 'mae', 'mse'):
            if getattr(self, field).fraction != getattr(a, field).fraction-getattr(b, field).fraction:
                raise ValueError('incorrect signed difference')
        if self.wape_status != a.wape_status:
            raise ValueError('inconsistent WAPE status')
        if a.wape is None:
            if self.wape is not None: raise ValueError('undefined WAPE has no delta')
        elif self.wape is None or self.wape.fraction != a.wape.fraction-b.wape.fraction:
            raise ValueError('incorrect WAPE difference')
        return self


class JointComparisonResult(_TemporalContract):
    """Detached result, never an input receipt for a future trusted execution."""
    version: Literal['1.0'] = '1.0'
    plan: JointComparisonPlan
    policy: JointComparisonPolicy
    label_authority_digest: Digest
    truths: tuple[JointTruthPoint, ...] = Field(min_length=1, max_length=10000)
    producers: tuple[JointProducerScore, ...] = Field(min_length=2, max_length=2)
    deltas: tuple[JointScoreDelta, ...] = Field(min_length=1, max_length=10000)
    admission: tuple[tuple[str, int], ...]
    usage_phase: Literal['before_terminal'] = 'before_terminal'
    usage: tuple[tuple[str, int], ...]

    @model_validator(mode='after')
    def complete(self):
        keys = tuple(t.key.key_id for t in self.truths)
        if (self.plan.policy_digest != self.policy.digest or len(set(keys)) != len(keys)
                or tuple(p.role for p in self.producers) != ('candidate', 'baseline')):
            raise ValueError('comparison identity mismatch')
        claims = {t.key.key_id: t.label_binding_digest for t in self.truths}
        covered = tuple(k for d in self.deltas for k in d.forecast_key_ids)
        if len(covered) != len(keys) or set(covered) != set(keys):
            raise ValueError('comparison group coverage mismatch')
        if tuple(sorted(d.semantic_group_id for d in self.deltas)) != tuple(d.semantic_group_id for d in self.deltas) or len({d.semantic_group_id for d in self.deltas})!=len(self.deltas):
            raise ValueError('unique deterministic comparison groups required')
        group_statistics=[]
        for producer, approved in zip(self.producers, self.plan.predictions.producers):
            evaluation = producer.evaluation
            if (producer.profile_id != approved.profile_id or len(evaluation.events) != len(keys)
                    or evaluation.scoring_plan.producer_plan_digest != approved.plan.digest
                    or evaluation.scoring_plan.datasets != approved.plan.datasets
                    or evaluation.scoring_plan.arithmetic != self.policy.arithmetic
                    or evaluation.scoring_plan.partitions != self.policy.partitions
                    or evaluation.scoring_plan.event_weighting != self.policy.event_weighting):
                raise ValueError('producer scoring provenance mismatch')
            point_keys={};seen=set()
            for event in evaluation.events:
                scope=event.scope
                binding=next(d for d in approved.plan.datasets if d.reference.organization_id==scope.organization_id)
                key=JointForecastKey(dataset=binding,partition=event.partition,calendar=approved.plan.calendar,
                    period_anchor=approved.plan.period_anchor,assessment_cutoff=event.assessment_cutoff,
                    **{f:getattr(scope,f) for f in ('fold_id','organization_id','series_id','dimensions','metric','unit','currency',
                        'frequency','timezone','forecast_origin','horizon_step','target_period','target_period_end')})
                if key.key_id in seen or claims.get(key.key_id)!=event.label_binding_digest:
                    raise ValueError('producer/truth semantic alignment mismatch')
                seen.add(key.key_id);point_keys[event.event_id]=key.key_id
            if seen!=set(keys):raise ValueError('incomplete semantic score grid')
            groups={}
            for group in evaluation.groups:
                members=tuple(sorted(point_keys[k] for k in group.event_ids))
                semantic=(group.fold_id,str(group.organization_id),str(group.series_id),group.horizon_step,
                    group.metric,group.unit,group.currency or '',group.partition,group.assessment_cutoff.isoformat(),
                    group.forecast_origin.isoformat() if group.forecast_origin is not None else '')
                group_id=identity([self.policy.digest,semantic,members])
                if group_id in groups:raise ValueError('ambiguous semantic score group')
                groups[group_id]=(members,group.statistics)
            group_statistics.append(groups)
        wanted={d.semantic_group_id for d in self.deltas}
        if set(group_statistics[0])!=wanted or set(group_statistics[1])!=wanted:
            raise ValueError('incomplete comparison groups')
        for delta in self.deltas:
            if (group_statistics[0][delta.semantic_group_id]!=(delta.forecast_key_ids,delta.candidate)
                    or group_statistics[1][delta.semantic_group_id]!=(delta.forecast_key_ids,delta.baseline)):
                raise ValueError('comparison deltas differ from owned score groups')
        return self

    @property
    def comparison_id(self):
        return identity(self.model_dump(mode='json'))
