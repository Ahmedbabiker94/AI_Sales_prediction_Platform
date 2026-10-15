"""Model-independent declarations. None is admission or fitted-model provenance.

B1 supports trusted in-process synthetic callbacks, not a Python security sandbox.
Other model representations are declarations only; no wire serialization protocol
or general adapter/plugin framework is implied.
"""
import hashlib
import math
import struct
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import numpy as np
import pandas as pd
from pydantic import BeforeValidator, Field, StrictInt, model_validator

from .dataset import operational_json, _opaque
from .temporal_preparation import (_TemporalContract, TemporalTimestamp,
    TemporalBudgets, TemporalDatasetBinding, TemporalPreparationPlan)

Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


def identity(value):
    return hashlib.sha256(operational_json(value).encode()).hexdigest()


def point(value):
    if type(value) not in (float, np.float64) or not math.isfinite(value):
        raise ValueError('finite float64 prediction required')
    return float(value)


def cell_identity(value):
    if type(value) in (float, np.float64):
        if not math.isfinite(value): raise ValueError('finite representation required')
        return ['float64', struct.pack('>d', value).hex()]
    if type(value) in (int, np.int64, np.int32, np.int16, np.int8): return ['integer', str(value)]
    if type(value) in (bool, np.bool_): return ['boolean', bool(value)]
    if type(value) in (datetime, pd.Timestamp) and value is not pd.NaT:
        return ['timestamp', value.isoformat(), str(value.tzinfo)]
    raise ValueError('unsupported representation cell')


class PredictorCapabilities(_TemporalContract):
    version: Literal['1.0'] = '1.0'
    predictor_id: str
    predictor_version: str
    implementation_kind: Literal['synthetic'] = 'synthetic'
    strategy: Literal['recursive_one_step', 'direct_multi_horizon']
    representation_kind: Literal['tabular_step', 'temporal_sequence', 'canonical_history', 'statistical']
    representation_schema_id: Digest
    numeric_policy_id: Digest
    lookback: StrictInt = Field(ge=1, le=4096)
    supported_horizon: StrictInt = Field(ge=1, le=52)
    allowed_covariates: tuple[str, ...] = Field(default=(), max_length=128)
    max_absolute_prediction: float = Field(default=float.fromhex('0x1.fffffffffffffp+1023'), gt=0, allow_inf_nan=False)
    output_dtype: Literal['float64'] = 'float64'
    output_semantics: Literal['point_value'] = 'point_value'
    deterministic: Literal[True] = True

    @model_validator(mode='before')
    @classmethod
    def strict_determinism(cls, data):
        if isinstance(data, dict) and type(data.get('deterministic', True)) is not bool:
            raise ValueError('explicit deterministic declaration required')
        return data

    @model_validator(mode='after')
    def bounded(self):
        _opaque(self.predictor_id); _opaque(self.predictor_version)
        for field in self.allowed_covariates: _opaque(field)
        if tuple(sorted(set(self.allowed_covariates))) != self.allowed_covariates:
            raise ValueError('ordered unique covariates required')
        return self

    @property
    def digest(self): return identity(self.model_dump(mode='json'))


class RolloutBudgets(TemporalBudgets):
    max_horizon: StrictInt = Field(default=52, ge=1, le=52)
    max_predictor_calls: StrictInt = Field(default=10000, ge=1)
    max_prediction_events: StrictInt = Field(default=10000, ge=1)
    max_prediction_bytes: StrictInt = Field(default=16*1024*1024, ge=1)
    max_representation_bytes: StrictInt = Field(default=32*1024*1024, ge=1)
    max_feedback_bytes: StrictInt = Field(default=4*1024*1024, ge=1)
    max_external_requests: StrictInt = Field(default=10000, ge=1)


class ForecastEvaluationPlan(TemporalPreparationPlan):
    purpose: Literal['offline_forecast_evaluation'] = 'offline_forecast_evaluation'
    capability: Literal['recursive_rollout'] = 'recursive_rollout'
    predictor: PredictorCapabilities
    budgets: RolloutBudgets = Field(default_factory=RolloutBudgets)

    @model_validator(mode='after')
    def rollout_limits(self):
        n=sum(len(f.origins) for f in self.folds)
        if (self.forecast_horizon>min(self.budgets.max_horizon, self.predictor.supported_horizon)
                or n*len(self.datasets)*self.forecast_horizon>self.budgets.max_predictor_calls
                or n*sum(len(d.series_ids) for d in self.datasets)*self.forecast_horizon>self.budgets.max_prediction_events):
            raise ValueError('rollout plan budget exceeded')
        return self


