"""Immutable temporal declarations, never admission or authorization evidence."""
from datetime import datetime, timezone
import hashlib
import re
from typing import Annotated, Literal
from uuid import UUID

import pandas as pd
from pydantic import BeforeValidator, ConfigDict, Field, StrictInt, field_validator, model_validator

from .common import ContractModel
from .dataset import StoredDatasetReference, operational_json, _opaque
from .versioning import ContractVersion


class _TemporalContract(ContractModel):
    model_config = ConfigDict(frozen=True, revalidate_instances="always")


def _instants(dates):
    # datetime compares wall times when tzinfo objects match, including ambiguous
    # DST folds. Compare UTC instants while preserving every declared timestamp.
    return tuple(d.astimezone(timezone.utc) for d in dates)


def _exact_timestamp(value):
    # Plans use datetime's microsecond resolution. Do not let Pydantic silently
    # truncate a nanosecond origin into an apparently aligned calendar boundary.
    if type(value) is pd.Timestamp:
        if value is pd.NaT or value.nanosecond:
            raise ValueError("unsupported temporal timestamp precision")
    elif type(value) is str:
        if len(value)>128:
            raise ValueError("bounded ISO timestamp required")
        fraction=re.search(r"\d{2}:\d{2}:\d{2}[.,](\d+)",value)
        if fraction and len(fraction[1])>6:
            raise ValueError("unsupported temporal timestamp precision")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})",value):
            raise ValueError("explicit timezone-aware ISO timestamp required")
    elif type(value) is not datetime:
        raise ValueError("explicit ISO or datetime timestamp required")
    return value


TemporalTimestamp = Annotated[datetime, BeforeValidator(_exact_timestamp)]


class TemporalBudgets(_TemporalContract):
    max_organizations: StrictInt = Field(default=8, ge=1, le=8)
    max_dataset_versions: StrictInt = Field(default=8, ge=1, le=8)
    max_series: StrictInt = Field(default=128, ge=1)
    max_folds: StrictInt = Field(default=16, ge=1, le=32)
    max_origins: StrictInt = Field(default=256, ge=1)
    max_captured_rows: StrictInt = Field(default=10000, ge=1)
    max_decoded_bytes: StrictInt = Field(default=16 * 1024 * 1024, ge=1)
    max_output_cells: StrictInt = Field(default=100000, ge=1)
    max_fitted_state_bytes: StrictInt = Field(default=2 * 1024 * 1024, ge=1)
    max_assembly_bytes: StrictInt = Field(default=256 * 1024 * 1024, ge=1)
    max_work_cells: StrictInt = Field(default=1000000, ge=1)
    max_elapsed_seconds: StrictInt = Field(default=300, ge=1)


