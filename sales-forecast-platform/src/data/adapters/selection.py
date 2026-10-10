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


def identify_local_file(path: str | Path) -> SourceType:
    """Bounded local signature checks; never decompress or extract archives.

    CSV lacks a reliable signature and must be selected explicitly. Large JSON
    also needs explicit type; detection does not read unbounded input. Connector
    implementations must still validate full content before returning records.
    """
    path = Path(path)
    if not path.is_file():
        raise SourceSelectionError("local source must be an accessible regular file")
    with path.open("rb") as stream:
        prefix = stream.read(65537)
        if prefix[:4] == b"PAR1":
            if path.stat().st_size < 12:
                raise SourceSelectionError("truncated Parquet signature")
            stream.seek(-4, 2)
            if stream.read(4) != b"PAR1":
                raise SourceSelectionError("invalid Parquet signature")
            return SourceType.PARQUET
    if prefix[:4] == b"PK\x03\x04":
        # XLSX is a ZIP container. Bound its central directory before inspection.
        if path.stat().st_size > 16 * 1024 * 1024:
            raise _InconclusiveSignature((SourceType.EXCEL,))
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if "[Content_Types].xml" in names and "xl/workbook.xml" in names:
                    return SourceType.EXCEL
        except zipfile.BadZipFile as error:
            raise SourceSelectionError("invalid archive signature") from error
        raise SourceSelectionError("unsupported or ambiguous archive")
    if b"\x00" in prefix or prefix.startswith((b"PK", b"PAR")):
        raise SourceSelectionError("unsupported or malformed binary signature")
    try:
        text = prefix.decode("utf-8-sig")
    except UnicodeError:
        # A bounded read may end halfway through a UTF-8 character.
        if len(prefix) > 65536:
            try:
                # Only tolerate an incomplete trailing codepoint, not interior errors.
                text = codecs.getincrementaldecoder("utf-8-sig")().decode(prefix, final=False)
            except UnicodeError:
                raise SourceSelectionError("unsupported binary content") from None
        else:
            raise SourceSelectionError("unsupported binary content") from None
    if text.lstrip().startswith(("{", "[")):
        if len(prefix) > 65536:
            raise _InconclusiveSignature((SourceType.JSON,))
        try:
            value = json.loads(text, parse_constant=_reject_json_constant)
        except (ValueError, RecursionError):
            raise SourceSelectionError("malformed or unsupported JSON content") from None
        if isinstance(value, (dict, list)):
            return SourceType.JSON
    raise _InconclusiveSignature((SourceType.CSV,))


def select_connector(configuration: SourceConfiguration,
                     registry: Mapping[SourceType, Callable[[SourceConfiguration], SourceAdapter]],
                     *, authorize_network: Callable[[SourceConfiguration], bool] | None = None) -> SourceAdapter:
    """Only explicitly supplied factories are eligible. Nothing connects here.

    authorize_network is a caller-owned policy hook, not built-in authentication.
    No PostgreSQL or REST selection is possible without an affirmative policy.
    Future connectors must independently enforce read-only SQL and SSRF controls.
    """
    kind = configuration.source_type
    if kind in (SourceType.POSTGRESQL, SourceType.REST):
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
                detected = identify_local_file(path)
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
