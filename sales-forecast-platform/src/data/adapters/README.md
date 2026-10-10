# R-02A integration foundation

R-02B supplies CSV, XLSX, JSON and Parquet connectors through a caller-owned
`file_connector_registry`. PostgreSQL/REST remain deferred. Factories are supplied explicitly to
`select_connector`; no dynamic imports, plugin discovery, credential discovery,
or network probing occurs. Constructors and configuration/type validation must
be inert. Accessibility tests, schema discovery and extraction are separate
explicit lifecycle operations; context-manager exit closes connector resources.

Local automatic selection checks bounded JSON content, Parquet header/footer
magic, or XLSX container member names. These are identification checks, not full
format validation. CSV has no reliable magic and requires explicit selection.
Unknown/ambiguous content fails automatic selection. Large JSON/archives are
not auto-detected; explicit compatible selection delegates full content validation
to the connector. Malformed/unsupported signatures and positively detected type
conflicts fail even with explicit selection. Selection is never content acceptance.
Concrete connectors must validate their full content before returning batches.
URLs are not accepted as source references; network source references are opaque
names for separately approved configuration. Registry presence alone does not
authorize PostgreSQL/REST selection: an affirmative caller-owned policy hook is
required. That hook is not an authentication or authorization system.

## Required future connector boundaries

PostgreSQL must use explicitly supplied configuration and a least-privilege
read-only role/transaction, bounded fetches, statement timeouts, and parameterized
queries. Do not accept arbitrary SQL from untrusted source declarations. SQL
belongs to storage implementations; business mapping remains in the mapper.

REST must enforce an approved origin allowlist, TLS, bounded responses/pages,
timeouts, public destination-address checks and DNS/rebinding protections.
Redirects must be disabled or each destination independently validated. Reject
loopback/private/link-local/metadata destinations. A URL or a successful DNS
lookup is not access authorization. No environment credential discovery or
unauthenticated probing is permitted. These controls are requirements for R-02C,
not implemented network guarantees in R-02A.

Credential values and endpoint URLs must never enter batch provenance, errors,
logs or lineage. Source metadata must contain non-secret provenance only; future
connectors must whitelist safe metadata instead of copying connection settings.
Current selection accepts opaque names, and batch provenance rejects URL IDs.

## Canonical mapping

`CanonicalMapper(mapping).map_batch(batch)` returns `CanonicalBatch` containing
an existing `StructuredDataset` envelope and a bulk DataFrame equivalent to
Observation fields, with auxiliary fields preserved. The envelope's observations
list is empty; consumers must read `records`. SeriesDefinition objects are
created per distinct series, and batch-level LineageRecord references accompany
per-record source identifiers. This is not an alternative dataset schema or a
training-admission decision.

Supported metric meanings are explicit gross sales, returns, damage,
adjustments and signed reported sales; no sign-to-meaning inference occurs.
Only `explicit_bounds` with both start and end fields is supported. Dates must
be typed datetimes or ISO-8601 representations; locale-dependent strings are
rejected rather than interpreted implicitly. The mapper
preserves declared frequency/periods and never aggregates, resamples, fills gaps
or derives endpoints. Custom frequencies and unknown interpretations fail.
Dimension values currently support nonblank strings and integers (including
negative/zero integers); floats and booleans are rejected rather than normalized.
Numeric targets require pandas real numeric dtypes and are copied without casts.
Auxiliary lists, tuples and string-keyed dictionaries are recursively detached;
custom objects and cycles are rejected without copy hooks or stringification.
Numeric auxiliary scalars are checked regardless of dtype. Required auxiliaries
reject null/NaN values, including nested values; optional auxiliaries preserve
null/NaN, but infinity is never treated as missing. Numeric strings remain text.
Bulk observation declarations and source-record ID lengths reuse existing
Observation field constraints.

Units must be directly supplied because canonical Observation requires a unit.
Currency is required when mapping configuration declares it required. These
requirements are business declarations, not automatic classification from free
text. There is no verified authority service; suggested or self-declared
authorized resolution evidence never fills missing metadata. Evidence may be
retained as provenance, and metadata_ready is never used as admission permission.

`map_batches` checks duplicate canonical/source keys across batches using retained
key sets. It yields batches progressively: a later failure requires the caller to
abort its staging operation. Partial batches must not be published or supplied
to training; R-03 owns dataset completion/finalization and admission enforcement.
This is not atomic ingestion, bounded-memory key storage, a streaming framework, or training integration. No model/artifact is
loaded and generic DQ-01 policy evaluation remains separate and unchanged.

## R-02B file connectors

Use `CSVConfiguration`/`CSVAdapter`, `ExcelConfiguration`/`ExcelAdapter`,
`JSONConfiguration`/`JSONAdapter`, or `ParquetConfiguration`/`ParquetAdapter`.
The caller supplies existing `AssetMetadata` describing an immutable source
snapshot, including organization, source, ingestion batch and aware timestamp.
Constructors do not access files. Configuration validation is separate from
`test_access`, `discover_schema`, and full `extract` validation.

```python
settings = CSVConfiguration(
    path="/authorized/local/input.csv", source_dataset="sales-import",
    column_types=(("amount", "float"),),
)
registry = file_connector_registry(asset, csv=settings)
with select_connector(SourceConfiguration(settings.path, SourceType.CSV), registry) as adapter:
    for result in CanonicalMapper(mapping).map_batches(adapter.extract(batch_size=1000)):
        stage_only(result)  # caller-owned; requires R-03 completion/admission
```

