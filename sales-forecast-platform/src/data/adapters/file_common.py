"""Small shared file lifecycle; no business mapping or admission."""
from contextlib import contextmanager
from dataclasses import dataclass
import os
import stat

import pandas as pd

from src.data.adapters.base import SourceAdapter, SourceBatch
from src.data.adapters.selection import SourceConfiguration
from src.data.contracts import AssetMetadata


class FileAdapterError(ValueError):
    """Sanitized configuration/content/resource failure."""


def open_regular_file(path, *, max_bytes=None):
    """Verify the opened descriptor before buffered reads; fail closed off POSIX.

    O_NONBLOCK prevents FIFO opens from waiting for a writer. Descriptor checks,
    rather than a preceding path check, protect against path replacement races.
    Platforms without nonblocking descriptor opening are deliberately unsupported.
    """
    if not hasattr(os, 'O_NONBLOCK'):
        raise FileAdapterError("safe nonblocking file access is unsupported on this platform")
    descriptor = None
    try:
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0)
        descriptor = os.open(path, flags)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (max_bytes is not None and info.st_size > max_bytes):
            raise FileAdapterError("source must be a regular file within the byte limit")
        stream = os.fdopen(descriptor, 'rb')
        descriptor = None  # ownership transferred to the stream
        return stream
    except OSError:
        raise FileAdapterError("source file is inaccessible") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def positive_integer(value, name):
    if type(value) is not int or value <= 0:
        raise FileAdapterError(f"{name} must be a positive integer")


def validate_headers(names, max_columns):
    if not names or len(names) > max_columns:
        raise FileAdapterError("missing header or column limit exceeded")
    if any(type(name) is not str or not name.strip() for name in names):
        raise FileAdapterError("headers must be nonblank strings")
    if len(names) != len(set(names)):
        raise FileAdapterError("duplicate headers are unsupported")
    return tuple(names)


@dataclass(frozen=True)
class FileConfiguration:
    path: str
    source_dataset: str = "file-source"
    max_file_bytes: int = 64 * 1024 * 1024
    max_rows: int = 1_000_000
    max_columns: int = 256
    max_batch_rows: int = 10_000
    max_cell_characters: int = 131_072

    def validate(self):
        SourceConfiguration(self.path)
        if type(self.source_dataset) is not str or not self.source_dataset.strip() or len(self.source_dataset) > 512:
            raise FileAdapterError("source dataset requires a bounded nonblank opaque name")
        SourceConfiguration(self.source_dataset)
        for name in ('max_file_bytes', 'max_rows', 'max_columns', 'max_batch_rows', 'max_cell_characters'):
            positive_integer(getattr(self, name), name)


def records_frame(rows, columns):
    """Retain null integers without pandas' implicit float precision loss."""
    frame = pd.DataFrame(rows, columns=columns, dtype=object)
    for name in columns:
        values = frame[name].tolist()
        present = [v for v in values if v is not None]
        if present and all(type(v) is int for v in present):
            minimum, maximum = min(present), max(present)
            if -(2**63) <= minimum and maximum < 2**63:
                frame[name] = pd.Series(values, dtype='Int64')
            elif 0 <= minimum and maximum < 2**64:
                frame[name] = pd.Series(values, dtype='UInt64')
        elif present and all(type(v) in (int, float) for v in present):
            if all(type(v) is not int or abs(v) <= 2**53 for v in present):
                frame[name] = pd.Series(values, dtype='Float64')
        elif present and all(type(v) is bool for v in present):
            frame[name] = pd.Series(values, dtype='boolean')
    return frame


class FileAdapter(SourceAdapter):
    configuration_type = FileConfiguration
    source_type = None
    source_format = None
    adapter_version = "1.0"

    def __init__(self, configuration, asset: AssetMetadata):
        self.configuration = configuration
        self.asset = AssetMetadata.model_validate(asset.model_dump())
        self.closed = False
        self._resources = []
        self._active = False

    def validate_configuration(self):
        if type(self.configuration) is not self.configuration_type:
            raise FileAdapterError("connector configuration type mismatch")
        self.configuration.validate()
        if self.asset.created_at.tzinfo is None or self.asset.created_at.utcoffset() is None:
            raise FileAdapterError("source snapshot timestamp must be timezone-aware")

    def identify_source_type(self):
        return self.source_type

    def _ensure_open(self):
        if self.closed:
            raise FileAdapterError("connector is closed")

    @contextmanager
    def _owned(self, resource):
        self._resources.append(resource)
        try:
            yield resource
        finally:
            resource.close()
            if resource in self._resources:
                self._resources.remove(resource)

    @contextmanager
    def _binary(self):
        self._ensure_open()
        self.validate_configuration()
        stream = open_regular_file(self.configuration.path, max_bytes=self.configuration.max_file_bytes)
        with self._owned(stream):
            yield stream

    def test_access(self):
        with self._binary():
            pass

    @contextmanager
    def _extraction(self, batch_size):
        self._ensure_open()
        self.validate_configuration()
        positive_integer(batch_size, 'batch_size')
        if batch_size > self.configuration.max_batch_rows:
            raise FileAdapterError("batch_size exceeds configured limit")
        if self._active:
            raise FileAdapterError("concurrent extraction is unsupported")
        self._active = True
        try:
            yield
        finally:
            self._active = False

    def _batch(self, rows, columns, ids, offset, *, metadata=None):
        self._ensure_open()
        asset = AssetMetadata.model_validate(self.asset.model_dump())
        if metadata:
            asset.metadata = {**asset.metadata, **metadata}
        frame = rows if isinstance(rows, pd.DataFrame) else records_frame(rows, columns)
        return SourceBatch(frame, tuple(ids), asset, self.configuration.source_dataset,
                           self.source_format, type(self).__name__, self.adapter_version, str(offset))

    def close(self):
        self.closed = True
        for resource in tuple(self._resources):
            resource.close()
        self._resources.clear()
