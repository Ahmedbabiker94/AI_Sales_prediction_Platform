"""Explicit connector selection without discovery, imports, or network probes."""
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import codecs
import json
from pathlib import Path
from urllib.parse import urlsplit
import zipfile

from src.data.adapters.base import SourceAdapter, SourceType


class SourceSelectionError(ValueError):
    pass


class _InconclusiveSignature(SourceSelectionError):
    def __init__(self, eligible_types):
        self.eligible_types = frozenset(eligible_types)
        super().__init__("bounded detection inconclusive; explicit compatible source type is required")


def _reject_json_constant(value):
    raise ValueError("nonstandard JSON constant")


@dataclass(frozen=True)
class SourceConfiguration:
    """Non-secret selection descriptor; connector-specific options stay external.

    Local references are paths. Network references are opaque resource names:
    actual endpoints/credentials must be supplied to future connectors through
    approved configuration. A URL is intentionally not a network authorization.
    """
    source_reference: str
    source_type: SourceType | None = None

    def __post_init__(self):
        if not isinstance(self.source_reference, str) or not self.source_reference.strip():
            raise SourceSelectionError("source reference must be nonblank")
        if self.source_type is not None and not isinstance(self.source_type, SourceType):
            raise SourceSelectionError("source type must be an explicit supported SourceType")
        if urlsplit(self.source_reference).scheme or "://" in self.source_reference:
            raise SourceSelectionError("URLs are not accepted as authorized source references")


def _text_signature(text, *, bounded):
    if "\x00" in text:
        raise SourceSelectionError("unsupported binary content or configured encoding")
    if text.lstrip().startswith(("{", "[")):
        if bounded:
            raise _InconclusiveSignature((SourceType.JSON,))
        try:
            value = json.loads(text, parse_constant=_reject_json_constant)
        except (ValueError, RecursionError):
            raise SourceSelectionError("malformed or unsupported JSON content") from None
        if isinstance(value, (dict, list)):
            return SourceType.JSON
    raise _InconclusiveSignature((SourceType.CSV,))


def identify_local_file(path: str | Path, *, csv_encoding: str | None = None) -> SourceType:
    """Bounded signatures; configured CSV encoding never overrides binary formats.

    Unspecified CSV detection remains conservative. Factories may explicitly
    declare csv_encoding; extraction must still validate the entire source.
    """
    # Local import avoids a module initialization cycle with FileConfiguration.
    from src.data.adapters.file_common import FileAdapterError, open_regular_file
    if csv_encoding is not None:
        if type(csv_encoding) is not str or not csv_encoding.strip():
            raise SourceSelectionError("unsupported CSV encoding")
        try:
            if not codecs.lookup(csv_encoding)._is_text_encoding:
                raise LookupError()
        except (LookupError, ValueError):
            raise SourceSelectionError("unsupported CSV encoding") from None
    try:
        stream = open_regular_file(path)
    except FileAdapterError:
        raise SourceSelectionError("local source must be an accessible regular file") from None
    with stream:
        prefix = stream.read(65537)
        if prefix[:4] == b"PAR1":
            stream.seek(0, 2)
            if stream.tell() < 12:
                raise SourceSelectionError("truncated Parquet signature")
            stream.seek(-4, 2)
            if stream.read(4) != b"PAR1":
                raise SourceSelectionError("invalid Parquet signature")
            return SourceType.PARQUET
        if prefix[:4] == b"PK\x03\x04":
            stream.seek(0, 2)
            if stream.tell() > 16 * 1024 * 1024:
                raise _InconclusiveSignature((SourceType.EXCEL,))
            stream.seek(0)
            try:
                with zipfile.ZipFile(stream) as archive:
                    names = set(archive.namelist())
                    if "[Content_Types].xml" in names and "xl/workbook.xml" in names:
                        return SourceType.EXCEL
            except zipfile.BadZipFile:
                raise SourceSelectionError("invalid archive signature") from None
            raise SourceSelectionError("unsupported or ambiguous archive")
    if prefix.startswith((b"PK", b"PAR")):
        raise SourceSelectionError("unsupported or malformed binary signature")
    # A confirmed UTF-8 JSON signature also conflicts with non-UTF-8 CSV config.
    if csv_encoding is not None:
        try:
            utf8_text = codecs.getincrementaldecoder('utf-8-sig')().decode(prefix, final=len(prefix) <= 65536)
        except UnicodeError:
            pass
        else:
            if utf8_text.lstrip().startswith(("{", "[")):
                return _text_signature(utf8_text, bounded=len(prefix) > 65536)
    try:
        decoder = codecs.getincrementaldecoder(csv_encoding or 'utf-8-sig')(errors='strict')
        text = decoder.decode(prefix, final=len(prefix) <= 65536)
        if type(text) is not str:
            raise TypeError()
    except (UnicodeError, LookupError, TypeError, ValueError):
        raise SourceSelectionError("unsupported content or configured encoding") from None
    return _text_signature(text, bounded=len(prefix) > 65536)


def select_connector(configuration: SourceConfiguration,
                     registry: Mapping[SourceType, Callable[[SourceConfiguration], SourceAdapter]],
                     *, authorize_network: Callable[[SourceConfiguration], bool] | None = None) -> SourceAdapter:
    """Only explicitly supplied factories are eligible. Nothing connects here.

    authorize_network is a caller-owned policy hook, not built-in authentication.
    No PostgreSQL, REST or OData selection is possible without an affirmative policy.
    Future connectors must independently enforce read-only SQL and SSRF controls.
    """
    kind = configuration.source_type
    if kind in (SourceType.POSTGRESQL, SourceType.REST, SourceType.ODATA):
        if authorize_network is None or authorize_network(configuration) is not True:
            raise SourceSelectionError("network connector requires explicit caller authorization")
    else:
        path = Path(configuration.source_reference)
        if kind is None:
            kind = identify_local_file(path)
        else:
            if not path.is_file():
                raise SourceSelectionError("local source must be an accessible regular file")
            try:
                factory = registry.get(kind)
                encoding = getattr(factory, 'csv_encoding', None) if kind is SourceType.CSV else None
                detected = identify_local_file(path, csv_encoding=encoding)
            except _InconclusiveSignature as error:
                if kind not in error.eligible_types:
                    raise SourceSelectionError("configured source type is incompatible with bounded signature") from None
                # Explicit selection delegates full validation to the connector.
            else:
                if detected is not kind:
                    raise SourceSelectionError("configured source type conflicts with local signature")
    factory = registry.get(kind)
    if factory is None:
        raise SourceSelectionError("no connector is explicitly registered for this source type")
    adapter = factory(configuration)
    if not isinstance(adapter, SourceAdapter):
        raise SourceSelectionError("registered factory must return a SourceAdapter")
    try:
        adapter.validate_configuration()
        if adapter.identify_source_type() is not kind:
            raise SourceSelectionError("connector identity conflicts with selected source type")
    except Exception:
        adapter.close()
        raise
    # Accessibility/schema/extraction methods are never invoked in selection.
    return adapter
