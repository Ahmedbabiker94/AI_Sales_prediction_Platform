# R7-05B3: controlled-offline PostgreSQL evidence foundation

This is an internal database foundation, not an authorized forecast producer or
production persistence path. Tests store synthetic canonical **content**. They
do not establish original M6 ownership, successful B3 origin, a live B1 receipt,
or a consumed B2 write grant. No such service is invoked. R7-05A remains DECLARED
only. Accepted native execution, P0, producer and write-intent implementations
are unchanged. No application consumer imports this foundation.

## Boundaries and contract

`forecast_evidence_storage.py` contains frozen, bounded, versioned data contracts.
They depend on accepted measurement/calendar declarations and canonical JSON,
not model classes, M3 kernels, native loaders, source adapters or MLflow. This
permits future source adapters and model families without storage changes.

The logical key preserves the exact accepted
`canonical_candidate_h1_forecast_key_v1` JSON-array preimage: tenant, dataset,
series, candidate/family, origin, exact H1 start/end/step, metric, measurement,
temporal semantics and identity-target transformation. H1 uses the accepted
daily/weekly local-calendar geometry, including daylight-saving transitions.
Publication/cutoff and artifact/input/snapshot/owner/execution references are
immutable lineage, not extra key partitions. These references and their digests
are declarations, not authentication or producer attestation.

One bounded batch contains one row for every series in its complete sorted scope
(1–32 series). The original declared row order is retained independently of the
sorted scope. Each row stores **four exact IEEE-754 binary32 bytes**, represented
as lowercase little-endian hex in JSON and `bytea` in PostgreSQL. No numeric SQL
float conversion occurs; signed zero and subnormal bits survive. NaN/Infinity,
missing values, duplicate keys, inconsistent lineage and revisions other than
one are rejected. Canonical row and batch SHA-256 hashes bind the exact UTF-8
encoding. Closed/duplicate-free JSON, scope and content are revalidated on ingress
and readback. There is no implicit dtype conversion.

## Explicit migration and schema

`src/database/migrations/001_canonical_forecast_evidence.sql` is the explicit,
checksum-pinned, transactional migration. No import, constructor or reader runs
it. `apply_forecast_evidence_migration()` uses an advisory transaction lock and a
version/checksum record; an existing wrong/unknown version rejects. A failed
migration rolls back. No Alembic configuration existed to extend. Neither
`metadata.create_all()` nor the global application engine is used.

The separate `canonical_forecast` schema has four tables:

| Table | Durable meaning |
|---|---|
| `schema_migrations` | Explicit version and migration checksum. |
| `batches` | Content-bound idempotency ledger and transactional storage receipt. |
| `evidence` | Immutable logical key/revision, ordered canonical row, SHA-256 and exact bytes. |
| `current_records` | Initial pointer, unique per logical key, FK-bound to exact evidence. |

Primary/unique keys enforce tenant+dataset+idempotency, logical key+revision, row
ordinal and initial pointer uniqueness. Foreign keys bind every pointer to its
row and batch. SQL SHA checks, finite-byte checks, JSON/column consistency checks
and deferred constraint triggers reject incomplete or inconsistent batches at
COMMIT. UPDATE/DELETE/TRUNCATE triggers reject mutation, even normal SQL from the
test database admin. A database superuser/schema owner can change DDL; protection
against a hostile administrator is not claimed.

## Transactions, replay and conflicts

The private provider delegates to the private repository; SQL stays in repository
or database infrastructure. There are no routes, jobs, registries or automatic
startup connections. Only a trusted host holding the dedicated test credentials
may construct these objects. Python naming conventions are not an IAM sandbox.

`_append_initial()` validates the entire scope/content, opens READ COMMITTED,
inserts the batch ledger, every row and every initial pointer, verifies readback,
and commits once. No receipt returns before successful commit acknowledgement.
The only `ON CONFLICT DO NOTHING` is on the idempotency ledger; it never replaces
evidence. An exact replay must match **canonical bytes and digest**, all rows and
pointers before returning the original receipt. A different content under the
same idempotency identity rejects. A different identity targeting an existing
logical key rejects, even with equal values. Concurrent writers are serialized
by PostgreSQL uniqueness; a losing conflicting batch rolls back entirely.

Expected-current CAS is **ABSENT/version 0 → initial revision 1 only**. This
release offers no correction, supersession, overwrite or arbitrary promotion.
The pointer and immutable-row checks enforce that restriction independently of
application assertions. Future CAS against a nonzero version requires another
explicitly reviewed design and correction authority.

