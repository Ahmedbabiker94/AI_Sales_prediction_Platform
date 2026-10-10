"""Bounded Arrow batch extraction with scalar-only source schemas."""
from dataclasses import dataclass

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src.data.adapters.base import SourceType
from src.data.adapters.file_common import FileAdapter, FileAdapterError, FileConfiguration, positive_integer, validate_headers
from src.data.contracts import DataFormat


@dataclass(frozen=True)
class ParquetConfiguration(FileConfiguration):
    max_decoded_bytes: int = 256 * 1024 * 1024

    def validate(self):
        super().validate()
        positive_integer(self.max_decoded_bytes, 'max_decoded_bytes')


def _supported_scalar(kind):
    return any(check(kind) for check in (pa.types.is_null, pa.types.is_boolean, pa.types.is_integer,
        pa.types.is_floating, pa.types.is_string, pa.types.is_large_string, pa.types.is_date,
        pa.types.is_timestamp, pa.types.is_decimal))


class ParquetAdapter(FileAdapter):
    configuration_type = ParquetConfiguration
    source_type = SourceType.PARQUET
    source_format = DataFormat.PARQUET

    def _file(self, stream):
        file = pq.ParquetFile(stream, thrift_string_size_limit=1024 * 1024, thrift_container_size_limit=100_000)
        try:
            names = validate_headers(file.schema_arrow.names, self.configuration.max_columns)
            if any(not _supported_scalar(field.type) for field in file.schema_arrow):
                raise FileAdapterError("unsupported Parquet schema: only configured scalar types are accepted")
            if not file.metadata.num_rows or file.metadata.num_rows > self.configuration.max_rows:
                raise FileAdapterError("Parquet record count is empty or exceeds limit")
            decoded = sum(file.metadata.row_group(i).total_byte_size for i in range(file.metadata.num_row_groups))
            if decoded > self.configuration.max_decoded_bytes:
                raise FileAdapterError("Parquet declared decoded byte limit exceeded")
            return file, names
        except Exception:
            file.close()
            raise

    def discover_schema(self):
        with self._binary() as stream:
            try:
                file, _ = self._file(stream)
                with self._owned(file):
                    return {field.name: str(field.type) for field in file.schema_arrow}
            except FileAdapterError:
                raise
            except (pa.ArrowException, OSError, ValueError):
                raise FileAdapterError("invalid Parquet schema") from None

    def extract(self, *, batch_size=10000):
        with self._extraction(batch_size), self._binary() as stream:
            try:
                file, names = self._file(stream)
                with self._owned(file):
                    offset = 0
                    decoded = 0
                    for arrow_batch in file.iter_batches(batch_size=batch_size, use_threads=False):
                        self._ensure_open()
                        decoded += arrow_batch.nbytes
                        if decoded > self.configuration.max_decoded_bytes:
                            raise FileAdapterError("Parquet decoded byte limit exceeded")
                        frame = arrow_batch.to_pandas(types_mapper=pd.ArrowDtype)
                        for field in names:
                            if pa.types.is_string(arrow_batch.schema.field(field).type) or pa.types.is_large_string(arrow_batch.schema.field(field).type):
                                if frame[field].dropna().map(len).gt(self.configuration.max_cell_characters).any():
                                    raise FileAdapterError("Parquet string cell limit exceeded")
                        ids = [f'record-{i + 1}' for i in range(offset, offset + len(frame))]
                        yield self._batch(frame, names, ids, offset)
                        self._ensure_open()
                        offset += len(frame)
            except FileAdapterError:
                raise
            except (pa.ArrowException, OSError, ValueError):
                raise FileAdapterError("invalid Parquet content") from None
