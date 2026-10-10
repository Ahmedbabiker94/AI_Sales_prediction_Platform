"""Strict CSV records with explicitly configured lexical conversions."""
import codecs
import csv
from dataclasses import dataclass
from io import TextIOWrapper

from src.data.adapters.base import SourceType
from src.data.adapters.file_common import FileAdapter, FileAdapterError, FileConfiguration, validate_headers
from src.data.contracts import DataFormat


@dataclass(frozen=True)
class CSVConfiguration(FileConfiguration):
    encoding: str = 'utf-8'
    delimiter: str = ','
    has_header: bool = True
    field_names: tuple[str, ...] = ()
    column_types: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        object.__setattr__(self, 'field_names', tuple(self.field_names))
        object.__setattr__(self, 'column_types', tuple(tuple(pair) for pair in self.column_types))

    def validate(self):
        super().validate()
        try:
            if type(self.encoding) is not str or not self.encoding.strip():
                raise LookupError()
            if not codecs.lookup(self.encoding)._is_text_encoding:
                raise LookupError()
        except (LookupError, TypeError, ValueError):
            raise FileAdapterError("unsupported CSV encoding") from None
        if type(self.delimiter) is not str or len(self.delimiter) != 1 or self.delimiter in '\r\n\x00"':
            raise FileAdapterError("CSV delimiter must be a single supported character")
        if type(self.has_header) is not bool or (self.has_header and self.field_names):
            raise FileAdapterError("configure either a file header or explicit field names")
        if not self.has_header:
            validate_headers(self.field_names, self.max_columns)
        if any(len(pair) != 2 or type(pair[0]) is not str or not pair[0].strip()
               or pair[1] not in ('text', 'integer', 'float', 'boolean') for pair in self.column_types):
            raise FileAdapterError("invalid explicit CSV column type")
        if len({name for name, _ in self.column_types}) != len(self.column_types):
            raise FileAdapterError("duplicate CSV column type declarations")


class CSVAdapter(FileAdapter):
    configuration_type = CSVConfiguration
    source_type = SourceType.CSV
    source_format = DataFormat.CSV

    def _header(self, reader):
        names = next(reader, None) if self.configuration.has_header else self.configuration.field_names
        names = validate_headers(names, self.configuration.max_columns)
        if not set(dict(self.configuration.column_types)) <= set(names):
            raise FileAdapterError("CSV type declarations reference missing fields")
        return names

    def discover_schema(self):
        with self._binary() as stream:
            with TextIOWrapper(stream, encoding=self.configuration.encoding, errors='strict', newline='') as text:
                try:
                    names = self._header(csv.reader(text, delimiter=self.configuration.delimiter, strict=True))
                    declared = dict(self.configuration.column_types)
                    return {name: declared.get(name, 'text') for name in names}
                except (UnicodeError, csv.Error):
                    raise FileAdapterError("invalid CSV header or encoding") from None

    def _row(self, values, names):
        if len(values) != len(names):
            raise FileAdapterError("CSV row width does not match header")
        if any(len(v) > self.configuration.max_cell_characters for v in values):
            raise FileAdapterError("CSV cell limit exceeded")
        types = dict(self.configuration.column_types)
        result = []
        for name, value in zip(names, values):
            kind = types.get(name, 'text')
            if kind == 'text':
                result.append(value)
            elif value == '':
                result.append(None)  # explicit typed empty-field null semantics
            else:
                try:
                    if kind == 'integer':
                        result.append(int(value))
                    elif kind == 'float':
                        result.append(float(value))
                    else:
                        if value not in ('0', '1', 'true', 'false', 'True', 'False'):
                            raise ValueError()
                        result.append(value in ('1', 'true', 'True'))
                except ValueError:
                    raise FileAdapterError("CSV value violates declared lexical type") from None
        return result

    def extract(self, *, batch_size=10000):
        with self._extraction(batch_size), self._binary() as stream:
            with TextIOWrapper(stream, encoding=self.configuration.encoding, errors='strict', newline='') as text:
                try:
                    reader = csv.reader(text, delimiter=self.configuration.delimiter, strict=True)
                    names = self._header(reader)
                    rows, ids, offset, count = [], [], 0, 0
                    for values in reader:
                        self._ensure_open()
                        count += 1
                        if count > self.configuration.max_rows:
                            raise FileAdapterError("CSV row limit exceeded")
                        rows.append(self._row(values, names)); ids.append(f'record-{count}')
                        if len(rows) == batch_size:
                            yield self._batch(rows, names, ids, offset, metadata={"csv_encoding": self.configuration.encoding,
                                "csv_delimiter": self.configuration.delimiter, "csv_column_types": dict(self.configuration.column_types)})
                            self._ensure_open()
                            offset = count; rows, ids = [], []
                    if rows:
                        yield self._batch(rows, names, ids, offset, metadata={"csv_encoding": self.configuration.encoding,
                            "csv_delimiter": self.configuration.delimiter, "csv_column_types": dict(self.configuration.column_types)})
                    if not count:
                        raise FileAdapterError("CSV contains no records")
                except (UnicodeError, csv.Error, OSError):
                    raise FileAdapterError("invalid CSV content or encoding") from None
