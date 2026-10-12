# R-02A integration foundation

R-02B supplies CSV, XLSX, JSON and Parquet connectors through a caller-owned
`file_connector_registry`. R-02C adds PostgreSQL/REST through
`network_connector_registry`. Factories are supplied explicitly to
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

## Network connector boundaries

PostgreSQL must use explicitly supplied configuration and a least-privilege
read-only role/transaction, bounded fetches, statement timeouts, and parameterized
queries. Do not accept arbitrary SQL from untrusted source declarations. SQL
belongs to storage implementations; business mapping remains in the mapper.

REST must enforce an approved origin allowlist, TLS, bounded responses/pages,
timeouts, public destination-address checks and DNS/rebinding protections.
Redirects must be disabled or each destination independently validated. Reject
loopback/private/link-local/metadata destinations. A URL or a successful DNS
lookup is not access authorization. No environment credential discovery or
unauthenticated probing is permitted. R-02C implements the connector controls
described below; caller authentication and deployment verification are separate.

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
deployment verification of PostgreSQL and REST security; the R-02C connector
implementation below does not complete those production integration requirements.

## R-02C PostgreSQL and REST connectors

Both use the existing `SourceAdapter`, `SourceBatch` and unchanged
`CanonicalMapper`. SQL/storage extraction is confined to the PostgreSQL adapter;
operational providers/repositories are not imported or modified. No source
connector grants training admission, persistence permission or dataset completion.

Constructors and selection remain inert. `network_connector_registry` registers
only explicitly supplied configurations. Existing `select_connector` still needs
an affirmative `authorize_network` callback. Each operational method additionally
requires a trusted `authorize_scope(configuration, asset)` callback returning
exactly `True`, and the configuration organization must match the asset. Neither
callback is implemented authentication: the application must bind them to real
permissions, approved destinations/relations, credentials and organization scope.
Canonical metadata, resolution evidence and `metadata_ready` never authorize
network access. Do not populate provenance with secrets.

```python
registry = network_connector_registry(
    asset, authorize_scope=application_scope_policy,
    postgres=database_settings, rest=api_settings,
    resolve_postgres_credentials=database_secret_resolver,
    resolve_rest_credentials=api_secret_resolver,
)
with select_connector(
    SourceConfiguration(database_settings.source_reference, SourceType.POSTGRESQL),
    registry, authorize_network=application_selection_policy,
) as source:
    for batch in source.extract(batch_size=1000):
        mapped = CanonicalMapper(mapping).map_batch(batch)
        stage_only(mapped)  # Future caller-owned R-03 boundary, not publication.
```

### PostgreSQL

`PostgreSQLConfiguration` declares host/port/database, an opaque credential
reference, organization, schema/table, selected columns, an explicit relation
allowlist, unique ordering/key fields, optional equality filters and tenant
column. There is no arbitrary SQL/DSN configuration. Identifiers use a conservative
ASCII identifier vocabulary and `psycopg2.sql.Identifier`; values are placeholders.
If configured, the tenant predicate always compares the configured column with
the authorized organization UUID, independently of other filters. Without that
predicate, the trusted scope policy must authorize an organization-exclusive
table/view or explicitly permitted shared source. RLS is never assumed.

Secret resolution returns `PostgreSQLCredentials(username, password)` only when
an operation is authorized. Credentials are excluded from repr/provenance/errors.
No operational database configuration or environment credentials are discovered.
TLS `verify-full` is mandatory; an explicit CA path may be supplied. Connection
timeout defaults to 10 seconds; statement and idle-transaction limits default to
30 seconds, with configuration upper bounds. Extraction begins read-only and
uses `REPEATABLE READ`, verifies transaction mode, rejects privileged role flags,
schema CREATE permission, and relation/column write privileges. These checks
cover the selected source, not a global audit of every role grant. Administrators
must provision a genuinely least-privilege source role and trust approved view
definitions; no role/schema mutations are performed by the connector.

