# R7-05B5-1: protected exact-batch read

Internal controlled-offline read boundary only. No API, dashboard, accuracy,
report, scheduler, Celery, model resolver or production authentication binding.
Synthetic test fixtures are not claims of authorized production forecast origin.

## Composition and trust

The trusted host calls `build_canonical_forecast_reader(reader, scope,
operator=...)`. `reader` must be the exact accepted `OfflineForecastPostgres`
reader configuration, with its explicit disposable database identity and guard.
No writer object/password is accepted. This factory does not connect or migrate.
It returns **separate** `ReadAuthority` and `CanonicalForecastReadService` objects.
Only the service and original read handle are given to a consumer. The host keeps
the authority, operator, credential configuration and all private state protected.

The host, after independently authenticating/authorizing its principal, invokes
`establish_principal` for one exact complete tenant/dataset/series scope. The
subject reference is an opaque host audit label, NOT authentication. Tests label
it `synthetic:*`; no real authentication provider is implemented. The host must
explicitly `authorize` an `ExactForecastRead` for that original principal. This
grants disclosure of the complete batch, full lineage and durable receipt, so the
host must authorize those fields as well as values. No partial disclosure is
implemented. A consumer cannot pass a principal string, scope declaration or new
issuer to create authority on the protected service.

Principal and grant handles are opaque, process-local, original-object identities
retained in private records. Copies, serialization, foreign instances, missing
records and detached declarations cannot authenticate. Construction/private-state
control is a trusted-host assumption: Python underscores, frozen declarations and
identity checks are not a sandbox against arbitrary host code or coherently
rewritten private memory/credentials. Different attacker-created compositions do
not become the host's pinned service. Neither result dictionaries nor read DTOs
are capabilities. No cross-process authentication/cryptographic attestation is
claimed.

## Reader Repository -> Reader Provider -> Read Service

`ForecastReadRepository(reader_factory, scope)` reuses ONLY the accepted
`_batch`, `_find` and `_lookup` function objects from the original storage
repository. It does not inherit from or construct that repository and has no
writer, append, migration or transaction-commit API. This preserves the exact
accepted SQL, canonical encoding, row/receipt/pointer checks without duplicating
the verifier or broadening privileges. `ForecastReadProvider.lookup` delegates
only to this reader. The business service contains no SQL.

`ForecastReadConnectionFactory` owns a fresh reader-only connection from setup
through rollback/close, including interruption while the accepted role/identity
guard executes. It preserves explicit disposable identity, Unix socket, credentials,
timeouts, scoped UUID GUC options, fixed role, READ ONLY / REPEATABLE READ and
accepted guard. It separately owns setup because the accepted factory's
pre-yield handler catches Exception rather than all host interruptions; accepted
code is unchanged. Nested finally ensures close even if rollback is interrupted.
Third-party connection establishment still has its driver's bounded connection
timeout and driver-owned cancellation limitations; no universal I/O deadline is
claimed. There is no pool, environment DSN fallback, schema initialization or
legacy SQLite/forecast table fallback.

RLS/GUC settings restrict rows for the selected host scope; they do **not**
authenticate that scope. Shared database credentials, arbitrary SQL and GUC
selection must never reach untrusted callers. The original reader role has
SELECT-only grants, no CREATE/TEMP or bypass-RLS, and READ ONLY transactions.
The existing disposable-only provider is not a production connection provider.

## Exact request, output and lifetime

An `ExactForecastRead` binds the scope, exact idempotency identity and a mandatory
host-approved expected content digest. That canonical batch digest binds the
candidate/family, keys, origin/targets, cutoff/publication and immutable lineage,
original ordering/row count, dtype and exact float32 bits. The host must approve
that intended identity independently; caller-selected digests are not authority.
Requests cannot select credentials, repositories, issuers or alternate backends.

`service.read(original_grant, original_selection, cancelled=...)` returns:

