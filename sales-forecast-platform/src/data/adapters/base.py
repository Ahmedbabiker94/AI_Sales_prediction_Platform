"""Source-neutral connector lifecycle and raw extraction batches."""
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit
from typing import Annotated

from pydantic import TypeAdapter

import pandas as pd

from src.data.contracts import AssetMetadata, DataFormat, Observation


_record_field = Observation.model_fields["source_record_id"]
_record_id_validator = TypeAdapter(Annotated[_record_field.annotation, *_record_field.metadata])


def validate_record_ids(record_ids):
    """Reuse canonical field constraints without constructing observations."""
    for value in record_ids:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("source record IDs must be nonblank strings")
        try:
            _record_id_validator.validate_python(value)
        except ValueError:
            raise ValueError("source record ID violates canonical constraints") from None


class SourceType(str, Enum):
    CSV = "csv"
    EXCEL = "excel"
    JSON = "json"
    PARQUET = "parquet"
    POSTGRESQL = "postgresql"
    REST = "rest"
    ODATA = "odata"


@dataclass(frozen=True)
class SourceBatch:
    """Raw records with extraction provenance, independent of connector type.

    IDs are source-local and stable within a supplied source snapshot. Consumers
    must not modify records. Neither mapping nor training admission runs here.
    source_dataset is an opaque, non-secret name, never a credential-bearing URI.
    """
    records: pd.DataFrame
    record_ids: tuple[str, ...]
    asset: AssetMetadata
    source_dataset: str
    source_format: DataFormat
    adapter_name: str
    adapter_version: str
    batch_id: str

    def __post_init__(self):
        object.__setattr__(self, "record_ids", tuple(self.record_ids))
        validate_record_ids(self.record_ids)
        object.__setattr__(self, "asset", AssetMetadata.model_validate(self.asset.model_dump()))
        if not isinstance(self.records, pd.DataFrame):
            raise ValueError("source records must be tabular")
        if len(self.records) != len(self.record_ids) or len(set(self.record_ids)) != len(self.record_ids):
            raise ValueError("source record IDs must be unique and aligned with records")
        for value in (*self.record_ids, self.source_dataset, self.adapter_name, self.adapter_version, self.batch_id):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("source provenance requires nonblank identifiers")
        # Opaque names and IDs are safe provenance. Do not retain network
        # endpoint URLs (which may include credentials/query tokens) in lineage.
        for value in (self.source_dataset, *self.record_ids):
            if urlsplit(value).scheme or "://" in value:
                raise ValueError("source provenance requires opaque non-URL identifiers")
        if self.asset.created_at.tzinfo is None or self.asset.created_at.utcoffset() is None:
            raise ValueError("extraction timestamp must be timezone-aware")


class SourceAdapter(ABC):
    """Implementations own configuration and resources, not canonical semantics.

    Constructors must be inert. Accessibility checks and extraction are explicit
    operations; selection never invokes them. SQL/REST specifics belong to future
    implementations. __exit__ releases resources even when mapping fails.
    """
    @abstractmethod
    def validate_configuration(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def identify_source_type(self) -> SourceType:
        raise NotImplementedError

    @abstractmethod
    def test_access(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def discover_schema(self) -> Mapping[str, str]:
        raise NotImplementedError

    @abstractmethod
    def extract(self, *, batch_size: int = 10000) -> Iterator[SourceBatch]:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