Regular/partitioned tables and materialized views require a valid, unconditional,
non-expression unique index matching the configured key columns. Approved views
have no index to verify: their unique ordering contract must be guaranteed by the
trusted source owner. All extracted keys must be nonnull and unique; duplicate
keys fail the overall extraction, possibly after earlier batches were yielded.
The metadata catalog checks target modern PostgreSQL (index key/include metadata).
Real server compatibility is not established by the controlled doubles.

Ordering keys support only built-in SMALLINT/INTEGER/BIGINT (exact Python `int`,
excluding booleans and outside-range values), NUMERIC/DECIMAL (finite `Decimal`),
UUID (`UUID` or its exact lowercase hyphenated string), and TEXT/VARCHAR (exact
`str`). The adapter queries the exposed table/view attributes in PostgreSQL's
catalogs, verifies type OIDs/names/namespace, and checks fetched key type OIDs
against that metadata. Unknown/extension types, domains, date/time keys, floating
keys and incompatible Python representations fail closed. These checks apply to
views as well as tables; returned Python types alone never establish SQL safety.
Text keys require built-in `pg_catalog` C/POSIX libc deterministic collations;
default, ICU, nondeterministic, custom and other unverified collations are rejected.
No arbitrary collation normalization or CITEXT equality is implemented. Catalog
checks require PostgreSQL 12 or newer; real server verification remains required.

Decimal identities strip only insignificant coefficient zeros using tuple
inspection, without Decimal arithmetic, float conversion or context-dependent
rounding. Equivalent scales and signed zeros share identity, while distinct
high-precision values remain distinct. UUID identity accepts both supported
representations consistently. Original extracted values are not rewritten for
identity; existing Arrow storage may use a common column scale without changing
numeric values. Generic record identities still distinguish integer/Decimal/text
types; PostgreSQL rejects representations incompatible with its verified type
instead of guessing cross-type equality. Duplicate tracking retains one hash per
record up to `max_rows`; this is a row-count guard, not a hard memory budget.

A named non-holdable server cursor uses bounded `fetchmany` calls. Source IDs
are typed structured-key hashes, stable across batch sizes for unchanged source
records. Transactions always roll back and close; cancellation requires closing
the iterator or exiting the adapter context. Snapshot consistency is within a
single extraction transaction, not across separate calls. PostgreSQL NUMERIC
values use exact Arrow Decimal storage where representable, never float casts;
unsupported values fail through extraction or the existing mapper. There is no
sign-based target transformation, currency/unit inference, repair or admission.

### REST

`RESTConfiguration` declares an explicit endpoint and allowed origins (scheme,
host, port), organization, source record keys, response envelope and pagination.
HTTPS is required by default; userinfo, fragments and endpoint query strings are
rejected. Only GET is supported. Authentication is none, Bearer or Basic via
an opaque secret reference and explicit `RESTCredentials` resolver. There are no
environment proxies, automatic cookies, destination guessing or implicit secrets.

Each request/retry resolves the configured host and rejects any prohibited answer,
including loopback/private/link-local/metadata/reserved/multicast/unspecified and
IPv4-mapped prohibited addresses. The transport connects directly to one validated
numeric address, retaining TLS hostname verification and SNI for the configured
host. It does not resolve the hostname again during connection. Redirect responses
always fail, and credentials are resolved only after scope/destination approval.
The default DNS resolver bounds caller waiting by the configured connect timeout
and allows at most four outstanding native lookups. A timed-out libc lookup cannot
be cancelled; its daemon worker retains a slot until it returns, and exhausted
capacity fails closed. PostgreSQL connection timing relies on libpq's configured
timeout; real DNS/TLS/server timing still requires isolated deployment verification.
Injected transports/resolvers are trusted test dependencies and can invalidate
these guarantees if substituted with unsafe implementations.

`RESTNetworkPolicy` permits explicit controlled loopback tests only. HTTP also
requires the loopback test exception and can never target a public address. These
switches are not for production source declarations. The suite includes a real
isolated loopback HTTP transport test, not a live customer API or real HTTPS test.

