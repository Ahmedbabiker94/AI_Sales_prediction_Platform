# R7-05B4B controlled-offline authorized atomic persistence

This is an internal synthetic validation composition, not an operational writer,
public endpoint, production resolver or consumer activation. No correction is
supported. The native worker, original execution and producer/write-intent
services, P0 image guard, database schema, migration, repository and provider are
unchanged. Two test-host launchers have small optional additive hooks; their
existing callers/defaults remain unchanged.

## Original admission and independent host permission

`ProtectedHostAdmissions.capture()` invokes the original fixed host broker itself.
There is no result, channel, image, command, source or session input parameter.
The broker's optional protected retention hook executes immediately after its
original `_HostAdmission.admit()`, before ACK. It receives the original local
session and channel and the actual verified FINAL_RELEASE. The retention service
validates the original broker class, pending invocation ticket, original channel,
complete approved proposal, release/challenge/approval/grant references and scope.
It retains original object references and immutable canonical strings. A usable
opaque handle is installed only after the original broker returns successfully
and confirms worker exit and cleanup. A failed ACK, crash or unconfirmed cleanup
cannot publish a handle. Detached VALIDATED_NO_WRITE dictionaries and logs cannot
be supplied to this interface or reconstructed into authority.

The record retains the original host session/context (including exact container,
source, challenge), channel identity, canonical release/proposal/projection and
bounded lifetime. The worker's B2 grant is a delegation reference in this record,
not a reconstructed Python grant handle. The original worker-local owner,
producer, envelopes and consumed grant were live-verified at FINAL_RELEASE.
After that cutoff, the host cannot query exited-worker Python objects. It relies
on authenticated original invocation/channel and exact delegated content.

The host independently constructs `AuthorizedForecastPersistence`, pinning the
original admissions service, separate operator and storage port. `authorize()`
requires that operator and the original issued admission handle. Admission alone
never calls storage. A permanent claim is recorded at the original admissions
service, so creating a second coordinator cannot authorize the same admission.
Each opaque commit grant is exact-original-identity checked. Copies, dictionaries,
foreign issuers and arbitrary prediction/projection arguments are rejected.

Construction, source, Docker daemon, approval policy, clocks, storage port and
private Python state belong to the protected host. This is not a Python sandbox:
a hostile host that coherently rewrites private state or substitutes functions is
outside the boundary. No public API exposes these services, records or credentials.
Public `verify()` returns freshly parsed frozen content, never the private record.
Digests are integrity checks, not cryptographic authentication or durable IAM.

## States and linearization

- ADMITTED_NO_WRITE: original broker admission retained; no storage permission.
- COMMIT_AUTHORIZED: separate host operator authorizes exact original projection;
  original admission permanently claimed; lifetime at most 60 seconds and no later
  than admission/release/session expiry.
- COMMIT_ATTEMPT_RESERVED: final supported cancellation/wall/elapsed callbacks
  have completed, original admission/claim/content and revocation are live checked.
  The coordinator and admission locks cover the final local live check and
  transition, with consistent lock ordering and no callbacks under those locks.
  This is the local authorization linearization point; clock validity is based
  on the final supported clock observation, not an atomic distributed time lease.
- TRANSACTION_IN_PROGRESS: exactly one call to the host-pinned storage port.
- COMMIT_CONFIRMED: accepted provider returns a fully validated durable receipt
  matching the original approved batch, or exact READ ONLY reconciliation proves it.
- COMMIT_REJECTED: accepted storage conflict/rejection or known rolled-back
  serialization failure. The original attempt remains spent, with no retry.
- COMMIT_OUTCOME_UNKNOWN: transport/unknown failure after reservation, malformed
  response, or interruption. The original attempt remains spent. No missing
  receipt, expiry, restart, cancellation or lost ACK restores permission.

Before reservation, rejected authorization opens no database connection. All
callbacks precede final live validation; no callbacks execute between the local
cutoff and storage invocation. Revocation after that cutoff cannot undo a running
transaction or retroactively roll back a completed PostgreSQL COMMIT. There is no
distributed transaction with Python authority and no atomic cross-process
revocation lease. PostgreSQL's `connection.commit()` is the durable commit point.
A crash after local reservation but before the call may produce no rows, but the
process-local attempt still cannot be reused. Loss of all host memory cannot
restore authority from a serialized record.

