"""Shared, non-mutating bulk canonical mapping; not training admission.

Only explicit_bounds period semantics are executable here. Measurement metadata
must be supplied directly in SourceMapping: no authority resolver exists, so even
self-declared authorized resolution records cannot fill missing measurements.
"""
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from numbers import Integral
from decimal import Decimal
from datetime import date, datetime
from typing import Annotated

from pydantic import TypeAdapter
from pytz.exceptions import AmbiguousTimeError, NonExistentTimeError
import json
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd

from src.data.adapters.base import SourceBatch, validate_record_ids
from src.data.contracts import LineageRecord, SeriesDefinition, SourceMapping, StructuredDataset, Observation
from src.data.processing.tabular_quality import detect_tabular_quality


class CanonicalMappingError(ValueError):
    """Structural mapping failure; never permission to train."""
    def __init__(self, message: str, issues=()):
        self.issues = tuple(issues)
        super().__init__(message)


@dataclass(frozen=True)
class CanonicalBatch:
    """StructuredDataset envelope plus bulk Observation-equivalent records.

    dataset.observations is intentionally empty: records carry canonical
    observation fields plus configured auxiliary fields. One SeriesDefinition is
    created per distinct series, not one Observation object per source row.
    Lineage is batch-level; each bulk row retains its original source_record_id.
    This result has not passed training admission.
    """
    dataset: StructuredDataset
    records: pd.DataFrame


def _identity(organization, dimensions):
    payload = json.dumps([str(organization), sorted(dimensions.items())],
                         ensure_ascii=False, separators=(",", ":"))
    return uuid5(NAMESPACE_URL, payload)


def _dimension(value):
    # SeriesDefinition uses string dimensions. Only unambiguous scalar forms
    # are supported; float IDs, booleans and complex objects fail closed.
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, Integral) and not isinstance(value, (bool, np.bool_)):
        return str(value)
    raise CanonicalMappingError("dimensions require nonblank strings or integral identifiers")


# Exact concrete types only: subclasses may retain mutable state or override
# iteration/conversion methods. Identity checks avoid untrusted metaclass equality.
_AUXILIARY_INTEGER_TYPES = (int, bool, np.bool_, np.int8, np.int16, np.int32, np.int64,
                            np.uint8, np.uint16, np.uint32, np.uint64)
_AUXILIARY_FLOAT_TYPES = (float, np.float16, np.float32, np.float64, np.longdouble)
_AUXILIARY_TEXT_TYPES = (str, np.str_)
_AUXILIARY_TYPES = (_AUXILIARY_INTEGER_TYPES + _AUXILIARY_FLOAT_TYPES
                    + _AUXILIARY_TEXT_TYPES + (Decimal, date, datetime, pd.Timestamp, list, tuple, dict))


def _detach_auxiliary(value, *, required, field, active=None):
    """Copy a small supported value vocabulary; never invoke custom copy hooks."""
    if value is None or value is pd.NA or value is pd.NaT:
        if required:
            raise CanonicalMappingError(f"missing required auxiliary value: {field}")
        return value
    value_type = type(value)
    if not any(value_type is trusted for trusted in _AUXILIARY_TYPES):
        raise CanonicalMappingError(f"unsupported auxiliary value type: {field}")
    active = set() if active is None else active
    if value_type in (list, tuple, dict):
        if id(value) in active:
            raise CanonicalMappingError(f"cyclic auxiliary value: {field}")
        active.add(id(value))
        try:
            if value_type is dict:
                if any(not any(type(key) is trusted for trusted in _AUXILIARY_TEXT_TYPES) for key in value):
                    raise CanonicalMappingError(f"auxiliary dictionary keys require strings: {field}")
                return {key: _detach_auxiliary(item, required=required, field=field, active=active)
                        for key, item in value.items()}
            items = [_detach_auxiliary(item, required=required, field=field, active=active) for item in value]
            return tuple(items) if value_type is tuple else items
        finally:
            active.remove(id(value))
    if value_type in _AUXILIARY_INTEGER_TYPES:
        return value
    if value_type in _AUXILIARY_FLOAT_TYPES or value_type is Decimal:
        finite = value.is_finite() if value_type is Decimal else np.isfinite(value)
        if not finite:
            missing = value.is_nan() if value_type is Decimal else np.isnan(value)
            if required or not missing:
                raise CanonicalMappingError(f"non-finite auxiliary numeric value: {field}")
        return value
    if value_type is pd.Timestamp:
        if value.__dict__:
            raise CanonicalMappingError(f"unsupported auxiliary timestamp state: {field}")
        # Timestamp permits arbitrary instance attributes despite immutable time
        # semantics. Return a fresh scalar so output state cannot reach raw input.
        return value.replace()
    return value  # trusted immutable text/date scalars