Supported responses are strict UTF-8 JSON arrays (unpaginated), exact configured
single-key envelopes, page-number envelopes with a boolean continuation field,
or cursor envelopes with an opaque token/null. Cursor tokens are URL-encoded
parameters on the same endpoint, never followed as URLs. Empty pages, changed
schemas, duplicate keys/record IDs, repeated tokens, unsupported shapes and
nonfinite numbers fail closed. Record references are hashed keys; metadata stores
page ordinals, never endpoint URLs, credentials or cursor tokens.

Defaults: 1,000 records/page, 1,000 pages, 1,000,000 records total, 10,000 rows/batch,
256 columns, 4 MiB response, 16 KiB request, depth 32 (maximum 64), and 131,072
characters/cell. Connect timeout and header/body read deadline default to 10
seconds. Framing/header lines are capped at 8 KiB and cumulative framing lines
at 64 KiB. Compressed responses are unsupported. Bodies are materialized within
the response-byte cap; this is not a hard process-RSS bound.

Only connection/timeouts and HTTP 429/502/503/504 are retried, at most three retries
(default two), solely for GET. Retry-After seconds/dates are honored within
per-delay and extraction-wide sleep budgets (defaults 30/60 seconds); excessive
rate limits fail instead of sleeping indefinitely. Other statuses, malformed
responses, TLS verification failures and redirects are not retried. Every request
owns and closes its connection; completion, failure and iterator cancellation
close the transport. Unexpected failures use sanitized public errors.

R-03 still owns immutable source/snapshot policy, staging/finalization, cross-batch
canonical admission, bounded duplicate storage and invalid Unicode-surrogate
rejection before publishing. Repeated API calls cannot manufacture snapshot
consistency unless the upstream service provides it. No production workflow uses
these connectors yet. Existing dependencies suffice: psycopg2, pandas, PyArrow and
the Python standard library. No packages are added for R-02C.

## R-02D-2 OData V4 foundation

`ODataAdapter` implements the existing `SourceAdapter` directly. It does not
subclass `RESTAdapter`, map vendor business fields, or grant dataset completion,
persistence permission or training admission. `SourceType.ODATA` requires the same
explicit selection authorization as REST/PostgreSQL. Register it using the
optional `odata` configuration in `network_connector_registry`.

The HTTP seam is small: `http_client.py` shares destination validation and the
bounded GET/retry loop. REST keeps its existing configuration, JSON decoder,
credentials, pinned transport, DNS worker cap and compatibility imports. OData
uses that same GET-only pinned transport; POST/token acquisition is not enabled.

`ODataConfiguration` declares an opaque source/connection reference, organization,
service root, origin allowlist, entity set, projected fields, entity key fields,
ascending order fields, credential reference and opaque authorization context.
All keys must be projected and included in ordering. Optional fixed
`scope_parameters` are application-approved query parameters, not `$filter`
expressions or proof of tenant authorization. Service root paths are explicit
ASCII paths without percent encoding, dot segments, double slashes or queries.
Entity/property names use conservative ASCII identifiers. No auto-discovery of
unconfigured services or entities takes place.

Both `authorize_scope(configuration, asset)` and
`authorize_request(configuration, asset, purpose)` must affirm permission.
Purposes are `metadata` and `entity`, on exact configured resource paths. The
trusted credential provider receives the opaque reference plus keyword arguments
`organization_id`, `authorization_context` and `purpose`, and returns existing
`RESTCredentials(token=...)`. It must bind these to authenticated permissions and
organization-scoped credentials; self-declared configuration is not authority.
Tokens are resolved after destination approval on every attempt. Neither URLs,
tokens nor pagination links enter generated provenance. HTTPS is mandatory except
for the existing explicit caller-only loopback test policy.

Supported protocol is **OData 4.0**, not V2 or 4.01. A `4.0` OData-Version response
header is required. Metadata discovery fetches only the configured `$metadata`
resource and parses XML with DefusedXML, forbidding DTDs/entities/external
references. CSDL entity-set/type/property/key declarations are inspected locally;
references are never fetched. Inherited, abstract/open selected entities and
unsupported projected types/facets reject. Navigation properties are never
projected or traversed. Discovery reports the configured projected properties.