class ForecastScope(_TemporalContract):
    plan_digest: Digest
    dataset_version: Digest
    fold_id: str
    organization_id: UUID
    series_id: UUID
    metric: str
    unit: str
    currency: str | None
    frequency: Literal['daily', 'weekly']
    timezone: str
    dimensions: tuple[tuple[str, str], ...]
    forecast_origin: TemporalTimestamp
    horizon_step: StrictInt = Field(ge=1, le=52)
    target_period: TemporalTimestamp
    target_period_end: TemporalTimestamp

    @model_validator(mode='after')
    def valid_scope(self):
        for s in (self.fold_id,self.metric,self.unit,self.timezone): _opaque(s)
        if self.currency is not None: _opaque(self.currency)
        if len(self.dimensions)>128 or tuple(sorted(self.dimensions))!=self.dimensions or len(dict(self.dimensions))!=len(self.dimensions):
            raise ValueError('unique ordered dimensions required')
        for k,v in self.dimensions: _opaque(k); _opaque(v)
        dates=(self.forecast_origin,self.target_period,self.target_period_end)
        if any(d.tzinfo is None for d in dates) or not dates[0]<=dates[1]<dates[2]:
            raise ValueError('aware ordered forecast periods required')
        return self


class RolloutCompatibilityBinding(_TemporalContract):
    version: Literal['1.0'] = '1.0'
    plan_digest: Digest
    fold_id: str
    organization_id: UUID
    forecast_origin: TemporalTimestamp
    total_horizon: StrictInt = Field(ge=1, le=52)
    strategy: Literal['recursive_one_step'] = 'recursive_one_step'
    predictor_digest: Digest
    representation_schema_id: Digest
    configuration_digest: Digest
    state_id: Digest
    numeric_policy_id: Digest
    prefix_schema_ids: tuple[Digest, ...] = Field(min_length=1, max_length=52)

    @model_validator(mode='after')
    def prefixes(self):
        if len(self.prefix_schema_ids)!=self.total_horizon: raise ValueError('complete prefix schemas required')
        _opaque(self.fold_id)
        if self.forecast_origin.tzinfo is None: raise ValueError('aware origin required')
        return self

    @property
    def compatibility_id(self): return identity(self.model_dump(mode='json'))


class PredictionRequest(_TemporalContract):
    version: Literal['1.0'] = '1.0'
    dataset_binding: TemporalDatasetBinding
    capabilities: PredictorCapabilities
    compatibility: RolloutCompatibilityBinding
    prefix_length: StrictInt = Field(ge=1, le=52)
    prefix_schema_id: Digest
    scopes: tuple[ForecastScope, ...] = Field(min_length=1, max_length=128)
    feature_names: tuple[str, ...] = Field(min_length=1, max_length=128)
    feature_dtypes: tuple[str, ...] = Field(min_length=1, max_length=128)
    rows: tuple[tuple[Any, ...], ...] = Field(min_length=1, max_length=128)

    @model_validator(mode='after')
    def representation(self):
        c=self.compatibility; cap=self.capabilities; b=self.dataset_binding
        if (cap.strategy!='recursive_one_step' or cap.representation_kind!='tabular_step'
                or self.prefix_length>c.total_horizon or self.prefix_schema_id!=c.prefix_schema_ids[self.prefix_length-1]
                or c.predictor_digest!=cap.digest or c.numeric_policy_id!=cap.numeric_policy_id
                or c.representation_schema_id!=cap.representation_schema_id
                or c.organization_id!=b.reference.organization_id):
            raise ValueError('incompatible representation binding')
        if (len(self.rows)!=len(self.scopes) or len(self.scopes)!=len(b.series_ids)
                or len(set(self.feature_names))!=len(self.feature_names) or len(self.feature_dtypes)!=len(self.feature_names)
                or tuple(s.series_id for s in self.scopes)!=b.series_ids):
            raise ValueError('complete ordered representation required')
        for s,row in zip(self.scopes,self.rows):
            if (len(row)!=len(self.feature_names) or s.dataset_version!=b.reference.version_id
                    or s.plan_digest!=c.plan_digest or s.fold_id!=c.fold_id or s.organization_id!=c.organization_id
                    or s.forecast_origin!=c.forecast_origin or s.horizon_step!=self.prefix_length):
                raise ValueError('representation scope mismatch')
            for value in row: cell_identity(value)
        return self

    @property
    def request_id(self):
        # JSON mode handles UUID/time identities; native cells retain float bits.
        data=self.model_dump(mode='json',exclude={'rows'})
        return identity([data,[[cell_identity(v) for v in row] for row in self.rows]])

    @property
    def matrix(self):
        index=pd.MultiIndex.from_tuples([(s.organization_id,s.series_id,pd.Timestamp(s.target_period)) for s in self.scopes],
            names=('organization_id','series_id','period_start'))
        return pd.DataFrame(self.rows,index=index,columns=self.feature_names).astype(dict(zip(self.feature_names,self.feature_dtypes)))