`reconcile()` is a separately invoked trusted operation on a retained original
attempt; it uses only exact idempotency/content READ ONLY lookup. No receipt means
UNKNOWN, not rejection or permission to retry. A restarted host/client may use
the existing separately trusted SELECT-only repository lookup with exact original
content for observation; such content never recreates admission or write grants.
This release provides no durable authority recovery, automatic retry, correction,
supersession or new current-pointer policy.

## Exact projection and SQL boundary

The host uses the original approved `CanonicalForecastStorageBatch.canonical_json`
from B4A1 projection. Revalidating bytes does not transform/reorder/recalculate
values. Candidate/family, complete tenant/dataset/series scope, logical H1 keys,
publication/cutoff/targets, frozen/native/input/snapshot/owner lineage, ordinal,
finite float32 little-endian bytes and host idempotency stay content-bound.
Storage remains model/source-adapter independent. SQL stays in accepted database/
repository infrastructure and explicit test infrastructure. The coordinator has
no SQL, native loader, feature processing or external source adapter.

Accepted B3 PostgreSQL enforces immutable initial-only evidence/current records,
unique logical keys and revisions, atomic multi-series transactions, durable
content-bound idempotency and read-after-write receipt verification. A content-only
repository primitive is not a public write API; only the protected host possesses
credentials/ports. The new coordinator never retries this primitive, even though
the underlying foundation can recognize an exact durable replay.

## Test topology and invocation

Three separate disposable components are used:

1. Original fixed native worker, exact recovered image/manifest/source validation,
   network-none, UID/GID 1000, read-only source/root, cap-drop ALL, no-new-privileges,
   1 CPU, 1 GiB, 64 PIDs, bounded time, no PostgreSQL/Docker socket or credentials.
2. Accepted isolated PostgreSQL 16.15 server, unique disposable database/cluster,
   named owned data/socket volumes, no TCP listening or published ports.
3. Fixed database client from the accepted pinned driver build. It has only test
   credentials, network-none and a read-only mount of the dedicated Unix socket.
   It has no Docker socket, host home, production data or native execution path.
   Lookup/proof clients receive reader credentials only (writer/admin/migrator
   secrets blank); the reader role and READ ONLY transactions are checked by the
   accepted connection infrastructure.

The trusted outer host launches stages through the original broker and accepted
PostgreSQL harness. The fixed database client calls the original provider and
repository. Its stdio request is content on a protected host-created test channel,
not independent producer authority. It is never exposed as an endpoint. The test
host's pinned storage port cannot be selected or replaced by commit callers.

```
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python \
  tests/persistence/run_authorized_persistence.py \
  --authorized-native-postgres --wheelhouse <verified-wheel-directory> \
  --evidence <new-exclusive-evidence-directory>
```

The accepted PostgreSQL harness runs 69 foundation cases plus the restart case,
then its optional host stage. Genuine native admissions exercise commit, partial
row rollback, real commit with injected lost acknowledgement, UNKNOWN/absent
receipt, and host revocation. A new client/read-only connection verifies receipts
and raw PostgreSQL bytea in ordinal order. Server restart verification uses new
clients without recreating write authority. Repository tests independently cover
concurrent first-writer conflicts, content-bound idempotency, RLS tenant isolation,
CAS mismatch and immutable-row constraints. Those database fixtures are synthetic
content, distinct from genuinely authorized native admissions.

Expected pinned CPU fixture outputs are
`[498.6732482910156,649.3267211914062,498.6732482910156,649.3267211914062]`,
with bytea hex `2d56f943 e9542244 2d56f943 e9542244`. Native export/reference
parity is measured in the unchanged B3 fixture, not inferred from printed values.
No fixed native artifact digest is expected across new publications.

Cleanup remains bounded and ownership-labelled; pending Docker daemon creation
is tracked separately from terminated CLI processes. CLEANUP_UNCONFIRMED stays
an operational warning, never success. No universal daemon quiescence guarantee
is claimed. Evidence is create-exclusive in new directories. Historical
`cleanup-probes-verified/timeout-probe.json` remains HISTORICAL EVIDENCE LOST.

## R7-05B5 prerequisites

Independent acceptance and separately authorized integration of this candidate;
decisions on controlled read consumers, durable authorization/audit/recovery and
operational credential policy; explicit correction/revision authority if needed;
production schema/rollout review and later consumer migration. None are enabled
here. There is no API, Celery/scheduler/dashboard/report/accuracy activation,
registry promotion, production synchronization or deployment.
