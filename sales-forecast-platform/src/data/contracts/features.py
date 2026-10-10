"""Dataset-independent feature declarations; no feature executor or registry."""
from typing import Literal

from pydantic import ConfigDict, Field, StrictInt, field_validator, model_validator

from .common import ContractModel
from .versioning import ContractVersion
from .lineage import FrozenParameters, FrozenStrings, _nonblank


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
