"""Versioned scoring declarations; construction grants no data or producer trust."""
from fractions import Fraction
from math import gcd
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictInt, model_validator

from .backtesting import Digest, ForecastScope, identity
from .dataset import _opaque
from .temporal_preparation import _TemporalContract, TemporalDatasetBinding, TemporalTimestamp


class BaselineDefinition(_TemporalContract):
    """Algorithm declaration only; approval comes from operator-owned C0 bootstrap."""
    version: Literal['1.0'] = '1.0'
    algorithm: Literal['last_observation_v1', 'seasonal_naive_v1']
    season_length: StrictInt | None = Field(default=None, ge=1, le=4096)
    lookback: StrictInt = Field(ge=1, le=4096)
    supported_horizon: StrictInt = Field(ge=1, le=52)

    @model_validator(mode='after')
    def explicit_period_lag(self):
        if self.algorithm == 'last_observation_v1':
            if self.season_length is not None:
                raise ValueError('last observation has no seasonal parameter')
        elif self.season_length is None or self.season_length > self.lookback:
            raise ValueError('explicit season within declared lookback required')
        return self

    @property
    def period_lag(self):
        return 1 if self.algorithm == 'last_observation_v1' else self.season_length

    @property
    def digest(self):
        return identity(self.model_dump(mode='json'))


class ScoringArithmeticPolicy(_TemporalContract):
    policy_id: str
    version: Literal['1.0'] = '1.0'
    interpretation: Literal['canonical_exact_v1'] = 'canonical_exact_v1'
    max_significant_digits: StrictInt = Field(default=512, ge=1, le=1024)
    max_abs_exponent: StrictInt = Field(default=1024, ge=0, le=2048)
    max_operand_bits: StrictInt = Field(default=4096, ge=1, le=4096)
    max_accumulator_bits: StrictInt = Field(default=8192, ge=1, le=8192)

    @model_validator(mode='after')
    def named(self):
        _opaque(self.policy_id)
        if self.max_accumulator_bits<self.max_operand_bits:raise ValueError('incompatible arithmetic bounds')
        return self

    @property
    def digest(self):return identity(self.model_dump(mode='json'))


class EvaluationBudgets(_TemporalContract):
    max_capture_sessions: StrictInt = Field(default=16, ge=1, le=16)
    max_captured_rows: StrictInt = Field(default=20000, ge=1)
    max_decoded_bytes: StrictInt = Field(default=64*1024*1024, ge=1)
    max_intermediate_bytes: StrictInt = Field(default=512*1024*1024, ge=1)
    max_verification_bytes: StrictInt = Field(default=32*1024*1024, ge=1)
    max_work_cells: StrictInt = Field(default=4000000, ge=1)
    max_prediction_events: StrictInt = Field(default=10000, ge=1, le=10000)
    max_scored_events: StrictInt = Field(default=10000, ge=1, le=10000)
    max_groups: StrictInt = Field(default=10000, ge=1, le=10000)
    max_result_bytes: StrictInt = Field(default=32*1024*1024, ge=1)
    max_elapsed_seconds: StrictInt = Field(default=300, ge=1)


class ForecastScoringPlan(_TemporalContract):
    plan_id: str
    version: Literal['1.0'] = '1.0'
    producer_plan_digest: Digest
    datasets: tuple[TemporalDatasetBinding,...] = Field(min_length=1,max_length=8)
    partitions: tuple[Literal['validation','test'],...] = Field(min_length=1,max_length=2)
    purpose: Literal['offline_forecast_evaluation'] = 'offline_forecast_evaluation'
    label_revision_policy: Literal['captured_value_as_of_E_v1'] = 'captured_value_as_of_E_v1'
    event_weighting: Literal['origin_scoped_v1','forecast_event_weighted_v1'] = 'origin_scoped_v1'
    output: Literal['scored_events_and_statistics_v1'] = 'scored_events_and_statistics_v1'
    arithmetic: ScoringArithmeticPolicy
    budgets: EvaluationBudgets = Field(default_factory=EvaluationBudgets)

    @model_validator(mode='after')
    def explicit(self):
        _opaque(self.plan_id)
        if self.partitions not in (('validation',),('test',),('validation','test')):
            raise ValueError('unique ordered held-out partitions required')
        orgs=tuple(d.reference.organization_id for d in self.datasets)
        if tuple(sorted(set(orgs),key=str))!=orgs:raise ValueError('one ordered version per organization required')
        if len(self.model_dump_json().encode())>128*1024:raise ValueError('scoring declaration too large')
        return self

    @property
    def digest(self):return identity(self.model_dump(mode='json'))


