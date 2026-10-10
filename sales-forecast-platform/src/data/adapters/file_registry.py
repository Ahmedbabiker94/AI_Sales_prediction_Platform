"""Caller-owned, explicit file connector registration; no discovery."""
from src.data.adapters.base import SourceType
from src.data.adapters.csv_adapter import CSVAdapter
from src.data.adapters.excel_adapter import ExcelAdapter
from src.data.adapters.json_adapter import JSONAdapter
from src.data.adapters.parquet_adapter import ParquetAdapter
from src.data.adapters.file_common import FileAdapterError


def file_connector_registry(asset, *, csv=None, excel=None, json=None, parquet=None):
    """Only supplied configurations are eligible; endpoints/credentials are absent.

    Returns factories for use with existing select_connector. Construction and
    configuration validation are inert; extract performs full content validation.
    """
    registry = {}
    for kind, cls, configuration in ((SourceType.CSV, CSVAdapter, csv), (SourceType.EXCEL, ExcelAdapter, excel),
                                      (SourceType.JSON, JSONAdapter, json), (SourceType.PARQUET, ParquetAdapter, parquet)):
        if configuration is None:
            continue
        def factory(selection, cls=cls, configuration=configuration):
            if selection.source_reference != configuration.path:
                raise FileAdapterError("selection reference differs from explicitly configured source")
            return cls(configuration, asset)
        if kind is SourceType.CSV:
            # Explicit, non-secret factory metadata for bounded selection only.
            # The adapter still validates configuration and parses full content.
            if type(configuration) is not CSVAdapter.configuration_type:
                raise FileAdapterError("connector configuration type mismatch")
            factory.csv_encoding = configuration.encoding
        registry[kind] = factory
    return registry
