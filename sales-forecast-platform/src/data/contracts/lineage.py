from __future__ import annotations

from datetime import datetime
from uuid import UUID
from typing import Literal, Annotated, Any
from types import MappingProxyType
from collections.abc import Mapping

from pydantic import AfterValidator, ConfigDict, Field, PlainSerializer, field_validator, model_validator

from .common import ContractModel


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError("configuration parameters must be JSON-compatible")


def _thaw(value):
    if isinstance(value, MappingProxyType):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


FrozenStrings = Annotated[dict[str, str], AfterValidator(_freeze), PlainSerializer(_thaw)]
FrozenParameters = Annotated[dict[str, Any], AfterValidator(_freeze), PlainSerializer(_thaw)]


def _nonblank(value):
    if value is not None and not value.strip():
        raise ValueError("declarations must not be whitespace-only")
    return value


class MetadataResolution(ContractModel):
    """Resolution provenance, not an authority lookup or a value transformation."""

    model_config = ConfigDict(frozen=True, revalidate_instances="always")

    field: Literal["unit", "currency"]
    metric: str = Field(min_length=1)
    original_value_reference: str = Field(min_length=1)
    status: Literal["suggested", "authorized"]
    proposed_value: str = Field(min_length=1)
    evidence_kind: Literal["statistical", "source_declaration", "business_authorization"]
    evidence_reference: str = Field(min_length=1)
    authority_reference: str | None = Field(default=None, min_length=1)
    organization_id: UUID
    business_scope: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)
    rule_version: str
    resolved_at: datetime

    @field_validator("metric", "original_value_reference", "proposed_value",
                     "evidence_reference", "authority_reference", "business_scope", "rule_id")
    @classmethod
    def nonblank(cls, value):
        return _nonblank(value)

    @model_validator(mode="after")
    def valid_evidence(self):
        from .versioning import ContractVersion
        ContractVersion.parse(self.rule_version)
        if self.resolved_at.tzinfo is None or self.resolved_at.utcoffset() is None:
            raise ValueError("resolution timestamp must be timezone-aware")
        if self.status == "authorized" and (self.evidence_kind == "statistical" or not self.authority_reference):
            raise ValueError("authorization requires non-statistical evidence and authority")
        if self.field == "currency" and len(self.proposed_value) != 3:
            raise ValueError("currency resolution must satisfy existing three-character semantics")
        return self


class LineageRecord(ContractModel):
    """Traceability from canonical data back to its source."""

    lineage_id: UUID
    source_id: UUID
    source_dataset: str = Field(min_length=1, max_length=512)
    source_record_id: str = Field(min_length=1, max_length=512)
    ingestion_batch_id: UUID
    adapter_name: str = Field(min_length=1, max_length=128)
    adapter_version: str = Field(min_length=1, max_length=64)
    mapping_version: str = Field(min_length=1, max_length=64)
    ingested_at: datetime
    transformed_at: datetime | None = None

    metadata_resolutions: tuple[MetadataResolution, ...] = ()
