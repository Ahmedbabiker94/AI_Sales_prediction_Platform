"""Bounded causal features only: no admission, fitting models or operational I/O.

The future R-04C caller owns authenticated data/configuration provenance. Frames
use existing bulk Observation fields plus asset organization and configured
dimensions; this module neither creates nor admits a canonical dataset.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
import math
from uuid import UUID

import numpy as np
import pandas as pd

from src.data.contracts.features import FeatureConfiguration, FeatureState
from src.data.contracts.forecast import ForecastConfig
from src.data.contracts.dataset import operational_json, _opaque
from src.data.contracts.versioning import ContractVersion

EXECUTOR_VERSION = "1.0.0"
_STRUCTURAL = ("organization_id", "series_id", "metric", "unit", "currency", "frequency")
_PERIODS = ("period_start", "period_end")
_DTYPES = {"float64", "int64", "int32", "int16", "int8", "bool"}
_NUMERIC = (int, float, np.int8, np.int16, np.int32, np.int64, np.uint8,
            np.uint16, np.uint32, np.uint64, np.float16, np.float32, np.float64)


class FeatureExecutionError(ValueError):
    """Fixed diagnostics: no raw data, evidence references or external errors."""


@dataclass(frozen=True)
class FeatureBudgets:
    max_rows: int = 10000
    max_series: int = 128
    max_columns: int = 128
    max_outputs: int = 128
    max_calculations: int = 256
    max_calculation_cells: int = 1000000
    max_configuration_bytes: int = 64 * 1024
    max_state_bytes: int = 2 * 1024 * 1024
    max_horizon: int = 52
    max_history_periods: int = 4096
    max_categories: int = 1024
    max_cell_bytes: int = 4096
    max_frame_bytes: int = 32 * 1024 * 1024
    max_result_bytes: int = 8 * 1024 * 1024

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in vars(self).values()):
            raise ValueError("feature budgets require positive integers")


@dataclass(frozen=True)
class ExternalFeatureRequest:
    organization_id: UUID
    series_id: UUID
    metric: str
    field: str
    period_start: pd.Timestamp
    period_end: pd.Timestamp
    forecast_origin: pd.Timestamp
    available_at: pd.Timestamp
    value: object
    evidence_reference: str
    configuration_digest: str
    unit: str
    currency: str | None
    frequency: str
    dimensions: tuple[tuple[str, str], ...]


def configuration_digest(configuration):
    return hashlib.sha256(operational_json(configuration.model_dump(mode="json")).encode()).hexdigest()


def _missing(value):
    return value is None or value is pd.NA or value is pd.NaT or (type(value) in (float, np.float64, np.float32) and np.isnan(value))


def _number(value):
    if type(value) is Decimal:
        if not value.is_finite():
            raise FeatureExecutionError("nonfinite numeric input")
        try: result = float(value)
        except (ValueError, OverflowError): raise FeatureExecutionError("unsupported numeric magnitude") from None
        if not math.isfinite(result) or Decimal.from_float(result) != value:
            raise FeatureExecutionError("numeric input is not losslessly representable")
    elif type(value) in _NUMERIC:
        try: result = float(value)
        except (ValueError, OverflowError): raise FeatureExecutionError("unsupported numeric magnitude") from None
        if not math.isfinite(result):
            raise FeatureExecutionError("nonfinite numeric input")
        if type(value) not in (float, np.float16, np.float32, np.float64) and int(result) != int(value):
            raise FeatureExecutionError("numeric input is not losslessly representable")
    else:
        raise FeatureExecutionError("unsupported numeric input")
    return result


class CanonicalFeaturePipeline:
    def __init__(self, configuration, *, budgets=None, authorize_external_forecast=None):
        self.budgets = budgets or FeatureBudgets()
        if type(self.budgets) is not FeatureBudgets:
            raise FeatureExecutionError("invalid feature budgets")
        self.authorize_external_forecast = authorize_external_forecast
        try:
            if type(configuration) is not FeatureConfiguration or type(configuration.calculations) is not tuple or len(configuration.calculations) > self.budgets.max_calculations:
                raise ValueError()
            for name, limit in (("canonical_inputs",self.budgets.max_columns),("ordered_outputs",self.budgets.max_outputs),
                                ("lag_offsets",self.budgets.max_calculations),("rolling_windows",self.budgets.max_calculations)):
                fields = getattr(configuration,name)
                if type(fields) is not tuple or len(fields) > limit: raise ValueError()
            self.configuration = FeatureConfiguration.model_validate(configuration.model_dump())
            self._validate_configuration()
            if len(operational_json(self.configuration.model_dump(mode="json")).encode()) > self.budgets.max_configuration_bytes:
                raise ValueError()
            self.digest = configuration_digest(self.configuration)
        except Exception:
            raise FeatureExecutionError("unsupported executable feature configuration") from None
        self.rules = self.configuration.execution
        self.temporal = self.rules.temporal
        self.step = 1 if self.temporal.frequency.value == "daily" else 7

    def _validate_configuration(self):
        c = self.configuration; b = self.budgets; r = c.execution
        if r is None or ContractVersion.parse(c.version).major != 2 or c.history_order != "oldest_first":
            raise ValueError()
        if r.measurement.metric != c.target_metric or len(c.ordered_outputs) > b.max_outputs or len(c.canonical_inputs) > b.max_columns or len(c.calculations) > b.max_calculations:
            raise ValueError()
        known = {"value", "period_start", "period_end"} | set(r.dimension_fields) | {v.field for v in r.exogenous}
        if set(c.canonical_inputs) != known or "value" in c.ordered_outputs:
            raise ValueError()
        if set(c.category_encoding) != {v.field for v in r.categories} or any(v != "ordinal" for v in c.category_encoding.values()):
            raise ValueError()
        if set(c.ordered_outputs) != set(c.missing_value_behavior) or any(v not in ("reject", "zero", "nan") for v in c.missing_value_behavior.values()):
            raise ValueError()
        for name, dtype in c.output_dtypes.items():
            if dtype not in _DTYPES and not (name in _PERIODS and dtype == f"datetime64[ns, {r.temporal.timezone}]"):
                raise ValueError()
            if c.missing_value_behavior[name] == "nan" and dtype != "float64":
                raise ValueError()
        for exo in r.exogenous:
            if exo.offset is not None and exo.offset not in c.lag_offsets:
                raise ValueError()
        if any(v > b.max_history_periods for v in c.lag_offsets + c.rolling_windows) or r.temporal.minimum_history > b.max_history_periods:
            raise ValueError()
        for calculation in c.calculations:
            op = calculation.operation; p = calculation.parameters; inputs = calculation.inputs
            if op == "lag":
                if inputs != ("value",): raise ValueError()
            elif op in ("rolling_mean", "rolling_std", "rolling_sum"):
                required = {"window", "offset", "min_periods"} | ({"ddof"} if op == "rolling_std" else set())
                if inputs != ("value",) or set(p) != required:
                    raise ValueError()
                if any(type(p[k]) is not int or p[k] <= 0 for k in ("window", "offset", "min_periods")):
                    raise ValueError()
                if p["offset"] not in c.lag_offsets or p["min_periods"] > p["window"] or p["window"] + p["offset"] - 1 > b.max_history_periods:
                    raise ValueError()
                if op == "rolling_std" and (type(p["ddof"]) is not int or p["ddof"] not in (0, 1)):
                    raise ValueError()
            elif op == "calendar":
                if inputs != ("period_" + r.temporal.period_anchor,) or set(p) != {"component"} or type(p["component"]) is not str or p["component"] not in ("year", "month", "day", "weekday", "iso_week", "quarter"):
                    raise ValueError()
            elif op in ("identity", "difference", "sum"):
                if p or "value" in inputs or (op == "identity" and len(inputs) != 1) or (op == "difference" and len(inputs) != 2):
                    raise ValueError()
            else:
                raise ValueError()

    def _text(self, value):
        if type(value) is not str or not value.strip():
            raise FeatureExecutionError("invalid text input")
        try:
            if len(value.encode("utf-8", errors="strict")) > self.budgets.max_cell_bytes:
                raise FeatureExecutionError("feature cell budget exceeded")
        except UnicodeError:
            raise FeatureExecutionError("invalid Unicode input") from None
        return value

    def _frame_budget(self, frame):
        if type(frame) is not pd.DataFrame or not frame.columns.is_unique or any(type(v) is not str for v in frame.columns):
            raise FeatureExecutionError("invalid feature frame")
        if len(frame) > self.budgets.max_rows or len(frame.columns) > self.budgets.max_columns or frame.memory_usage(deep=True).sum() > self.budgets.max_frame_bytes:
            raise FeatureExecutionError("feature input budget exceeded")

    def _time(self, value):
        if type(value) not in (datetime, pd.Timestamp) or _missing(value):
            raise FeatureExecutionError("aware canonical timestamps required")
        try:
            stamp = pd.Timestamp(value)
            if stamp.tzinfo is None: raise ValueError()
            return stamp.tz_convert(self.temporal.timezone)
        except Exception:
            raise FeatureExecutionError("invalid feature timestamp") from None

    def _advance(self, stamp, count):
        try:
            return stamp + pd.DateOffset(days=self.step * count)
        except Exception:
            raise FeatureExecutionError("unsupported calendar transition") from None

    def _aligned(self, start):
        if start != start.normalize() or (self.step == 7 and start.weekday() != self.temporal.week_start):
            raise FeatureExecutionError("period is not calendar aligned")

    def _rows(self, frame, *, observations, predictions=False):
        self._frame_budget(frame)
        required = set(_STRUCTURAL + self.rules.dimension_fields)
        if observations: required |= set(_PERIODS + ("value","available_at"))
        if not required <= set(frame.columns):
            raise FeatureExecutionError("canonical feature inputs missing")
        result = []; seen = set()
        for row in frame.to_dict("records"):
            if type(row["organization_id"]) is not UUID or type(row["series_id"]) is not UUID:
                raise FeatureExecutionError("canonical UUID scope required")
            for field in ("metric","unit","frequency"): self._text(row[field])
            if row["currency"] is not None: self._text(row["currency"])
            if (row["metric"] != self.configuration.target_metric or row["unit"] != self.rules.measurement.unit
                    or row["currency"] != self.rules.measurement.currency or row["frequency"] != self.temporal.frequency.value):
                raise FeatureExecutionError("metric or measurement scope mismatch")
            for field in self.rules.dimension_fields: self._text(row[field])
            key = (row["organization_id"], row["series_id"])
            if observations:
                start = self._time(row["period_start"]); end = self._time(row["period_end"])
                available = self._time(row["available_at"])
                self._aligned(start)
                if end != self._advance(start, 1): raise FeatureExecutionError("invalid canonical period bounds")
                if not predictions and available < end: raise FeatureExecutionError("actual value cannot precede its completed period")
                row = {**row, "period_start":start, "period_end":end, "available_at":available}
                _number(row["value"])
                key += (start,)
            if key in seen: raise FeatureExecutionError("duplicate scoped feature records")
            seen.add(key); result.append(row)
        return result

    def _scope(self, *groups):
        scopes = {}
        for rows in groups:
            for row in rows:
                key = (row["organization_id"], row["series_id"])
                dims = tuple(row[f] for f in self.rules.dimension_fields)
                if key in scopes and scopes[key] != dims:
                    raise FeatureExecutionError("series dimensions changed")
                scopes[key] = dims
        if len(scopes) > self.budgets.max_series: raise FeatureExecutionError("series budget exceeded")

    def _schema(self, frame):
        fields = _STRUCTURAL + ("available_at",) + self.configuration.canonical_inputs
        if not set(fields) <= set(frame): raise FeatureExecutionError("training schema missing")
        # repr retains extension dtype parameters (e.g. string storage backend
        # and category ordering), whereas str(dtype) can collapse them.
        payload = [(field, repr(frame[field].dtype)) for field in fields]
        return hashlib.sha256(operational_json(payload).encode()).hexdigest()

    def fit_state(self, training_observations, *, training_cutoff, partition_reference):
        """Fit vocabulary only from the explicitly supplied training partition."""
        rows = self._rows(training_observations, observations=True); self._scope(rows)
        cutoff = self._time(training_cutoff)
        self._aligned(cutoff)
        if not rows or any(row["period_end"] > cutoff or row["available_at"] > cutoff for row in rows):
            raise FeatureExecutionError("fit partition extends beyond training cutoff")
        vocabulary = {}
        for rule in self.rules.categories:
            values = set()
            for value in training_observations[rule.field]:
                if _missing(value):
                    if rule.missing == "reject": raise FeatureExecutionError("missing training category")
                else:
                    values.add(self._text(value))
                    if len(values) > self.budgets.max_categories: raise FeatureExecutionError("category budget exceeded")
            vocabulary[rule.field] = sorted(values)
        try:
            state = FeatureState(executor_version=EXECUTOR_VERSION, configuration_id=self.configuration.configuration_id,
                configuration_version=self.configuration.version, configuration_digest=self.digest,
                training_schema_digest=self._schema(training_observations), training_partition_reference=partition_reference,
                training_cutoff=cutoff.to_pydatetime(), organization_ids=tuple(sorted({r["organization_id"] for r in rows}, key=str)),
                vocabularies=vocabulary)
            if len(operational_json(state.model_dump(mode="json")).encode()) > self.budgets.max_state_bytes:
                raise FeatureExecutionError("fitted state budget exceeded")
            return self.validate_fitted_state(state)
        except Exception:
            raise FeatureExecutionError("invalid fitted feature state") from None

    def validate_fitted_state(self, state):
        """Shared state/resource binding for execution and compatibility checks."""
        from types import MappingProxyType
        if (type(state) is not FeatureState or type(state.vocabularies) is not MappingProxyType
                or type(state.organization_ids) is not tuple or len(state.organization_ids) > self.budgets.max_series
                or len(state.vocabularies) > self.budgets.max_columns
                or any(type(v) is not tuple or len(v) > self.budgets.max_categories for v in state.vocabularies.values())):
            raise FeatureExecutionError("invalid or oversized fitted state")
        size = 0
        for values in state.vocabularies.values():
            for value in values:
                self._text(value); size += len(value.encode())
                if size > self.budgets.max_state_bytes: raise FeatureExecutionError("fitted state budget exceeded")
        try: state = FeatureState.model_validate(state.model_dump())
        except Exception: raise FeatureExecutionError("invalid fitted feature state") from None
        if len(operational_json(state.model_dump(mode="json")).encode()) > self.budgets.max_state_bytes:
            raise FeatureExecutionError("fitted state budget exceeded")
        if (state.executor_version != EXECUTOR_VERSION or state.configuration_id != self.configuration.configuration_id
                or state.configuration_version != self.configuration.version or state.configuration_digest != self.digest
                or set(state.vocabularies) != {r.field for r in self.rules.categories}):
            raise FeatureExecutionError("fitted state binding mismatch")
        for rule in self.rules.categories:
            dtype = self.configuration.output_dtypes.get(rule.field, "")
            if dtype.startswith("int"):
                limits = np.iinfo(dtype)
                codes = [len(state.vocabularies[rule.field])-1] + [v for v in (rule.unknown_code,rule.missing_code) if v is not None]
                if any(not int(limits.min) <= v <= int(limits.max) for v in codes):
                    raise FeatureExecutionError("category code cannot be represented safely")
        return state

    def _state(self, state, history, requests, origin):
        state = self.validate_fitted_state(state)
        if state.training_schema_digest != self._schema(history) or self._time(state.training_cutoff) > origin:
            raise FeatureExecutionError("fitted state binding mismatch")
        if any(row["organization_id"] not in state.organization_ids for row in requests):
            raise FeatureExecutionError("fitted state organization scope mismatch")
        return state

    def _category(self, field, value, state):
        rule = next(r for r in self.rules.categories if r.field == field)
        if _missing(value):
            if rule.missing == "reject": raise FeatureExecutionError("missing feature category")
            return rule.missing_code
        value = self._text(value); vocabulary = state.vocabularies[field]
        if value not in vocabulary:
            if rule.unknown == "reject": raise FeatureExecutionError("unknown feature category")
            return rule.unknown_code
        return vocabulary.index(value)

    def _exo_index(self, frame):
        if frame is None: return {}
        self._frame_budget(frame)
        fields = {"organization_id", "series_id", "metric", "field", "value", "period_start", "period_end", "available_at"}
        if not fields <= set(frame): raise FeatureExecutionError("exogenous availability metadata missing")
        index = {}
        configured = {r.field for r in self.rules.exogenous}
        scopes = set()
        for row in frame.to_dict("records"):
            self._text(row["metric"]); self._text(row["field"])
            if type(row["organization_id"]) is not UUID or type(row["series_id"]) is not UUID or row["metric"] != self.configuration.target_metric or row["field"] not in configured:
                raise FeatureExecutionError("exogenous scope mismatch")
            scopes.add((row["organization_id"],row["series_id"]))
            if len(scopes) > self.budgets.max_series: raise FeatureExecutionError("exogenous series budget exceeded")
            if not _missing(row["value"]):
                if row["field"] in self.configuration.category_encoding: self._text(row["value"])
                elif type(row["value"]) not in (bool,np.bool_): _number(row["value"])
            start = self._time(row["period_start"]); self._aligned(start)
            if self._time(row["period_end"]) != self._advance(start, 1): raise FeatureExecutionError("exogenous period mismatch")
            row = {**row, "period_start":start, "period_end":self._advance(start, 1), "available_at":self._time(row["available_at"])}
            key = (row["organization_id"], row["series_id"], start, row["field"])
            if key in index: raise FeatureExecutionError("ambiguous exogenous record")
            index[key] = row
        return index

    def _exo(self, context, target, origin, rule, index):
        at = self._advance(target, -rule.offset) if rule.offset is not None else target
        row = index.get((context["organization_id"], context["series_id"], at, rule.field))
        if row is None:
            if rule.missing == "zero": return 0.0
            raise FeatureExecutionError("required exogenous input absent")
        if row["available_at"] > origin or (rule.availability in ("observed_only", "lagged") and row["period_end"] > origin):
            raise FeatureExecutionError("exogenous input unavailable at forecast origin")
        categorical = rule.field in self.configuration.category_encoding
        if _missing(row["value"]):
            if categorical: value = None
            elif rule.missing == "zero": value = 0.0
            else: raise FeatureExecutionError("required exogenous value missing")
        else:
            if categorical: value = self._text(row["value"])
            elif type(row["value"]) in (bool,np.bool_): value = bool(row["value"])
            else: value = _number(row["value"])
        if rule.availability == "external_forecast":
            try:
                reference = row["evidence_reference"]; _opaque(reference)
                request = ExternalFeatureRequest(context["organization_id"], context["series_id"], context["metric"], rule.field,
                    at, row["period_end"], origin, row["available_at"], value, reference, self.digest,
                    context["unit"],context["currency"],context["frequency"],tuple(sorted((f,context[f]) for f in self.rules.dimension_fields)))
                valid = self.authorize_external_forecast is not None and self.authorize_external_forecast(request) is True
            except Exception:
                raise FeatureExecutionError("external feature authorization failed") from None
            if not valid: raise FeatureExecutionError("external feature is not authorized")
        return value

    def _warm(self, name, value):
        if value is None:
            rule = self.configuration.missing_value_behavior[name]
            if rule == "reject": raise FeatureExecutionError("insufficient feature history")
            return 0.0 if rule == "zero" else np.nan
        if isinstance(value, (float, np.floating)) and not math.isfinite(value):
            raise FeatureExecutionError("nonfinite calculated feature")
        return value

    def _calculate(self, base, history, target):
        newest = sorted(history, reverse=True)
        for calculation in self.configuration.calculations:
            p = calculation.parameters; op = calculation.operation
            def lag(offset):
                if self.temporal.lag_basis == "period": return history.get(self._advance(target, -offset))
                return history[newest[offset-1]] if len(newest) >= offset else None
            if op == "lag": value = lag(p["offset"])
            elif op in ("rolling_mean", "rolling_sum", "rolling_std"):
                values = [lag(i) for i in range(p["offset"], p["offset"]+p["window"])]
                values = [v for v in values if v is not None]
                if len(values) < p["min_periods"] or (op == "rolling_std" and len(values) <= p["ddof"]): value = None
                else:
                    array = np.array(values, dtype="float64")
                    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                        value = float(np.std(array, ddof=p["ddof"]) if op == "rolling_std" else np.sum(array) if op == "rolling_sum" else np.mean(array))
            elif op == "calendar":
                stamp = base[calculation.inputs[0]]; component = p["component"]
                value = int(stamp.isocalendar().week if component == "iso_week" else stamp.weekday() if component == "weekday" else getattr(stamp, component))
            else:
                values = [base[field] for field in calculation.inputs]
                if any(v is None for v in values): value = None
                elif op == "identity": value = values[0]
                elif op == "difference": value = _number(values[0]) - _number(values[1])
                else: value = sum(_number(v) for v in values)
            if value is not None and type(value) in (float, np.float64) and not math.isfinite(value):
                raise FeatureExecutionError("nonfinite calculated feature")
            # Missing behavior applies to declared outputs; intermediate missing
            # values propagate rather than silently inventing target observations.
            base[calculation.output] = value
        return {name:self._warm(name, base[name]) for name in self.configuration.ordered_outputs}

    def prepare_training(self, observations, requests, state, **kwargs):
        return self._prepare(observations, requests, state, **kwargs)

    def prepare_inference(self, observations, requests, state, **kwargs):
        return self._prepare(observations, requests, state, **kwargs)

    def _prepare(self, observations, requests, state, *, forecast_origin, forecast_config,
                 exogenous=None, prior_predictions=None):
        origin = self._time(forecast_origin); self._aligned(origin)
        try: forecast = ForecastConfig.model_validate(forecast_config.model_dump())
        except Exception: raise FeatureExecutionError("invalid forecast configuration") from None
        if forecast.forecast_frequency.value != self.temporal.frequency.value or forecast.forecast_horizon > self.budgets.max_horizon:
            raise FeatureExecutionError("unsupported or oversized forecast horizon")
        rows = self._rows(observations, observations=True); contexts = self._rows(requests, observations=False)
        if not contexts: raise FeatureExecutionError("empty feature request")
        if len(contexts)*forecast.forecast_horizon*len(self.configuration.ordered_outputs)*8 > self.budgets.max_result_bytes:
            raise FeatureExecutionError("feature result budget exceeded")
        predictions = [] if prior_predictions is None else self._rows(prior_predictions, observations=True, predictions=True)
        self._scope(rows, contexts, predictions)
        state = self._state(state, observations, contexts, origin)
        # Bound combined window/category work, not just each independent limit.
        # This is an operation-count estimate, not a hard CPU time guarantee.
        per_target = sum(c.parameters["window"] if c.operation.startswith("rolling_") else max(1,len(c.inputs))
                         for c in self.configuration.calculations)
        per_target += len(self.rules.categories)**2 + 2*sum(len(v) for v in state.vocabularies.values())
        if len(contexts)*forecast.forecast_horizon*per_target > self.budgets.max_calculation_cells:
            raise FeatureExecutionError("feature calculation budget exceeded")
        exo = self._exo_index(exogenous)
        wanted = {(r["organization_id"],r["series_id"]) for r in contexts}
        if any((r["organization_id"],r["series_id"]) not in wanted or r["available_at"] > origin or not origin <= r["period_start"] < self._advance(origin,forecast.forecast_horizon-1) for r in predictions):
            raise FeatureExecutionError("recursive prediction scope or horizon mismatch")
        result = []; identities = []
        for context in sorted(contexts, key=lambda r:(str(r["organization_id"]),str(r["series_id"]))):
            key = (context["organization_id"],context["series_id"])
            history = {r["period_start"]:_number(r["value"]) for r in rows
                       if (r["organization_id"],r["series_id"])==key and r["period_end"]<=origin and r["available_at"]<=origin}
            if len(history) > self.budgets.max_history_periods: raise FeatureExecutionError("history budget exceeded")
            if history and self.temporal.gap_policy == "reject":
                first = min(history)
                days = (origin.date()-first.date()).days
                if days//self.step > self.budgets.max_history_periods or days % self.step or len(history) != days//self.step:
                    raise FeatureExecutionError("missing configured history periods")
            recursive = {r["period_start"]:_number(r["value"]) for r in predictions if (r["organization_id"],r["series_id"])==key}
            for horizon in range(forecast.forecast_horizon):
                target = self._advance(origin,horizon)
                if horizon:
                    prior = self._advance(target,-1)
                    if prior not in recursive: raise FeatureExecutionError("prior recursive prediction required")
                    history[prior] = recursive[prior]
                if len(history) < self.temporal.minimum_history and self.temporal.cold_start == "reject":
                    raise FeatureExecutionError("minimum history not satisfied")
                base = {f:context[f] for f in self.rules.dimension_fields}
                base.update(period_start=target,period_end=self._advance(target,1))
                for rule in self.rules.exogenous: base[rule.field] = self._exo(context,target,origin,rule,exo)
                for rule in self.rules.categories: base[rule.field] = self._category(rule.field,base[rule.field],state)
                result.append(self._calculate(base,history,target)); identities.append((*key,target))
        estimate = len(result)*len(self.configuration.ordered_outputs)*8
        if estimate > self.budgets.max_result_bytes: raise FeatureExecutionError("feature result budget exceeded")
        output = pd.DataFrame(result, columns=self.configuration.ordered_outputs,
            index=pd.MultiIndex.from_tuples(identities,names=("organization_id","series_id","period_start")))
        for name,dtype in self.configuration.output_dtypes.items():
            if dtype.startswith("int"):
                limits = np.iinfo(dtype)
                if any(type(v) not in _NUMERIC or not math.isfinite(float(v)) or float(v)!=int(v) or not int(limits.min)<=int(v)<=int(limits.max) for v in output[name]):
                    raise FeatureExecutionError("integer output cannot be represented safely")
            elif dtype == "float64":
                if any(type(v) not in _NUMERIC for v in output[name]):
                    raise FeatureExecutionError("nonnumeric floating output")
            elif dtype == "bool" and not output[name].isin([0,1,False,True]).all():
                raise FeatureExecutionError("nonbinary boolean output")
            try: output[name] = output[name].astype(dtype)
            except Exception: raise FeatureExecutionError("feature dtype conversion failed") from None
        if output.memory_usage(deep=True).sum() > self.budgets.max_result_bytes: raise FeatureExecutionError("feature result budget exceeded")
        return output