class TemporalDatasetBinding(_TemporalContract):
    reference: StoredDatasetReference
    source_id: UUID
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    admission_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    mapping_id: str
    mapping_version: str
    mapping_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    quality_policy_id: str
    quality_policy_version: str
    series_definition_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    series_ids: tuple[UUID, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def explicit_binding(self):
        _opaque(self.mapping_id); _opaque(self.quality_policy_id)
        ContractVersion.parse(self.mapping_version); ContractVersion.parse(self.quality_policy_version)
        if self.manifest_digest != self.reference.version_id:
            raise ValueError("manifest/version binding mismatch")
        if tuple(sorted(set(self.series_ids), key=str)) != self.series_ids:
            raise ValueError("series scope must be unique and ordered")
        return self


class TemporalFold(_TemporalContract):
    fold_id: str
    training_origins: tuple[TemporalTimestamp, ...] = Field(min_length=1, max_length=1024)
    validation_origins: tuple[TemporalTimestamp, ...] = Field(default=(), max_length=1024)
    test_origins: tuple[TemporalTimestamp, ...] = Field(default=(), max_length=1024)
    calibration_start: TemporalTimestamp
    categorical_cutoff: TemporalTimestamp
    model_fit_cutoff: TemporalTimestamp
    assessment_cutoff: TemporalTimestamp
    history_strategy: Literal["expanding"] = "expanding"
    purge_policy: Literal["reject_overlap"] = "reject_overlap"
    embargo_periods: StrictInt = Field(default=0, ge=0, le=0)

    @model_validator(mode="after")
    def chronological(self):
        _opaque(self.fold_id)
        dates = (self.calibration_start, self.categorical_cutoff,
                 self.model_fit_cutoff, self.assessment_cutoff) + self.origins
        if any(d.tzinfo is None or d.utcoffset() is None for d in dates):
            raise ValueError("aware temporal boundaries required")
        for group in (self.training_origins, self.validation_origins, self.test_origins):
            instants = _instants(group)
            if tuple(sorted(set(instants))) != instants:
                raise ValueError("origins must be unique and ordered")
        origins = _instants(self.origins)
        if tuple(sorted(set(origins))) != origins:
            raise ValueError("partition origins overlap or are unordered")
        calibration_start, categorical_cutoff, model_fit_cutoff, assessment_cutoff = _instants(dates[:4])
        if not calibration_start < categorical_cutoff <= origins[0]:
            raise ValueError("calibration must be a prefix before prepared origins")
        if not _instants(self.training_origins)[-1] < model_fit_cutoff <= assessment_cutoff:
            raise ValueError("invalid label assessment boundaries")
        heldout = _instants(self.validation_origins + self.test_origins)
        if heldout and model_fit_cutoff > heldout[0]:
            raise ValueError("model fitting extends into held-out origins")
        if heldout and assessment_cutoff <= heldout[-1]:
            raise ValueError("assessment precedes held-out labels")
        return self

    @property
    def origins(self):
        return self.training_origins + self.validation_origins + self.test_origins


class TemporalPreparationPlan(_TemporalContract):
    plan_id: str
    version: Literal["1.0"] = "1.0"
    datasets: tuple[TemporalDatasetBinding, ...] = Field(min_length=1, max_length=8)
    configuration_id: str
    configuration_version: str
    configuration_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    frequency: Literal["daily", "weekly"]
    calendar: Literal["gregorian"]
    timezone: str
    period_anchor: Literal["start", "end"]
    purpose: Literal["production_training"]
    folds: tuple[TemporalFold, ...] = Field(min_length=1, max_length=32)
    forecast_horizon: StrictInt = Field(default=1, ge=1, le=52)
    capability: Literal["supervised_h1", "planning_only"] = "supervised_h1"
    budgets: TemporalBudgets = Field(default_factory=TemporalBudgets)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try: ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("unsupported temporal timezone") from None
        return value

    @model_validator(mode="after")
    def bounded_plan(self):
        _opaque(self.plan_id); _opaque(self.configuration_id)
        ContractVersion.parse(self.configuration_version)
        if self.capability == "supervised_h1" and self.forecast_horizon != 1:
            raise ValueError("multi-horizon execution is unsupported")
        organizations = tuple(d.reference.organization_id for d in self.datasets)
        if tuple(sorted(set(organizations), key=str)) != organizations:
            raise ValueError("one dataset per organization, deterministically ordered, required")
        if len({f.fold_id for f in self.folds}) != len(self.folds):
            raise ValueError("duplicate fold identity")
        fitting = _instants(tuple(f.model_fit_cutoff for f in self.folds))
        if any(a >= b for a, b in zip(fitting, fitting[1:])):
            raise ValueError("fold fitting cutoffs must advance")
        heldout = _instants(tuple(o for f in self.folds for o in f.validation_origins + f.test_origins))
        if tuple(sorted(set(heldout))) != heldout:
            raise ValueError("held-out fold origins overlap or are unordered")
        b = self.budgets
        if (len(self.datasets) > min(b.max_organizations, b.max_dataset_versions)
                or sum(len(d.series_ids) for d in self.datasets) > b.max_series
                or len(self.folds) > b.max_folds
                or sum(len(f.origins) for f in self.folds) * len(self.datasets) > b.max_origins):
            raise ValueError("temporal plan budget exceeded")
        if len(self.model_dump_json().encode()) > 128 * 1024:
            raise ValueError("temporal plan declaration too large")
        return self

    @property
    def digest(self):
        return hashlib.sha256(operational_json(self.model_dump(mode="json")).encode()).hexdigest()
