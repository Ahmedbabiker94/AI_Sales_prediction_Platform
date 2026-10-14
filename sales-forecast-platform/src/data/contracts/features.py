"""Feature declarations and immutable fitted state; no admission or registry."""
from datetime import datetime
from uuid import UUID
from typing import Literal

from pydantic import ConfigDict, Field, StrictInt, StrictBool, field_validator, model_validator

from .common import ContractModel, Frequency, Measurement
from .mapping import _DeclaredMeasurement
from .versioning import ContractVersion
from .lineage import FrozenParameters, FrozenStrings, _nonblank


class TemporalFeatureRules(ContractModel):
    model_config = ConfigDict(frozen=True, revalidate_instances="always")

    frequency: Frequency
    calendar: Literal["gregorian"]
    timezone: str = Field(min_length=1)
    period_anchor: Literal["start", "end"]
    week_start: StrictInt | None = None
    lag_basis: Literal["period", "observation"]
    gap_policy: Literal["reject", "allow_observation_gaps"]
    minimum_history: StrictInt = Field(ge=0)
    cold_start: Literal["reject", "configured_missing"]
    recursive_strategy: Literal["provided_predictions"]

    @model_validator(mode="after")
    def supported_calendar(self):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("unsupported feature timezone") from None
        if self.frequency not in (Frequency.DAILY, Frequency.WEEKLY):
            raise ValueError("executor supports Gregorian daily/weekly periods only")
        if self.frequency is Frequency.WEEKLY:
            if self.week_start is None or not 0 <= self.week_start <= 6:
                raise ValueError("weekly calendar requires an explicit weekday anchor")
        elif self.week_start is not None:
            raise ValueError("daily calendar has no weekday anchor")
        if self.lag_basis == "period" and self.gap_policy != "reject":
            raise ValueError("period lags require gap rejection")
        return self


class CategoryFeatureRule(ContractModel):
    model_config = ConfigDict(frozen=True, revalidate_instances="always")

    field: str = Field(min_length=1)
    unknown: Literal["reject", "use_code"]
    missing: Literal["reject", "use_code"]
    unknown_code: StrictInt | None = None
    missing_code: StrictInt | None = None

    @model_validator(mode="after")
    def explicit_reserved_codes(self):
        _nonblank(self.field)
        for behavior, code in ((self.unknown, self.unknown_code), (self.missing, self.missing_code)):
            if (behavior == "use_code" and (code is None or code >= 0)) or (behavior == "reject" and code is not None):
                raise ValueError("category fallback requires an explicit negative reserved code")
        if self.unknown_code is not None and self.unknown_code == self.missing_code:
            raise ValueError("unknown and missing category codes must be distinct")
        return self


class ExogenousFeatureRule(ContractModel):
    model_config = ConfigDict(frozen=True, revalidate_instances="always")

    field: str = Field(min_length=1)
    availability: Literal["known_in_advance", "observed_only", "lagged", "external_forecast"]
    offset: StrictInt | None = None
    missing: Literal["reject", "zero"]
    substitute_reference: str | None = None

    @model_validator(mode="after")
    def explicit_availability(self):
        _nonblank(self.field)
        if self.availability == "lagged":
            if self.offset is None or self.offset <= 0:
                raise ValueError("lagged exogenous input requires a positive offset")
        elif self.offset is not None:
            raise ValueError("only lagged inputs declare offsets")
        if self.missing == "zero":
            if not self.substitute_reference or not self.substitute_reference.strip():
                raise ValueError("substitution requires a documented configuration reference")
        elif self.substitute_reference is not None:
            raise ValueError("unexpected substitution reference")
        return self


class FeatureExecutionRules(ContractModel):
    """Opt-in executable semantics; legacy declarations remain non-executable."""
    model_config = ConfigDict(frozen=True, revalidate_instances="always")

    version: Literal["1.0"] = "1.0"
    measurement: _DeclaredMeasurement
    currency_required: StrictBool
    metric_meaning: str = Field(min_length=1)
    sign_semantics: str = Field(min_length=1)
    dimension_fields: tuple[str, ...] = Field(min_length=1)
    temporal: TemporalFeatureRules
    historical_value_availability: Literal["available_at"]
    categories: tuple[CategoryFeatureRule, ...] = ()
    exogenous: tuple[ExogenousFeatureRule, ...] = ()
    numeric_conversion: Literal["lossless_float64"]

    @field_validator("measurement", mode="before")
    @classmethod
    def reuse_measurement(cls, value):
        return value.model_dump() if isinstance(value, Measurement) else value

    @model_validator(mode="after")
    def unambiguous_roles(self):
        _nonblank(self.metric_meaning); _nonblank(self.sign_semantics)
        if self.currency_required and self.measurement.currency is None:
            raise ValueError("required currency must be explicitly supplied")
        fields = self.dimension_fields + tuple(r.field for r in self.exogenous)
        reserved = {"organization_id", "series_id", "metric", "unit", "currency", "frequency",
                    "period_start", "period_end", "value", "available_at", "evidence_reference"}
        if len(fields) != len(set(fields)) or any(not f.strip() or f in reserved for f in fields):
            raise ValueError("dimension and exogenous roles must be unique and nonreserved")
        categories = tuple(r.field for r in self.categories)
        if len(categories) != len(set(categories)) or not set(categories) <= set(fields):
            raise ValueError("categories must reference unique configured input roles")
        return self


