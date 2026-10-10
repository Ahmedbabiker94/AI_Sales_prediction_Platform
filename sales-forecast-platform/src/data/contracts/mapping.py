"""Declarative source mapping; no adapter, resolution, or admission execution."""
from uuid import UUID

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator

from .common import ContractModel, Frequency, Measurement
from .lineage import FrozenStrings, MetadataResolution, _nonblank
from .versioning import ContractVersion


class _DeclaredMeasurement(Measurement):
    """Immutable declaration reusing Measurement's existing fields/semantics."""
    model_config = ConfigDict(frozen=True, revalidate_instances="always")

    @field_validator("metric", "unit", "currency")
    @classmethod
    def nonblank(cls, value):
        return _nonblank(value)


class SourceMapping(ContractModel):
    # Identity is organization UUID + canonical key/value pairs sorted by key.
    # Future computation must serialize a structured JSON array (not delimiters),
    # preserving exact keys/values. No normalization or hashing is performed here.
    model_config = ConfigDict(frozen=True, revalidate_instances="always")

    mapping_id: str = Field(min_length=1)
    version: str
    organization_id: UUID
    business_scope: str = Field(min_length=1)
    field_mappings: FrozenStrings  # canonical field -> source field
    identity_dimensions: tuple[str, ...] = Field(min_length=1)
    target_metric: str = Field(min_length=1)
    target_value_field: str = Field(min_length=1)
    metric_meaning: str = Field(min_length=1)
    sign_semantics: str = Field(min_length=1)  # declared meaning, never inferred from sign
    policy_metric: str = Field(min_length=1)
    policy_context: str = Field(min_length=1)
    period_start_field: str = Field(min_length=1)
    period_end_field: str | None = None
    period_interpretation: str = Field(min_length=1)
    frequency: Frequency
    timezone: str = Field(min_length=1)
    unit_required: bool
    currency_required: bool
    measurement: _DeclaredMeasurement | None = None
    required_auxiliary_fields: tuple[str, ...] = ()
    resolutions: tuple[MetadataResolution, ...] = ()

    @field_validator("mapping_id", "business_scope", "target_metric", "target_value_field",
                     "metric_meaning", "sign_semantics", "policy_metric", "policy_context",
                     "period_start_field", "period_end_field", "period_interpretation", "timezone")
    @classmethod
    def nonblank(cls, value):
        return _nonblank(value)

    @field_validator("identity_dimensions")
    @classmethod
    def stable_dimension_order(cls, fields):
        return tuple(sorted(fields))

    @field_validator("measurement", mode="before")
    @classmethod
    def copy_measurement(cls, value):
        return value.model_dump() if isinstance(value, Measurement) else value

    @field_validator("version")
    @classmethod
    def valid_version(cls, value):
        ContractVersion.parse(value)
        return value

    @model_validator(mode="after")
    def consistent_declarations(self):
        if not self.field_mappings or any(not k.strip() or not v.strip() for k, v in self.field_mappings.items()):
            raise ValueError("field_mappings requires nonempty canonical and source names")
        for fields in (self.identity_dimensions, self.required_auxiliary_fields):
            if len(fields) != len(set(fields)) or any(not f.strip() for f in fields):
                raise ValueError("configured fields must be nonempty and unique")
        required = self.identity_dimensions + self.required_auxiliary_fields + (self.period_start_field, self.target_value_field)
        if self.period_end_field is not None:
            required += (self.period_end_field,)
        if not set(required) <= self.field_mappings.keys():
            raise ValueError("configured fields must have source mappings")
        roles = [self.target_value_field, self.period_start_field]
        if self.period_end_field is not None:
            roles.append(self.period_end_field)
        if len(set(roles)) != len(roles) or set(roles) & set(self.identity_dimensions):
            raise ValueError("target, period, and identity roles must not overlap")
        role_fields = self.identity_dimensions + tuple(roles)
        sources = [self.field_mappings[field] for field in role_fields]
        if len(sources) != len(set(sources)):
            raise ValueError("distinct identity/target/period roles must use distinct source fields")
        if self.measurement is not None and self.measurement.metric != self.target_metric:
            raise ValueError("measurement metric must match target_metric")
        for resolution in self.resolutions:
            if resolution.organization_id != self.organization_id or resolution.business_scope != self.business_scope:
                raise ValueError("resolution scope must match mapping scope")
        authorized = {}
        for resolution in self.resolutions:
            if resolution.status == "authorized" and resolution.metric == self.target_metric:
                previous = authorized.setdefault(resolution.field, resolution.proposed_value)
                if previous != resolution.proposed_value:
                    raise ValueError("conflicting authorized metadata requires explicit resolution")
                declared = getattr(self.measurement, resolution.field, None)
                if declared and declared != resolution.proposed_value:
                    raise ValueError("authorized metadata conflicts with declared measurement")
        return self

    @property
    def metadata_ready(self) -> bool:
        """Evidence completeness only; not QualityGate admission or authority verification.

        Nonempty measurement metadata is an explicit declaration supplied by the
        caller. Missing required metadata needs an authorized scoped resolution.
        Statistical suggestions never supply a required value.
        """
        try:
            # Revalidate the whole declaration, even if an unsafe construction or
            # nested replacement bypassed normal Pydantic validation.
            checked = type(self).model_validate(self.model_dump())
        except (ValidationError, ValueError, TypeError):
            return False
        for field, required in (("unit", self.unit_required), ("currency", self.currency_required)):
            value = getattr(checked.measurement, field, None)
            if required and not value:
                if not any(r.field == field and r.metric == self.target_metric
                           and r.status == "authorized" for r in checked.resolutions):
                    return False
        return True