def _validate_observation_declarations(mapping):
    for name, value in (("metric", mapping.target_metric), ("unit", mapping.measurement.unit),
                        ("currency", mapping.measurement.currency)):
        field = Observation.model_fields[name]
        validator = TypeAdapter(Annotated[field.annotation, *field.metadata])
        try:
            validator.validate_python(value)
        except ValueError:
            raise CanonicalMappingError(f"canonical field constraint violation: {name}") from None


def _dates(values, timezone, field):
    if values.isna().any() or values.map(lambda v: isinstance(v, (int, float, np.number, bool))).any():
        raise CanonicalMappingError(f"invalid or missing calendar dates: {field}")
    try:
        parsed = pd.to_datetime(values, errors="raise", format="ISO8601")
        if parsed.dt.tz is None:
            parsed = parsed.dt.tz_localize(timezone, ambiguous="raise", nonexistent="raise")
        else:
            parsed = parsed.dt.tz_convert(timezone)
        return parsed
    except (ValueError, TypeError, AttributeError, OverflowError, AmbiguousTimeError, NonExistentTimeError):
        raise CanonicalMappingError(f"invalid calendar dates or timezone: {field}") from None


class CanonicalMapper:
    def __init__(self, mapping: SourceMapping):
        # Revalidate even explicitly unsafe Pydantic copies before execution.
        self.mapping = SourceMapping.model_validate(mapping.model_dump())
        if self.mapping.period_interpretation != "explicit_bounds" or not self.mapping.period_end_field:
            raise CanonicalMappingError("only explicit_bounds with mapped start/end is supported")
        supported = {"hourly", "daily", "weekly", "monthly", "quarterly", "yearly", "irregular"}
        if self.mapping.frequency.value not in supported:
            raise CanonicalMappingError("unsupported canonical frequency")
        # Meanings are explicit execution capabilities, not inferred labels.
        # Do not invent algebra relating any of these distinct metrics.
        if self.mapping.metric_meaning not in {"gross sales", "returns", "damage", "adjustments", "signed reported sales"}:
            raise CanonicalMappingError("unsupported metric interpretation")
        try:
            ZoneInfo(self.mapping.timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise CanonicalMappingError("unsupported timezone") from error
        measurement = self.mapping.measurement
        if measurement is None or not measurement.unit:
            raise CanonicalMappingError("required unit must be supplied directly; resolution execution is deferred")
        if self.mapping.currency_required and not measurement.currency:
            raise CanonicalMappingError("required currency must be supplied directly; resolution execution is deferred")
        _validate_observation_declarations(self.mapping)
        # Prevent auxiliary fields from overwriting canonical Observation fields.
        self._reserved = {"series_id", "period_start", "period_end", "frequency", "metric", "value",
                          "unit", "currency", "value_status", "source_record_id", "lineage_id"}
        role_fields = set(self.mapping.identity_dimensions) | {self.mapping.target_value_field,
                      self.mapping.period_start_field, self.mapping.period_end_field}
        if (set(self.mapping.field_mappings) - role_fields) & self._reserved:
            raise CanonicalMappingError("auxiliary mappings conflict with canonical observation fields")
        if len(set(self.mapping.field_mappings.values())) != len(self.mapping.field_mappings):
            raise CanonicalMappingError("source aliases to multiple canonical roles are unsupported")

    def map_batch(self, batch: SourceBatch) -> CanonicalBatch:
        cfg = self.mapping
        # Validate a detached metadata copy, never write to raw extraction state.
        asset = type(batch.asset).model_validate(batch.asset.model_dump())
        if asset.organization_id != cfg.organization_id:
            raise CanonicalMappingError("source and mapping organization scopes differ")
        if not batch.records.columns.is_unique or batch.records.empty:
            raise CanonicalMappingError("source batch requires nonempty records and unique columns")
        missing = set(cfg.field_mappings.values()) - set(batch.records.columns)
        if missing:
            raise CanonicalMappingError("source fields required by mapping are missing")
        if len(batch.record_ids) != len(batch.records) or len(set(batch.record_ids)) != len(batch.record_ids):
            raise CanonicalMappingError("source record IDs are not aligned or unique")
        try:
            validate_record_ids(batch.record_ids)
        except ValueError:
            raise CanonicalMappingError("source record ID violates canonical constraints") from None
        frame = batch.records[list(cfg.field_mappings.values())].copy(deep=True)
        frame.columns = list(cfg.field_mappings)
        role_fields = set(cfg.identity_dimensions) | {cfg.target_value_field, cfg.period_start_field, cfg.period_end_field}
        for field in frame.columns:
            if field not in role_fields:
                detached = [_detach_auxiliary(value, required=field in cfg.required_auxiliary_fields, field=field)
                            for value in frame[field]]
                if frame[field].dtype == object:
                    frame[field] = pd.Series(detached, index=frame.index, dtype=object)
        start = _dates(frame[cfg.period_start_field], cfg.timezone, cfg.period_start_field)
        end = _dates(frame[cfg.period_end_field], cfg.timezone, cfg.period_end_field)
        if not (end > start).all():
            raise CanonicalMappingError("period_end must be later than period_start")
        # Reuse structural/numeric mechanisms; this does not evaluate/duplicate
        # sales policies and intentionally does not classify negative values.
        frame[cfg.period_start_field] = start
        issues = detect_tabular_quality(
            frame, required_columns=tuple(frame.columns),
            required_numeric_columns=(cfg.target_value_field,) + tuple(
                field for field in cfg.required_auxiliary_fields
                if field != cfg.target_value_field and pd.api.types.is_numeric_dtype(frame[field].dtype)
                and not pd.api.types.is_bool_dtype(frame[field].dtype)),
            nonnull_columns=cfg.identity_dimensions + cfg.required_auxiliary_fields,
            date_column=cfg.period_start_field, business_key=(),
        )
        if issues:
            raise CanonicalMappingError("canonical mapping rejected: " + ", ".join(sorted({i.issue_code for i in issues})), issues)
        dimension_rows = [dict(zip(cfg.identity_dimensions, values)) for values in
                          frame[list(cfg.identity_dimensions)].map(_dimension).itertuples(index=False, name=None)]
        ids = [_identity(cfg.organization_id, dimensions) for dimensions in dimension_rows]
        keys = pd.DataFrame({"series_id": ids, "period_start": start.to_list()})
        if keys.duplicated(keep=False).any():
            raise CanonicalMappingError("duplicate canonical business keys")
        lineage_id = uuid5(asset.ingestion_batch_id, json.dumps([str(asset.source_id), batch.source_dataset,
                              batch.batch_id, cfg.mapping_id, cfg.version], separators=(",", ":")))
        lineage = LineageRecord(lineage_id=lineage_id, source_id=asset.source_id,
            source_dataset=batch.source_dataset, source_record_id=f"batch-{batch.batch_id}",
            ingestion_batch_id=asset.ingestion_batch_id, adapter_name=batch.adapter_name,
            adapter_version=batch.adapter_version, mapping_version=cfg.version, ingested_at=asset.created_at,
            metadata_resolutions=cfg.resolutions)
        role_fields = set(cfg.identity_dimensions) | {cfg.target_value_field, cfg.period_start_field, cfg.period_end_field}
        records = frame[[c for c in frame if c not in role_fields]].copy()
        records["series_id"] = ids
        records["period_start"] = start
        records["period_end"] = end
        records["frequency"] = cfg.frequency.value
        records["metric"] = cfg.target_metric
        records["value"] = frame[cfg.target_value_field]  # no casts, clipping or target splitting
        records["unit"] = cfg.measurement.unit
        records["currency"] = cfg.measurement.currency
        records["value_status"] = "actual"
        records["source_record_id"] = list(batch.record_ids)
        records["lineage_id"] = lineage_id
        unique = dict(zip(ids, dimension_rows))
        series = [SeriesDefinition(series_id=key, series_type=cfg.policy_metric, dimensions=dimensions,
                  attributes={"organization_id": str(cfg.organization_id)}) for key, dimensions in unique.items()]
        asset.metadata = {**asset.metadata, "mapping_id": cfg.mapping_id, "mapping_version": cfg.version,
                          "metric_meaning": cfg.metric_meaning, "sign_semantics": cfg.sign_semantics,
                          "policy_metric": cfg.policy_metric, "policy_context": cfg.policy_context,
                          "business_scope": cfg.business_scope, "bulk_observations": True}
        asset.timezone = cfg.timezone
        dataset = StructuredDataset(asset=asset, name=batch.source_dataset, source_format=batch.source_format,
                  input_frequency=cfg.frequency, canonical_frequency=cfg.frequency,
                  metric_semantics=[cfg.metric_meaning], series=series, lineage=[lineage])
        return CanonicalBatch(dataset=dataset, records=records)

    def map_batches(self, batches: Iterable[SourceBatch]) -> Iterator[CanonicalBatch]:
        """Check stream-wide keys/record IDs; callers must stage before publishing.

        A later rejected batch does not revoke already yielded objects. This is
        not transactional ingestion or admission; consumers must handle aborts.
        """
        seen, source_ids = set(), set()
        for batch in batches:
            result = self.map_batch(batch)
            keys = set(zip(result.records.series_id, result.records.period_start))
            record_keys = {(batch.asset.source_id, batch.asset.ingestion_batch_id, batch.source_dataset, i)
                           for i in batch.record_ids}
            if seen & keys or source_ids & record_keys:
                raise CanonicalMappingError("duplicate keys or source record IDs across batches")
            seen.update(keys)
            source_ids.update(record_keys)
            yield result