There are no automatic transaction retries. SQLSTATE 40001/40P01 denotes a known
aborted serialization/deadlock attempt: only the entire identical batch and same
idempotency identity may be retried. A lost commit acknowledgement raises
`AmbiguousForecastCommit`, never a fabricated success. Use a new read-only
exact-content lookup first; if absent, an identical retry remains protected by
the durable idempotency/uniqueness constraints. Other failures roll back and close.
Receipt `transaction_timestamp` is PostgreSQL transaction-start time, not a claim
of exact commit time. A storage receipt asserts neither producer verification,
write-authority verification nor production permission.

## Genuine read-only verification and test-only connection guard

`OfflineForecastPostgres` only accepts a fixed private Unix-socket directory and
`r7_05b3_<run UUID>` database whose actual database comment matches that run. It
checks PostgreSQL 16, role identity and restricted privileges; it does not accept
URLs, TCP hosts or production credentials. Nonempty libpq `PG*` environment
settings reject before connection. `DATABASE_URL` is never read. Driver loading
is explicit/lazy, pinned to psycopg2-binary 2.9.12.

The reader uses a distinct SELECT-only, non-superuser/no-BYPASSRLS role and a
REPEATABLE READ, READ ONLY transaction. It has no database CREATE/TEMP, schema
CREATE or table/column DML privileges. Reads perform SELECT only and rollback/
close; missing databases or schema fail without initialization. This guarantee
concerns database operations, not PostgreSQL's internal statistics or caches.

Forced RLS adds tenant/dataset/series filtering using explicitly set session
scope, alongside parameterized scoped queries and exact host-pinned batch scope.
It prevents unscoped queries within that configured connection. A host holding
credentials can change session GUCs; this is not durable tenant authentication
against a hostile credential holder. Migration credentials are separate and are
never given to the provider.

## Disposable PostgreSQL reproduction

The separate opt-in `tests/postgres/run_postgres.py` never modifies the B3 native
worker. It accepts no test selector, connection URL or image override. It pins:

- PostgreSQL 16.15 official image config
  `sha256:6a35283e53fbe883da6a6ffbce8d3999dc66076cc5c4e2f1d21bdbd9e26bf86f`
  (repo digest `postgres@sha256:e8a07d2c7579b038938cdba6c539e580e5001caf9ea203d397b5cc83e76bd938`).
- Separate client base `sha256:417063b71e35c7a9889c10ac2516c328f16a483e065ec858df101eb9728c2df2`;
  the exact psycopg2-binary 2.9.12 CP311 wheel is hash-pinned in
  `tests/postgres/requirements.lock`. No package is installed on the host.

The client builds offline from the supplied, hash-verified wheel. The previously
validated base's supply-chain limitations remain; this is not a reproducible
historical native image claim. There is no native workload or native permission
in these tests. Operational imports are trapped.

```bash
python tests/postgres/run_postgres.py --allow-disposable-postgres \
  --wheelhouse /path/to/verified/wheels --evidence /path/to/new/evidence-directory
```

Both containers use network-none, read-only roots, non-root users, dropped
capabilities, no-new-privileges and CPU/memory/PID bounds. Only new named data and
tmpfs Unix-socket volumes are shared; no port is exposed. The client has read-only
source/test/socket mounts, tmpfs scratch, 1 CPU/1 GiB/64 PIDs, a CPU limit and a
240-second wall timeout. PostgreSQL has 1 CPU/512 MiB/64 PIDs, fsync/synchronous
commit/full-page writes enabled. A short provisioning container has only CHOWN
to initialize its **new** data volume. No home, production, credentials or Docker
socket mounts exist. All database roles/passwords are explicit random test values.
The Docker host and reviewed test files are trusted. Timeout/interruption/failure
cleanup removes only this run's unique containers/volumes.

The harness verifies final-server readiness (not initdb's temporary server), runs
the complete foundation module, restarts the same data-backed server, runs a new
client's durable lookup/replay check, then verifies cleanup. Tests exercise real
PostgreSQL transactions, independent SQL constraints, concurrency and roles;
synthetic fixtures establish no successful native origin or write authority.

## R7-05B4 prerequisites and remaining limits

B4 must pin the ORIGINAL live producer instance and receipt, verify retained
successful B3 result/owner/envelopes, independently consume the ORIGINAL scoped
write grant, and project exact rows/lineage into this storage contract. That
bridge must define reservation versus commit failure/retry/ambiguous-ack behavior,
expiry/revocation ordering and the point after which revocation cannot undo a
durable transaction. No atomic authorization lease is provided here. Serialized
receipts/digests alone must never authorize a database write.

Durable authority, authenticated tenant roles, production database provisioning,
backup/restore/crash-power-loss verification, production credentials, corrections
and consumer migration remain outside scope. The restart test proves committed
evidence survives a clean server restart; it is not a power-loss durability test.
Legacy forecast/accuracy tables and their consumers remain unchanged. There is
no claim of production readiness or Phase 7 closure.
