"""Strict, explicitly shaped JSON within configured file/record bounds."""
from dataclasses import dataclass
import json
import math

from src.data.adapters.base import SourceType
from src.data.adapters.file_common import FileAdapter, FileAdapterError, FileConfiguration, validate_headers
from src.data.contracts import DataFormat


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('invalid JSON constant')


@dataclass(frozen=True)
class JSONConfiguration(FileConfiguration):
    records_key: str | None = None
    max_depth: int = 32

    def validate(self):
        super().validate()
        if self.records_key is not None and (type(self.records_key) is not str or not self.records_key.strip()):
            raise FileAdapterError("JSON records_key must be a nonblank string")
        if type(self.max_depth) is not int or not 1 <= self.max_depth <= 64:
            raise FileAdapterError("JSON depth limit must be between 1 and 64")


class JSONAdapter(FileAdapter):
    configuration_type = JSONConfiguration
    source_type = SourceType.JSON
    source_format = DataFormat.JSON

    def _validate_value(self, value, depth=0):
        if depth > self.configuration.max_depth:
            raise FileAdapterError("JSON nesting limit exceeded")
        if type(value) is dict:
            for key, item in value.items():
                if len(key) > self.configuration.max_cell_characters:
                    raise FileAdapterError("JSON key limit exceeded")
                self._validate_value(item, depth + 1)
        elif type(value) is list:
            for item in value:
                self._validate_value(item, depth + 1)
        elif type(value) is str and len(value) > self.configuration.max_cell_characters:
            raise FileAdapterError("JSON cell limit exceeded")
        elif type(value) is float:
            if not math.isfinite(value):
                raise FileAdapterError("JSON numeric value is non-finite")

    def _records(self):
        with self._binary() as stream:
            try:
                # One bounded materialization, not unlimited streaming JSON.
                payload = stream.read(self.configuration.max_file_bytes + 1)
                if len(payload) > self.configuration.max_file_bytes:
                    raise FileAdapterError("JSON file byte limit exceeded")
                value = json.loads(payload.decode('utf-8-sig'), object_pairs_hook=_unique_object,
                                   parse_constant=_invalid_constant)
            except (UnicodeError, ValueError, RecursionError, OSError):
                raise FileAdapterError("invalid JSON content or resource limit") from None
        self._validate_value(value)
        if self.configuration.records_key is not None:
            key = self.configuration.records_key
            if type(value) is not dict or set(value) != {key}:
                raise FileAdapterError("JSON envelope must contain exactly the configured records key")
            value = value[key]
        if type(value) is not list or not value or len(value) > self.configuration.max_rows:
            raise FileAdapterError("JSON requires a nonempty bounded array of records")
        if any(type(row) is not dict for row in value):
            raise FileAdapterError("JSON record shape must be objects")
        names = validate_headers(list(value[0]), self.configuration.max_columns)
        if any(set(row) != set(names) for row in value):
            raise FileAdapterError("JSON records must have identical declared fields; use explicit nulls")
        return value, names

    def discover_schema(self):
        rows, names = self._records()
        return {name: '|'.join(sorted({type(row[name]).__name__ for row in rows})) for name in names}

    def extract(self, *, batch_size=10000):
        with self._extraction(batch_size):
            records, names = self._records()
            for offset in range(0, len(records), batch_size):
                self._ensure_open()
                chunk = records[offset:offset + batch_size]
                rows = [[row[name] for name in names] for row in chunk]
                ids = [f'record-{i + 1}' for i in range(offset, offset + len(chunk))]
                yield self._batch(rows, names, ids, offset)