`stage_only` illustrates a future caller boundary; no staging implementation is
provided. No adapter grants admission or finalization. A late malformed row,
limit violation or mapping failure invalidates the overall operation even if
earlier batches were yielded. Cancellation requires closing the extraction
iterator or exiting the adapter context; abandoning a live iterator is not a
cleanup guarantee. `close()` is idempotent, invalidates further operations and
releases owned handles. Only one extraction iterator may be active per adapter.

All configurations declare an opaque `source_dataset`, local `path`,
`max_file_bytes` (64 MiB), `max_rows` (1,000,000), `max_columns` (256),
`max_batch_rows` (10,000), and `max_cell_characters` (131,072). Limits are positive
integers; batch size must not exceed the configured maximum. Files must be
regular files. These are explicit processing limits, not hard process-RSS caps.
Caller-authorized local paths may include symlinks; connectors do not establish
an application-level filesystem allowlist. The caller must keep its source
snapshot unchanged during extraction; immutable storage/snapshot enforcement
remains outside these connectors.

- **CSV:** Explicit encoding (default UTF-8), single-character delimiter, file
  header or caller-supplied field names. Strict parser errors, unequal row widths,
  duplicate/blank headers and invalid declared lexical types fail. Fields default
  to unchanged text: leading zeros, empty strings and `NA`/`null` tokens are kept.
  Optional `column_types` declares `text`, `integer`, `float`, or `boolean`;
  numeric/boolean empty fields become null only under that explicit declaration.
  Boolean tokens are exactly `0`, `1`, `true`, `false`, `True`, `False`. No clipping,
  target splitting or business inference occurs. Configured lexical types,
  encoding and delimiter accompany batch metadata. Python's CSV parser also
  imposes its existing process-wide field-size ceiling; the connector does not
  change that global setting. Logical record IDs survive quoted multiline cells.
- **Excel:** XLSX only, parsed with OpenPyXL read-only mode and DefusedXML
  protection. `sheet_name` is required for multiple sheets; `header_row` defaults
  to 1. Formulas (including cached formulas), error cells, macro payloads and
  external links fail closed. There is no formula calculation or reliance on
  cached values. Data rows retain worksheet row numbers and batch metadata stores
  the sheet name. Incorrect worksheet dimension hints are ignored. Expanded ZIP
  bytes are limited to 256 MiB and archive members to 10,000, both configurable.
  Shared strings/styles/workbook metadata can still occupy memory; this is not a
  constant-memory workbook parser. Formatting-only/empty rows are not repaired
  or automatically dropped.
- **JSON:** UTF-8 (optional BOM), a nonempty array of object records, or an exact
  single-key envelope selected by `records_key`. All rows must expose identical
  field names; nullable values require explicit nulls. Duplicate keys,
  NaN/Infinity constants, overflowing floating literals and ambiguous shapes
  fail. `max_depth` defaults to 32 (maximum configurable 64). Parsing materializes
  the entire file within `max_file_bytes`; only emitted batches are chunked.
  Memory exceeds input byte size because Python objects are materialized. This is
  not unlimited or streaming JSON. Integer values remain exact; floating JSON
  literals use Python's standard binary floating representation.
- **Parquet:** PyArrow `iter_batches`, without parallel decoding, preserves
  nullable Arrow scalar columns. Supported types are null, boolean, signed/
  unsigned integers, floating, string, date, timestamp and Decimal. Lists,
  structs, maps, binary, duration and other unsupported types fail before yielding.
  `max_decoded_bytes` defaults to 256 MiB; declared row-group byte sizes are checked
  before decoding, and actual batch buffer sizes are checked during extraction.
  Decoding may allocate a batch/page before the actual-size check; metadata,
  compression and library overhead mean this is not a hard RSS bound.
  Decimal extraction is supported. Exact-precision target mapping is verified
  for the tested Arrow-backed decimal128 representation, including negative and
  zero values; no silent float conversion is introduced. This does not establish
  support for all Decimal representations, including object-dtype targets.

Schema discovery returns source fields/types, not business schema or admission
proof. CSV/Excel discovery validates headers; JSON reads its bounded payload;
Parquet reads/validates metadata. All connectors perform content validation during
extraction. Content signatures ignore extensions and selection stays separate:
explicit registration is required, confirmed conflicts fail, and inconclusive
bounded detection delegates validation to the chosen connector.

Record IDs are source-local logical ordinals (`record-N`) or worksheet references
(`sheet-N-row-N`), stable across batch sizes within an unchanged source snapshot.
Batch IDs identify extraction offsets. No filesystem paths are copied into
lineage. Canonical business rules, duplicate detection, timezone interpretation,
metadata requirements and training policies remain in the existing shared mapper
and quality components. Production workflows do not invoke these connectors yet.

Dependencies: existing pinned PyArrow; OpenPyXL 3.1.5 and DefusedXML 0.7.1 are
new pinned lightweight dependencies. OpenPyXL also installs `et-xmlfile`.

Explicit CSV factories expose their configured encoding as non-secret selection
metadata. Bounded selection respects it, but confirmed JSON/XLSX/Parquet conflicts
still fail and full CSV parsing remains mandatory. Unspecified selection does not
infer a CSV encoding. Local file access requires nonblocking descriptor opening;
platforms without that capability fail closed. Encrypted XLSX archive members and
unsupported archive compression are rejected before workbook parsing.

R-03 still owns dataset staging/finalization, cross-batch admission and rejecting
invalid Unicode surrogates safely before canonical publishing. R-02C owns
PostgreSQL and REST security; these requirements are not implemented here.