class PredictionPoint(_TemporalContract):
    scope: ForecastScope
    value: Annotated[float, BeforeValidator(point)]


class PredictionBatch(_TemporalContract):
    version: Literal['1.0'] = '1.0'
    request_id: Digest
    points: tuple[PredictionPoint, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode='after')
    def unique_points(self):
        if len({identity(p.scope.model_dump(mode='json')) for p in self.points})!=len(self.points):
            raise ValueError('duplicate prediction keys')
        return self


class PredictionEvent(_TemporalContract):
    version: Literal['1.0'] = '1.0'
    strategy: Literal['recursive_one_step','direct_multi_horizon'] = 'recursive_one_step'
    dataset_binding: TemporalDatasetBinding
    scope: ForecastScope
    request_id: Digest
    predictor_id: str
    predictor_version: str
    predictor_digest: Digest
    representation_schema_id: Digest
    configuration_digest: Digest
    state_id: Digest
    prefix_schema_id: Digest
    rollout_compatibility_id: Digest
    numeric_policy_id: Digest
    parent_feedback_ids: tuple[Digest, ...] = Field(max_length=51)
    value: Annotated[float, BeforeValidator(point)]

    @model_validator(mode='after')
    def lineage(self):
        b=self.dataset_binding; s=self.scope
        _opaque(self.predictor_id);_opaque(self.predictor_version)
        if (s.organization_id!=b.reference.organization_id or s.dataset_version!=b.reference.version_id
                or s.series_id not in b.series_ids or len(self.parent_feedback_ids)!=(s.horizon_step-1 if self.strategy=='recursive_one_step' else 0)):
            raise ValueError('prediction lineage mismatch')
        return self

    @property
    def event_id(self):
        return identity([self.model_dump(mode='json'),struct.pack('>d',self.value).hex()])


class DeterministicRolloutResult(_TemporalContract):
    version: Literal['1.0'] = '1.0'
    plan: ForecastEvaluationPlan
    events: tuple[PredictionEvent, ...]
    compatibilities: tuple[RolloutCompatibilityBinding, ...]

    @model_validator(mode='after')
    def complete(self):
        p=self.plan
        if p.predictor.strategy!='recursive_one_step': raise ValueError('unsupported rollout strategy')
        expected=[(f.fold_id,b.reference.organization_id,s,o,k) for f in p.folds for b in p.datasets
            for o in f.origins for k in range(1,p.forecast_horizon+1) for s in b.series_ids]
        actual=[(e.scope.fold_id,e.scope.organization_id,e.scope.series_id,e.scope.forecast_origin,e.scope.horizon_step) for e in self.events]
        if actual!=expected: raise ValueError('incomplete ordered rollout')
        bindings={b.reference.organization_id:b for b in p.datasets}
        keys=[(c.fold_id,c.organization_id,c.forecast_origin) for c in self.compatibilities]
        wanted=[(f.fold_id,b.reference.organization_id,o) for f in p.folds for b in p.datasets for o in f.origins]
        if keys!=wanted: raise ValueError('incomplete compatibility coverage')
        compat=dict(zip(keys,self.compatibilities));parents={}
        for e in self.events:
            s=e.scope;c=compat[s.fold_id,s.organization_id,s.forecast_origin]
            if (e.dataset_binding!=bindings[s.organization_id] or s.plan_digest!=p.digest
                    or e.strategy!='recursive_one_step' or e.predictor_digest!=p.predictor.digest
                    or e.predictor_id!=p.predictor.predictor_id or e.predictor_version!=p.predictor.predictor_version
                    or e.rollout_compatibility_id!=c.compatibility_id or c.plan_digest!=p.digest
                    or c.total_horizon!=p.forecast_horizon or c.configuration_digest!=p.configuration_digest
                    or e.configuration_digest!=c.configuration_digest or e.state_id!=c.state_id
                    or e.numeric_policy_id!=c.numeric_policy_id or c.numeric_policy_id!=p.predictor.numeric_policy_id
                    or c.representation_schema_id!=p.predictor.representation_schema_id
                    or e.representation_schema_id!=c.representation_schema_id or c.predictor_digest!=p.predictor.digest
                    or e.prefix_schema_id!=c.prefix_schema_ids[s.horizon_step-1]):
                raise ValueError('event binding mismatch')
            key=(s.fold_id,s.organization_id,s.series_id,s.forecast_origin)
            if e.parent_feedback_ids!=tuple(parents.get(key,())): raise ValueError('feedback scope mismatch')
            parents.setdefault(key,[]).append(e.event_id)
        return self

    @property
    def rollout_id(self): return identity([self.plan.digest,[e.event_id for e in self.events]])