- `VERIFIED_RECORD_SET`: exact revalidated `StoredForecastBatch` and read DTO.
- `DENIED`: invalid/substituted/foreign/expired/revoked/cancelled authority. No
  tenant/dataset existence diagnostic. Preauthorization denial opens no DB.
- `NOT_FOUND`: no visible exact batch in this authorized snapshot. No retry,
  rollback or UNKNOWN-write conclusion follows from absence.
- `UNAVAILABLE`: database/schema/content/verification failure, sanitized without
  raw exceptions or partial records. No fallback or partial batch release.

Principal and grant lifetime are independently bounded 1..60 seconds; grant expiry
is capped by its principal. Wall and elapsed clocks are both monotonic-checked;
equality at either deadline rejects. Revocation of the principal revokes access
through its grants; revocations are permanent for those original handles.
Verification is repeatable before expiry; read grants are not write reservations.
The host bounds issuance/retention; expired records remain process-local until
their host composition is discarded. Operational retention/quotas are deferred.

Cancellation, elapsed and wall callbacks complete before final private record,
original principal, grant and owner checks, both before lookup and before any
outcome returns. Lookup uses the retained immutable request rather than a mutable
caller alias. KeyboardInterrupt/SystemExit propagate; the reader closes its
connection. No callback is invoked after the final live check. This is sequential
validation, not an atomic revocation lease: concurrent revocation after that check
cannot retract already returned bytes. A caller-supplied blocking callback is a
host/application cooperation limitation, not a preemptible execution deadline.

Full batch scope is required. No listing, current-pointer browsing, pagination,
discovery, corrections, partial-series queries or scope expansion exists. Original
revision=1 / expected-current=0 pointers are checked by the accepted verifier.

## Numerical presentation and provenance limits

The authoritative values remain original four-byte little-endian `float32_le`
encodings and row canonical digests. `display_values` only unpacks IEEE-754
binary32 to Python float, preserving original bytes and order without rounding,
clamping, sorting or aggregation. It preserves negative zero, finite subnormals
and max finite values; malformed/nonfinite/unsupported dtype evidence rejects.

`2d56f943` -> `498.6732482910156`; `e9542244` -> `649.3267211914062`.
Display widening does not change canonical evidence. Storage contracts do not
depend on source adapters, native model classes, preprocessing or MLflow selection.
Reading family strings is not new model readiness or execution permission.

Stored receipts explicitly remain STORAGE-only, not producer approval or write
authority. This service verifies storage integrity; it does not reconstruct B4B
admission, authorize execution/writes or reconcile/restore consumed attempts.
Missing data never authorizes an UNKNOWN write retry. Operational claims of
historical authorization require independently governed durable audit linkage.

## Tests and next gate

Contract tests use mocked storage and synthetic host context, explicitly separate
from genuine PostgreSQL role/RLS/transaction/bytea tests. The separate opt-in
`tests/postgres/run_forecast_read.py` uses the UNCHANGED accepted PostgreSQL
harness, exact images/wheel hash, bounded network-none components, read-only
source/socket mounts, non-root clients and invocation-owned cleanup. Writer/admin
credentials are confined to synthetic test setup/fault injection; reader services
receive only reader credentials. Test admin corruption disables triggers only in
its own transaction/session on unique synthetic fixtures, never production.
All connection and permission claims require real PostgreSQL tests, not mocks.

Accepted cleanup warnings remain unchanged: a failed CLI/daemon operation can
remain CLEANUP_UNCONFIRMED even when no resource is currently observed. Never
turn it into cleanup success or assert universal daemon quiescence. Evidence
destinations are fresh and create-exclusive; historical timeout-probe.json remains
HISTORICAL EVIDENCE LOST.

B5-2 requires separate authorization and review for a pure stored-forecast accuracy
adapter, authorized actual-label revisions/availability, exact tenant/series,
measurement/unit/calendar matching and explicit zero-denominator policy. It must
not re-run a forecast, call a producer rollout to read stored values, write legacy
accuracy, consume a forecast write grant or activate a job. Any accuracy
persistence and operational authentication/credential/provider binding are later
independent gates.