Defaults: metadata 2 MiB, 10,000 XML nodes, depth 32 (maximum configurable 64);
256 properties on the selected type; entity responses 4 MiB; 1,000 rows/page;
1,000 pages; 1,000,000 rows; 10,000 rows/batch; 131,072 characters/cell; 16 KiB
requests. Timeouts and retry/sleep limits follow R-02C defaults. XML/JSON pages
are parsed in memory within byte limits; these are not hard process-RSS bounds.
Duplicate hashes grow to the configured row limit. R-03 owns bounded duplicate
storage. Cancellation requires closing the iterator or exiting its adapter context.

Supported projected scalars:

- `Edm.String`: unchanged valid UTF-8 text with declared/configured length limits.
- `Edm.Boolean`: JSON booleans only.
- `Edm.Int32`/`Edm.Int64`: exact bounded integers; Int64 strings are decoded only
  under `IEEE754Compatible=true`. No float intermediates or numeric guessing.
- `Edm.Decimal`: exact Decimal with fixed precision up to 38 and fixed scale;
  unspecified precision is capped at 38, and unspecified scale follows CSDL's
  default zero. Variable/floating scale is unsupported. Typed Arrow decimal128
  storage preserves numeric values, including signed/zero targets. Under
  IEEE754Compatible, decimal strings use plain decimal notation; otherwise JSON
  numbers are parsed directly as Decimal, without binary floating conversion.
- `Edm.Date`: exact ISO calendar dates.
- `Edm.DateTimeOffset`: explicit offsets/Z, declared precision up to seven digits,
  normalized to UTC instants without loss of supported precision. Ambiguous
  timezone forms and values outside pandas nanosecond range reject.
- Explicit nulls only for nullable properties; missing projected fields reject.

Collections, complex values, enums, binary values and unsupported EDM types reject
when projected. Boolean keys are unsupported. Blank/overlong string keys fail the
existing source identity constraints. Identities use entity set, sorted key names,
declared key types and exact decoded values through existing structured hashing.
Decimal scale variants/signed zeros share identity. String key equality is exact;
this is not a database-collation or vendor-specific identity normalization engine.

Entity responses require a strict `value` collection and matching `@odata.context`;
optional annotations are count and nextLink only. Rows must expose exactly the
projected fields. Empty pages reject. Relative and absolute continuations must
remain on the exact configured entity path/origin and retain every fixed scope,
projection, ordering and `$top` parameter. Only one `$skiptoken` or `$skip` may be
added; services that omit those fixed options from their links are outside this
initial supported subset. Encoded paths, scope changes, redirects, malformed
queries and cycles reject. DNS/address approval and purpose authorization are
repeated for every request/retry, including later pages.

The SHA-256 fingerprint covers the exact metadata bytes. Optional
`expected_metadata_fingerprint` pins an approved document; even formatting changes
then require explicit approval. Metadata is fetched once per extraction, not on
every page. Changed contexts, projected fields or incompatible values reject;
unannounced metadata-only changes during extraction cannot be independently
detected. Generated batch metadata retains the schema fingerprint/version and
marks upstream snapshot consistency **unverified**. Ordering, count annotations
and reaching the last page do not prove a snapshot or complete-dataset admission.

The unchanged CanonicalMapper still requires explicit start/end periods, declared
metric meaning and directly supplied unit/currency. No posting-date period
construction, sign-based target splitting, measurement inference or aggregation
is performed. Tests prove canonical equivalence with independent Parquet fixtures
for exact Decimal records and existing REST extraction for exact binary numeric
values. REST's existing float decoder is not claimed to preserve arbitrary Decimal
precision. Synthetic mocks and a controlled loopback HTTP server do not establish
SAP, Dynamics, Oracle or other vendor compatibility.

Existing DefusedXML, pandas and PyArrow suffice; no dependencies are added.
R-02D-3 owns V2, OAuth acquisition/refresh and verified enterprise profiles. R-03
owns immutable snapshots, staging/finalization and quality admission. Production
workflow integration, vendor sandbox verification and deployment remain deferred.