class FeatureCalculation(ContractModel):
    model_config = ConfigDict(frozen=True, revalidate_instances="always")

    output: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    inputs: tuple[str, ...] = Field(min_length=1)
    parameters: FrozenParameters = Field(default_factory=dict, validate_default=True)


    @field_validator("output", "operation")
    @classmethod
    def nonblank(cls, value):
        return _nonblank(value)

    @field_validator("parameters")
    @classmethod
    def valid_history_parameters(cls, values):
        for key in ("offset", "window"):
            if key in values and (type(values[key]) is not int or values[key] <= 0):
                raise ValueError("history parameters require positive non-boolean integers")
        return values


class FeatureConfiguration(ContractModel):
    model_config = ConfigDict(frozen=True, revalidate_instances="always")

    configuration_id: str = Field(min_length=1)
    version: str
    target_metric: str = Field(min_length=1)
    canonical_inputs: tuple[str, ...] = Field(min_length=1)
    calculations: tuple[FeatureCalculation, ...] = ()
    history_order: Literal["oldest_first", "newest_first"]
    lag_offsets: tuple[StrictInt, ...] = ()
    rolling_windows: tuple[StrictInt, ...] = ()
    missing_value_behavior: FrozenStrings
    category_encoding: FrozenStrings = Field(default_factory=dict, validate_default=True)
    ordered_outputs: tuple[str, ...] = Field(min_length=1)
    output_dtypes: FrozenStrings
    # Optional addition preserves deserialization of R-01 declarations. The
    # new executor requires explicit rules and a separately versioned config.
    execution: FeatureExecutionRules | None = None

    @field_validator("configuration_id", "target_metric")
    @classmethod
    def nonblank(cls, value):
        return _nonblank(value)

    @field_validator("version")
    @classmethod
    def valid_version(cls, value):
        ContractVersion.parse(value)
        return value

    @model_validator(mode="after")
    def consistent_schema(self):
        for fields in (self.canonical_inputs, self.ordered_outputs):
            if len(fields) != len(set(fields)) or any(not f.strip() for f in fields):
                raise ValueError("feature fields must be nonempty and unique")
        for windows in (self.lag_offsets, self.rolling_windows):
            if any(v <= 0 for v in windows) or len(windows) != len(set(windows)):
                raise ValueError("history offsets/windows must be positive and unique")
        available = set(self.canonical_inputs)
        for calculation in self.calculations:
            if not set(calculation.inputs) <= available or calculation.output in available:
                raise ValueError("calculations must reference preceding inputs and unique outputs")
            # Top-level offsets/windows are authoritative; calculations select
            # one declared value rather than defining an independent history.
            if calculation.operation == "lag":
                if set(calculation.parameters) != {"offset"} or calculation.parameters["offset"] not in self.lag_offsets:
                    raise ValueError("lag must select a declared offset")
            if calculation.operation in ("rolling_mean", "rolling_std", "rolling_sum"):
                if "window" not in calculation.parameters or calculation.parameters["window"] not in self.rolling_windows:
                    raise ValueError("rolling calculation must select a declared window")
                if "offset" in calculation.parameters and calculation.parameters["offset"] not in self.lag_offsets:
                    raise ValueError("rolling offset must select a declared offset")
            available.add(calculation.output)
        if not set(self.ordered_outputs) <= available:
            raise ValueError("model outputs must reference declared inputs/calculations")
        if set(self.output_dtypes) != set(self.ordered_outputs) or any(not v.strip() for v in self.output_dtypes.values()):
            raise ValueError("each model output requires exactly one dtype declaration")
        if not set(self.category_encoding) <= set(self.canonical_inputs):
            raise ValueError("category encoding must reference canonical inputs")
        if not set(self.missing_value_behavior) <= available or any(not v.strip() for v in self.missing_value_behavior.values()):
            raise ValueError("missing-value declarations must reference feature fields")
        return self


class FeatureState(ContractModel):
    """Fit provenance and vocabulary, not authenticated training permission."""
    model_config = ConfigDict(frozen=True, revalidate_instances="always")

    executor_version: Literal["1.0.0"]
    configuration_id: str = Field(min_length=1)
    configuration_version: str
    configuration_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    training_schema_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    training_partition_reference: str = Field(min_length=1, max_length=512)
    training_cutoff: datetime
    organization_ids: tuple[UUID, ...] = Field(min_length=1)
    vocabularies: FrozenParameters

    @model_validator(mode="after")
    def valid_fit_state(self):
        from .dataset import _opaque
        _nonblank(self.configuration_id); ContractVersion.parse(self.configuration_version)
        _opaque(self.training_partition_reference)
        if self.training_cutoff.tzinfo is None or self.training_cutoff.utcoffset() is None:
            raise ValueError("fitted state requires an aware training cutoff")
        if len(self.organization_ids) != len(set(self.organization_ids)) or tuple(sorted(self.organization_ids, key=str)) != self.organization_ids:
            raise ValueError("organization scope must be unique and deterministically ordered")
        for field, vocabulary in self.vocabularies.items():
            if not field.strip() or type(vocabulary) is not tuple or any(type(v) is not str or not v.strip() for v in vocabulary):
                raise ValueError("vocabulary requires nonblank string categories")
            if tuple(sorted(set(vocabulary))) != vocabulary:
                raise ValueError("vocabulary must be unique and deterministically ordered")
        return self

    @property
    def state_id(self):
        from .dataset import operational_json
        import hashlib
        return hashlib.sha256(operational_json(self.model_dump(mode="json")).encode()).hexdigest()