class ExactRatio(_TemporalContract):
    numerator: str = Field(pattern=r'^(0|[1-9][0-9]*)$',max_length=2500)
    denominator: str = Field(pattern=r'^[1-9][0-9]*$',max_length=2500)

    @model_validator(mode='after')
    def reduced_bounded(self):
        n,d=int(self.numerator),int(self.denominator)
        if max(n.bit_length(),d.bit_length())>8192 or gcd(n,d)!=1:
            raise ValueError('bounded reduced nonnegative ratio required')
        return self

    @property
    def fraction(self):return Fraction(int(self.numerator),int(self.denominator))


class ScoringStatistics(_TemporalContract):
    count: StrictInt = Field(ge=1,le=10000)
    sae: ExactRatio
    sse: ExactRatio
    saa: ExactRatio
    mae: ExactRatio
    mse: ExactRatio
    rmse_radicand: ExactRatio
    wape_status: Literal['defined','undefined_zero_denominator']
    wape: ExactRatio | None

    @model_validator(mode='after')
    def consistent(self):
        if self.mae.fraction!=self.sae.fraction/self.count or self.mse.fraction!=self.sse.fraction/self.count or self.rmse_radicand!=self.mse:
            raise ValueError('inconsistent sufficient statistics')
        if self.saa.fraction==0:
            if self.wape_status!='undefined_zero_denominator' or self.wape is not None:raise ValueError('zero denominator is undefined')
        elif self.wape_status!='defined' or self.wape is None or self.wape.fraction!=self.sae.fraction/self.saa.fraction:
            raise ValueError('inconsistent WAPE')
        return self


class ScoredEvent(_TemporalContract):
    """Generic verified forecast-point projection, without a model-family schema.

    Pure arithmetic contracts confer no admission. Source records, revision
    evidence and original labels remain inside the service-owned boundary.
    """
    scope: ForecastScope
    producer_event_id: Digest
    label_binding_digest: Digest
    scoring_plan_digest: Digest
    arithmetic_policy_digest: Digest
    assessment_cutoff: TemporalTimestamp
    partition: Literal['validation','test']
    statistics: ScoringStatistics

    @model_validator(mode='after')
    def one_observation(self):
        if self.statistics.count!=1 or self.assessment_cutoff.tzinfo is None or self.scope.target_period_end>self.assessment_cutoff:
            raise ValueError('invalid scored-event cutoff or count')
        return self

    @property
    def event_id(self):return identity(self.model_dump(mode='json'))


class ScoredGroup(_TemporalContract):
    fold_id: str
    organization_id: UUID
    series_id: UUID
    horizon_step: StrictInt = Field(ge=1,le=52)
    metric: str
    unit: str
    currency: str | None
    forecast_origin: TemporalTimestamp | None
    assessment_cutoff: TemporalTimestamp
    partition: Literal['validation','test']
    scoring_plan_digest: Digest
    arithmetic_policy_digest: Digest
    label_revision_policy: Literal['captured_value_as_of_E_v1']
    event_weighting: Literal['origin_scoped_v1','forecast_event_weighted_v1']
    event_ids: tuple[Digest,...] = Field(min_length=1,max_length=10000)
    statistics: ScoringStatistics

    @model_validator(mode='after')
    def complete_group(self):
        if len(set(self.event_ids))!=len(self.event_ids) or len(self.event_ids)!=self.statistics.count:
            raise ValueError('group coverage mismatch')
        if (self.forecast_origin is None)!=(self.event_weighting=='forecast_event_weighted_v1'):
            raise ValueError('explicit event weighting required')
        return self


class ForecastEvaluationResult(_TemporalContract):
    version: Literal['1.0'] = '1.0'
    scoring_plan: ForecastScoringPlan
    producer_result_id: Digest
    events: tuple[ScoredEvent,...] = Field(min_length=1,max_length=10000)
    groups: tuple[ScoredGroup,...] = Field(min_length=1,max_length=10000)

    @model_validator(mode='after')
    def coverage(self):
        ids=tuple(e.event_id for e in self.events)
        combined=tuple(i for g in self.groups for i in g.event_ids)
        if len(set(ids))!=len(ids) or len(combined)!=len(ids) or set(combined)!=set(ids):
            raise ValueError('evaluation group coverage mismatch')
        if any(e.scoring_plan_digest!=self.scoring_plan.digest or e.arithmetic_policy_digest!=self.scoring_plan.arithmetic.digest for e in self.events):
            raise ValueError('evaluation policy mismatch')
        return self

    @property
    def evaluation_id(self):return identity(self.model_dump(mode='json'))
