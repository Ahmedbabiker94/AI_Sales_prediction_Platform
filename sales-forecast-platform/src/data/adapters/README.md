# R-02A integration foundation

No concrete connectors are included. Factories are supplied explicitly to
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
