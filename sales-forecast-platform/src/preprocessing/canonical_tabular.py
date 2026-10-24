"""M3 frozen tabular execution over canonical features; no model dependencies.

This is a pure transformation kernel, not publication or model-use authority.
Providers/services must authenticate history and availability before calling it.
"""
import math
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import Field, model_validator

from src.data.contracts.backtesting import identity
from src.data.contracts.features import FeatureState
from src.data.contracts.forecast import ForecastConfig
from src.data.contracts.model_representation import (
    RepresentationContract, OrderedFeatures, TabularRepresentation,
)
from src.data.contracts.temporal_preparation import TemporalPreparationPlan, TemporalDatasetBinding
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
from src.features.feature_schema_compatibility import FeatureSchemaCompatibility, verify_feature_schema
from src.data.contracts.model_evaluation import _reject_declaration


class TabularPreprocessingState(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    policy: Literal['canonical_tabular_frozen_v1'] = 'canonical_tabular_frozen_v1'
    plan: TemporalPreparationPlan
    fold_id: str
    dataset: TemporalDatasetBinding
    canonical_schema: FeatureSchemaCompatibility
    canonical_state: FeatureState
    feature_schema: OrderedFeatures

    @model_validator(mode='after')
    def bound(self):
        schema, state, plan = self.canonical_schema, self.canonical_state, self.plan
        fold = next(f for f in plan.folds if f.fold_id == self.fold_id)
        if self.dataset not in plan.datasets or state.organization_ids != (self.dataset.reference.organization_id,):
            raise ValueError('original publication scope required')
        engine = CanonicalFeaturePipeline(schema.configuration)
        engine.validate_fitted_state(state)
        if (plan.frequency, plan.calendar, plan.timezone, plan.period_anchor) != (
                engine.temporal.frequency.value, engine.temporal.calendar, engine.temporal.timezone, engine.temporal.period_anchor):
            raise ValueError('original calendar required')
        if engine._advance(engine._time(fold.training_origins[-1]), 1) > engine._time(fold.model_fit_cutoff):
            raise ValueError('training targets must be complete by model-fit cutoff')
        # Match the accepted temporal service's original calibration identity.
        partition = identity([plan.digest, fold.fold_id, self.dataset.model_dump(mode='json'),
            engine.digest, engine._time(fold.calibration_start).isoformat(), engine._time(fold.categorical_cutoff).isoformat()])
        if (state.training_cutoff != fold.categorical_cutoff or state.training_partition_reference != partition
                or schema.fitted_state_id != state.state_id or schema.configuration_digest != plan.configuration_digest
                or schema.training_schema_digest != state.training_schema_digest
                or schema.executor_version != state.executor_version
                or schema.forecast_horizon != plan.forecast_horizon
                or schema.configuration.configuration_id != plan.configuration_id
                or schema.configuration.version != plan.configuration_version
                or tuple(f.name for f in self.feature_schema.features) != schema.configuration.ordered_outputs):
            raise ValueError('original training-only state required')
        cfg = schema.configuration
        for feature in self.feature_schema.features:
            source = cfg.output_dtypes[feature.name]
            if (feature.dtype not in (('float32','float64') if source in ('float32','float64') else (source,))
                    or feature.categorical_policy != ('frozen_vocabulary_v1' if feature.name in cfg.category_encoding else 'none')
                    or feature.vocabulary_field != (feature.name if feature.name in cfg.category_encoding else None)
                    or feature.missing != ('mask' if cfg.missing_value_behavior[feature.name] == 'nan' else 'reject')):
                raise ValueError('incompatible tabular schema')
        if plan.forecast_horizon != 1 or plan.capability != 'supervised_h1':
            raise ValueError('canonical H1 preparation required')
        return self

    @property
    def state_id(self):
        return identity(self.model_dump(mode='json', warnings='error'))


def checked_cell(value, feature, missing):
    if missing:
        if value is not None or feature.missing != 'mask':
            raise ValueError('explicit missing cell required')
        return
    dtype = feature.dtype
    if dtype == 'bool':
        if type(value) is not bool: raise ValueError('Boolean identity required')
    elif dtype.startswith('int'):
        if type(value) is not int or not np.iinfo(dtype).min <= value <= np.iinfo(dtype).max:
            raise ValueError('exact integer required')
    else:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('finite numeric scalar required')
        if type(value) is int and int(float(value)) != value:
            raise ValueError('inexact integer widening')
        if dtype == 'float32':
            from src.data.contracts.model_candidate import strict_float32
            strict_float32([value])
        elif abs(value) > np.finfo('float64').max:
            raise ValueError('numeric range exceeded')


def _require_unscaled(representation):
    # M2 can declare scalers; M3 executes none, including identity_v1 references.
    if representation.binding.scaler is not None:
        _reject_declaration()


class TabularOutput(RepresentationContract):
    """Actual detached values, bound to the complete M2 declaration and M3 state."""
    version: Literal['1.0'] = '1.0'
    model_family: Literal['xgboost', 'lightgbm']
    state: TabularPreprocessingState
    representation: TabularRepresentation
    values: tuple[tuple[Any, ...], ...] = Field(min_length=1, max_length=32)

    @model_validator(mode='after')
    def actual(self):
        r, state = self.representation, self.state
        _require_unscaled(r)
        b = r.binding
        if (b.plan != state.plan or b.fold_id != state.fold_id or b.canonical_schema != state.canonical_schema
                or b.canonical_state != state.canonical_state or r.feature_schema != state.feature_schema
                or len(self.values) != r.shape[0]):
            raise ValueError('frozen output binding mismatch')
        if any(p.organization_id != state.dataset.reference.organization_id for grid in r.samples for p in grid):
            raise ValueError('publication boundary mismatch')
        for row, mask in zip(self.values, r.missing_mask):
            if len(row) != r.shape[1]: raise ValueError('feature count mismatch')
            for value, missing, feature in zip(row, mask, r.feature_schema.features):
                checked_cell(value, feature, missing)
        return self

    @property
    def output_id(self):
        return identity(self.model_dump(mode='json', warnings='error'))

    @property
    def matrix(self):
        r = self.representation
        index = pd.MultiIndex.from_tuples([(g[0].organization_id, g[0].series_id, pd.Timestamp(g[0].target_period))
            for g in r.samples], names=('organization_id', 'series_id', 'period_start'))
        frame = pd.DataFrame(self.values, index=index, columns=[f.name for f in r.feature_schema.features])
        return frame.astype({f.name:f.dtype for f in r.feature_schema.features})


def bind_output(state, representation, matrix, model_family):
    """No coercion before checking raw scalars. Returned frames are detached."""
    try:
        state = TabularPreprocessingState.model_validate_json(state.canonical_json)
        r = TabularRepresentation.model_validate_json(representation.canonical_json)
        _require_unscaled(r)
        if type(matrix) is not pd.DataFrame or tuple(matrix.columns) != tuple(f.name for f in r.feature_schema.features):
            raise ValueError()
        expected = tuple((p[0].organization_id, p[0].series_id, pd.Timestamp(p[0].target_period)) for p in r.samples)
        if tuple(matrix.index.tolist()) != expected: raise ValueError()
        # itertuples retains scalar identities (no homogeneous numpy coercion).
        values = tuple(tuple(None if pd.isna(v) else v for v in row) for row in matrix.itertuples(index=False, name=None))
        return TabularOutput(model_family=model_family, state=state, representation=r, values=values)
    except Exception:
        pass
    _reject_declaration()


class FrozenTabularPreprocessor:
    """Load-only transform kernel. There is deliberately no fit method.

    The original provider must authenticate buffers. M2 declarations and hashes
    are not credentials. Unknown/missing category behavior is the explicit
    canonical configuration rule (reject or reserved code), never recoding.
    """
    def __init__(self, state):
        try:
            self.state = TabularPreprocessingState.model_validate_json(state.canonical_json)
            return
        except Exception:
            pass
        _reject_declaration()

    def transform(self, observations, requests, representation, *, model_family='xgboost', exogenous=None):
        try:
            r = TabularRepresentation.model_validate_json(representation.canonical_json)
            _require_unscaled(r)
            state = self.state
            if (r.binding.canonical_state != state.canonical_state or r.binding.canonical_schema != state.canonical_schema
                    or r.binding.plan != state.plan or r.binding.fold_id != state.fold_id): raise ValueError()
            origins = {g[0].forecast_origin for g in r.samples}
            if len(origins) != 1: raise ValueError()
            origin = pd.Timestamp(next(iter(origins)))
            if any(v != origin for v in r.available_at): raise ValueError()
            fields = state.canonical_schema.configuration.execution.dimension_fields
            actual = {(row['organization_id'], row['series_id']): tuple(sorted((f, row[f]) for f in fields))
                      for row in requests.to_dict('records')}
            expected = {(g[0].organization_id, g[0].series_id): g[0].dimensions for g in r.samples}
            if len(requests) != len(expected) or actual != expected: raise ValueError()
            engine = CanonicalFeaturePipeline(state.canonical_schema.configuration)
            forecast = ForecastConfig(forecast_frequency=state.plan.frequency, forecast_horizon=1)
            # Select the eligible prefix BEFORE numerical feature execution.
            history = observations[(observations.period_end <= origin) & (observations.available_at <= origin)].copy()
            matrix = engine.prepare_inference(history, requests.copy(deep=True), state.canonical_state,
                forecast_origin=origin, forecast_config=forecast, exogenous=exogenous)
            verify_feature_schema(state.canonical_schema, engine.configuration, state.canonical_state, matrix,
                requests=requests, forecast_config=forecast, forecast_origin=origin)
            return bind_output(state, r, matrix, model_family)
        except Exception:
            pass
        _reject_declaration()
