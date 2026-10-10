"""Small network connector lifecycle; authorization belongs to the caller."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from uuid import UUID

from src.data.adapters.base import SourceAdapter, SourceBatch
from src.data.adapters.file_common import positive_integer, records_frame, validate_headers
from src.data.adapters.selection import SourceConfiguration
from src.data.contracts import AssetMetadata


class NetworkAdapterError(ValueError):
    """Sanitized extraction/configuration failure, never admission evidence."""


def nonblank(value):
    if type(value) is not str or not value.strip() or len(value) > 512 or any(ord(c) < 32 for c in value):
        raise NetworkAdapterError("configuration requires bounded nonblank identifiers")


@dataclass(frozen=True)
class NetworkConfiguration:
    source_reference: str
    source_dataset: str
    organization_id: UUID
    record_key_fields: tuple[str, ...]
    max_rows: int = 1_000_000
    max_batch_rows: int = 10_000
    max_columns: int = 256

    def __post_init__(self):
        object.__setattr__(self, 'record_key_fields', tuple(self.record_key_fields))

    def validate(self):
        for value in (self.source_reference, self.source_dataset):
            nonblank(value)
            SourceConfiguration(value)
        if type(self.organization_id) is not UUID:
            raise NetworkAdapterError("an explicit organization UUID is required")
        validate_headers(self.record_key_fields, self.max_columns)
        for name in ('max_rows', 'max_batch_rows', 'max_columns'):
            positive_integer(getattr(self, name), name)


def record_reference(values):
    """Collision-safe, typed identity serialization; raw keys never enter lineage.

    Decimal identity follows exact numeric equality, independent of scale/context.
    Type tags intentionally distinguish integers, Decimals and text: cross-type
    SQL equality is not inferred. PostgreSQL columns have consistent result types;
    heterogeneous REST keys retain their declared Python types. Floats/bools are
    unsupported keys. This does not implement database text-collation equality.
    """
    encoded = []
    for value in values:
        kind = type(value)
        if kind in (str, int):
            if kind is str and (not value.strip() or len(value) > 4096):
                raise NetworkAdapterError("invalid source record key")
            encoded.append([kind.__name__, value])
        elif kind is UUID:
            encoded.append(['uuid', str(value)])
        elif kind in (date, datetime):
            encoded.append([kind.__name__, value.isoformat()])
        elif kind is Decimal and value.is_finite():
            sign, digits, exponent = value.as_tuple()
            if not any(digits):
                sign, digits, exponent = 0, (0,), 0
            else:
                end = len(digits)
                while digits[end - 1] == 0:
                    end -= 1
                exponent += len(digits) - end
                digits = digits[:end]
            # Tuple inspection and integer arithmetic cannot round coefficients,
            # even under a low-precision Decimal context with rounding traps.
            encoded.append(['decimal', [sign, digits, exponent]])
        else:
            raise NetworkAdapterError("unsupported or missing source record key")
    try:
        payload = json.dumps(encoded, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    except UnicodeError:
        raise NetworkAdapterError("invalid source record key encoding") from None
    return 'record-' + hashlib.sha256(payload).hexdigest()


class NetworkAdapter(SourceAdapter):
    configuration_type = NetworkConfiguration
    source_type = None
    source_format = None
    adapter_version = '1.0'

    def __init__(self, configuration, asset, *, authorize_scope):
        self.configuration = configuration
        self.asset = AssetMetadata.model_validate(asset.model_dump())
        self.authorize_scope = authorize_scope
        self.closed = False
        self._active = False
        self._resources = []

    def validate_configuration(self):
        if type(self.configuration) is not self.configuration_type:
            raise NetworkAdapterError("connector configuration type mismatch")
        self.configuration.validate()
        if self.configuration.organization_id != self.asset.organization_id:
            raise NetworkAdapterError("source organization does not match asset scope")
        if self.asset.created_at.tzinfo is None or self.asset.created_at.utcoffset() is None:
            raise NetworkAdapterError("source snapshot timestamp must be timezone-aware")
        if not callable(self.authorize_scope):
            raise NetworkAdapterError("explicit scope authorization is required")

    def _authorize(self):
        if self.closed:
            raise NetworkAdapterError("connector is closed")
        self.validate_configuration()
        try:
            allowed = self.authorize_scope(self.configuration, self.asset.model_copy(deep=True))
        except Exception:
            raise NetworkAdapterError("source scope authorization failed") from None
        if allowed is not True:
            raise NetworkAdapterError("source scope is not authorized")

    def identify_source_type(self):
        return self.source_type

    @contextmanager
    def _owned(self, resource):
        self._resources.append(resource)
        failed = False
        try:
            yield resource
        except BaseException:
            failed = True
            raise
        finally:
            cleanup_failed = False
            try:
                resource.close()
            except Exception:
                cleanup_failed = True
            if resource in self._resources:
                self._resources.remove(resource)
            if cleanup_failed and not failed:
                raise NetworkAdapterError("connector resource cleanup failed") from None

    @contextmanager
    def _extraction(self, batch_size):
        self._authorize()
        positive_integer(batch_size, 'batch_size')
        if batch_size > self.configuration.max_batch_rows or self._active:
            raise NetworkAdapterError("batch limit exceeded or extraction already active")
        self._active = True
        try:
            yield
        finally:
            self._active = False

    def _batch(self, rows, names, ids, offset, *, metadata=None):
        if self.closed:
            raise NetworkAdapterError("connector is closed")
        asset = AssetMetadata.model_validate(self.asset.model_dump())
        # Connector-owned metadata only, never endpoint/credential configuration.
        asset.metadata = {**asset.metadata, **(metadata or {})}
        frame = rows if hasattr(rows, 'columns') else records_frame(rows, names)
        return SourceBatch(frame, tuple(ids), asset, self.configuration.source_dataset,
                           self.source_format, type(self).__name__, self.adapter_version, str(offset))

    def close(self):
        self.closed = True
        failed = False
        for resource in tuple(self._resources):
            try:
                resource.close()
            except Exception:
                failed = True
        self._resources.clear()
        if failed:
            raise NetworkAdapterError("connector resource cleanup failed") from None
