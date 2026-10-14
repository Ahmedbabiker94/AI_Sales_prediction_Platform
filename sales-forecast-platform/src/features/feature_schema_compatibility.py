"""Strict opt-in compatibility evidence; never authenticates model provenance."""
import hashlib
from uuid import UUID

import pandas as pd
from pydantic import ConfigDict, Field, StrictInt, model_validator

from src.data.contracts.common import ContractModel
from src.data.contracts.features import FeatureConfiguration, FeatureState
from src.data.contracts.forecast import ForecastConfig
from src.data.contracts.dataset import operational_json
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline, configuration_digest


class FeatureSchemaMismatch(ValueError):
    pass


class FeatureSchemaCompatibility(ContractModel):
    """Full configuration binds calculations, calendar, units and availability.

    A real artifact must contain independently trusted matching metadata. Creating
    this declaration or matching the legacy fixture does not certify an artifact.
    """
    model_config = ConfigDict(frozen=True, revalidate_instances="always")

    version: str
    executor_version: str
    configuration: FeatureConfiguration
    configuration_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    fitted_state_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    training_schema_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Fixed number of consecutive target periods starting at the request origin.
    # Frequency/calendar/recursive strategy remain bound by configuration.
    forecast_horizon: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def executable_binding(self):
        if self.version != "1.1":
            raise ValueError("unsupported compatibility version")
        pipeline = CanonicalFeaturePipeline(self.configuration)
        if self.executor_version != pipeline.executor_version:
            raise ValueError("unsupported executor binding")
        if self.forecast_horizon > pipeline.budgets.max_horizon:
            raise ValueError("unsupported compatibility horizon")
        if configuration_digest(self.configuration) != self.configuration_digest:
            raise ValueError("configuration digest mismatch")
        return self

    @property
    def schema_id(self):
        return hashlib.sha256(operational_json(self.model_dump(mode="json")).encode()).hexdigest()


def _forecast_request(pipeline, state, forecast_config, forecast_origin):
    """Reuse executor calendar arithmetic; reject ambiguous runtime declarations."""
    if type(forecast_config) is not ForecastConfig or type(forecast_config.forecast_horizon) is not int:
        raise ValueError()
    forecast = ForecastConfig.model_validate(forecast_config.model_dump())
    if (forecast.forecast_frequency.value != pipeline.temporal.frequency.value
            or forecast.forecast_horizon > pipeline.budgets.max_horizon):
        raise ValueError()
    origin = pipeline._time(forecast_origin)
    pipeline._aligned(origin)
    if origin < pipeline._time(state.training_cutoff):
        raise ValueError()
    return forecast, origin


def describe_feature_schema(configuration, state, *, forecast_config, forecast_origin):
    try:
        pipeline = CanonicalFeaturePipeline(configuration)
        state = pipeline.validate_fitted_state(state)
        forecast, _ = _forecast_request(pipeline, state, forecast_config, forecast_origin)
        return FeatureSchemaCompatibility(version="1.1", executor_version=pipeline.executor_version,
            configuration=pipeline.configuration, configuration_digest=pipeline.digest,
            fitted_state_id=state.state_id, training_schema_digest=state.training_schema_digest,
            forecast_horizon=forecast.forecast_horizon)
    except Exception:
        raise FeatureSchemaMismatch("feature schema or fitted-state binding invalid") from None


def verify_feature_schema(expected, configuration, state, matrix, *, requests, forecast_config, forecast_origin):
    """No fallback for absent/legacy signatures or reordered/coerced matrices."""
    try:
        expected = FeatureSchemaCompatibility.model_validate(expected.model_dump())
        actual = describe_feature_schema(configuration, state, forecast_config=forecast_config, forecast_origin=forecast_origin)
        if expected != actual or type(matrix) is not pd.DataFrame:
            raise ValueError()
        if tuple(matrix.columns) != expected.configuration.ordered_outputs:
            raise ValueError()
        if tuple(str(dtype) for dtype in matrix.dtypes) != tuple(expected.configuration.output_dtypes[n] for n in matrix.columns):
            raise ValueError()
        if (type(matrix.index) is not pd.MultiIndex or tuple(matrix.index.names) != ("organization_id","series_id","period_start")
                or not matrix.index.is_unique or matrix.empty):
            raise ValueError()
        pipeline = CanonicalFeaturePipeline(configuration)
        state = pipeline.validate_fitted_state(state)
        forecast, origin = _forecast_request(pipeline, state, forecast_config, forecast_origin)
        pipeline._frame_budget(matrix)
        if matrix.memory_usage(deep=True).sum() > pipeline.budgets.max_result_bytes:
            raise ValueError()
        contexts = pipeline._rows(requests, observations=False)
        pipeline._scope(contexts)
        if not contexts or any(row["organization_id"] not in state.organization_ids for row in contexts):
            raise ValueError()
        count = len(contexts) * forecast.forecast_horizon
        if (count != len(matrix) or count > pipeline.budgets.max_rows
                or count * len(configuration.ordered_outputs) * 8 > pipeline.budgets.max_result_bytes):
            raise ValueError()
        periods = tuple(pipeline._advance(origin, step) for step in range(forecast.forecast_horizon))
        ordered = sorted(contexts, key=lambda row: (str(row["organization_id"]), str(row["series_id"])))
        identities = [(row["organization_id"], row["series_id"], start) for row in ordered for start in periods]
        expected_index = pd.MultiIndex.from_tuples(identities, names=matrix.index.names)
        if not matrix.index.equals(expected_index):
            raise ValueError()
        for position, (org, series, start) in enumerate(matrix.index):
            if type(org) is not UUID or type(series) is not UUID:
                raise ValueError()
            stamp = pipeline._time(start)
            if str(start.tzinfo) != pipeline.temporal.timezone:
                raise ValueError()
            if "period_start" in matrix and matrix.loc[(org,series,start),"period_start"] != stamp:
                raise ValueError()
            if "period_end" in matrix and matrix.loc[(org,series,start),"period_end"] != pipeline._advance(stamp,1):
                raise ValueError()
            context = ordered[position // forecast.forecast_horizon]
            for field in pipeline.rules.dimension_fields:
                if field in matrix:
                    value = context[field]
                    if field in configuration.category_encoding:
                        value = pipeline._category(field, value, state)
                    if matrix.iloc[position][field] != value:
                        raise ValueError()
    except Exception:
        raise FeatureSchemaMismatch("feature schema compatibility failed") from None
    return expected.schema_id
