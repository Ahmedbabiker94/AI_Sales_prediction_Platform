# R7-05B4A1: protected synthetic native handoff

This additive, controlled-offline experiment ends at `VALIDATED_NO_WRITE`.
It opens no PostgreSQL connection, commits nothing, grants no persistence
permission and returns no durable receipt. No API, job, consumer, registry or
production path imports it. Accepted B1/B2/B3, producer, write-intent, PostgreSQL
foundation and P0 source files are unchanged.

## Protected composition and trust

`tests/native/run_native_handoff.py` is a separate host-only Python entrypoint,
with explicit `allow_native_handoff=True`, `use_recovered_image=True` and a
host-controlled full-content approval callback. No command-line module, image,
source-path or worker override exists. The original P0 pytest launcher is not
an authority-producing transport and remains unchanged.

The host calls the original P0 recovery validator before creation and again
before start. It requires recovered image
`sha256:417063b71e35c7a9889c10ac2516c328f16a483e065ec858df101eb9728c2df2`
and fresh manifest SHA256
`d199a85c61ed01e3ac8da447ae32a16cd606d2e029b5c42d76aa0d8d0cb0b3f5`.
All eight evidence proofs and accepted Dockerfile/lock identities are checked by
unchanged P0. The historical B3 image guard remains unchanged; this image never
impersonates it. External evidence is required at use time and is not portable,
durable or signed merely because this code is committed.

The host inventories regular files under the mounted src/tests roots, rejecting
symlinks/special files and cached bytecode, binds that content digest to the exact created Docker
container ID and fixed argv, verifies the Docker inspect isolation and command,
and rechecks source before start and admission. The worker recomputes mounted
source identity. This pins host-selected source for a session; it is NOT a
signature, sandbox against Python reflection, or defense against a malicious
host changing mounts/source/functions/private records. Host code, approval
callback, clocks, private session state, Docker daemon and local attach pipes are
trusted. An untrusted caller cannot supply a channel or substitute a service
through `run()`. A party controlling that trusted host can run arbitrary Docker
commands outside this API; this code does not claim to prevent that.

Invocation authentication comes from that original host-created container and
its original `docker start --attach --interactive` process/pipes. Not from a
nonce, digest, saved frame, public receipt, test output or worker exit status.
No remote/hardware/cryptographic attestation is claimed.

## Worker ownership map and additive B2 seam

The fixed `tests/native/handoff_worker.py` entrypoint reuses the accepted genuine
synthetic fixture, not an arbitrary pytest selector. It builds a genuine original
M6 native candidate, original B1 artifact and B2 snapshot/input envelopes,
original B3 execution authority/service, original successful-producer instance
and receipt, and an independent write-intent issuer/operator. All remain in that
same process. The original B3 service itself performs H1 inference; no detached
success or second prediction implementation is supplied to the exporter.

`src/handoff/worker_export.py` pins these ORIGINAL identities. Its proposal is
constructed only after ORIGINAL successful-producer verification. Host approval
must match the complete canonical proposal. The exporter invokes ORIGINAL B2
`issue()` and `simulate_consume()` itself and retains the original successful
consumption return, grant record and reservation. It never accepts an externally
provided consumption return. A failed consumed attempt proves no successful
release. Public `verify()` and `simulate_consume()` semantics are unchanged.

Before final release all cancellation/elapsed/wall callbacks and the original
producer verification complete. Then the original callback-free B2 `_require`
seam revalidates the retained consumed record, original producer and owner.
Original private record identity and reservation must still match. Exporter
state permits at most one final release. Private-state/function replacement
belongs to the trusted-host boundary, not an adversarial Python sandbox claim.

## Content projection and independent host approval

`src/handoff/content.py` performs a pure generic projection from validated B2
records to existing B3 storage contracts. No repository/provider, SQL, native
loader or source adapter is invoked. All target semantics, complete series,
logical keys, original order, H1 geometry and exact little-endian float32 bytes
are revalidated. The host reconstructs this projection from the full records and
compares exact canonical bytes, not worker-supplied digest strings alone.

Lineage has compact producer/record/request references once per row rather than
a full nested producer JSON per row. `owner_reference` is the candidate reference
verified against the retained original owner in the worker, NOT a serialized
Python object or independent ownership proof. Artifact/input/snapshot,
representation/frozen state, publication/cutoff and original execution/receipt
are bound in the original record set and projected immutable lineage.

The host must explicitly approve the FULL validated new proposal with its
independent intended scope, row values and lineage policy. An always-True
callback is inappropriate for untrusted requests. The genuine integration test
has a separately written host policy for organization 2/dataset 102, original
four-series weekly fixture, exact target semantics/origin/cutoff, expected
float32 values and full internal lineage consistency. Fresh publication and
candidate IDs are approved as newly produced fixture identities authenticated by
the fixed worker, not looked up in a durable production ownership registry.
The host issues a fresh content-bound idempotency identity; this has no durable
idempotency effect yet. Family-specific fitting exists only in that fixed
synthetic test composition. Framing, projection and export are model/source
agnostic within accepted contracts; no new family is made executable.

## Framing, ordering and budgets

No TTY. Docker interactive stdin/stdout only; worker duplicates its protocol
output FD then redirects fd 1 to fd 2 BEFORE native imports, including C/native
library diagnostic writes. stderr is diagnostics and never protocol authority.

Each message is a four-byte big-endian length followed by strict UTF8 JSON.
Duplicate keys/nonfinite JSON constants, unknown fields/types, purpose/version,
direction, sequence or session/container/source/challenge mismatch reject.
Per-frame bound: 1152 KiB; cumulative per-direction bound: 4 MiB; diagnostics:
256 KiB. Underlying records and storage batch retain their accepted 512 KiB
bounds. Proposal encoding budgets are checked BEFORE grant consumption.
Partial reads/writes and stdout/stderr are multiplexed under the same monotonic
wall budget. Truncation, diagnostic flood, backpressure, cancellation and worker
crash fail closed. No shell interpolation or dynamic import/command selection.

```
SESSION_CREATED -> WORKER_VERIFIED -> PROPOSAL_RECEIVED -> HOST_APPROVED
 -> GRANT_RESERVED -> FINAL_RELEASE -> HOST_ADMITTED_NO_WRITE
```

Messages are HELLO(0 host), PROPOSAL(1 worker), APPROVAL(2 host), RESERVED(3
worker), CHALLENGE(4 host), FINAL_RELEASE(5 worker), ACK(6 host). The initial
challenge binds the session; a fresh final challenge binds exact approved
proposal, approval, reservation and grant to the release. There is no retry or
second admission transition. Host admission requires the original channel
object, exact locally approved bytes and identities, local live session,
cancellation and deadlines; serialized frames cannot create that session.

## Cutoff, expiry and UNKNOWN

FINAL_RELEASE is the explicit irreversible worker-side delegation cutoff after
successful original consumption and final live validation, BEFORE sending the
precomputed frame. Failure after reservation permanently burns that grant.
Original receipt/grant lifetimes remain 1–60 seconds. The total worker/protocol
budget is at most 240 seconds, native CPU180 seconds; channel waiting cannot
extend a producer/grant deadline. Host admission also checks original producer,
release and host deadlines and monotonic elapsed budget. Clock rollback rejects.

Synchronous callback revocation before the cutoff prevents release. Revocation
AFTER release cannot retract already delegated bytes atomically across
processes. The host may cancel before local admission, but no cross-process
atomic revocation lease, distributed transaction or durable recovery is claimed.
Native success or approval does not itself confer a write permission.

Known pre-approval failure is REJECTED. Once approval may have reached the worker,
reservation may have occurred even if its acknowledgement was lost. Crash/loss
after approval, reservation or admission
is UNKNOWN at the host API, not proof of a completed write or permission to
retry. Worker crash before release produces no admissible final message. Late
release, duplicate frame/admission, replayed stdout and foreign session reject.
Lost ACK can leave worker unsure whether host admitted; this experiment has no
replay/status endpoint. A host crash loses its process-local original session.
Logs/frames cannot recreate consumed authority. No new grant is inferred from
absence of an acknowledgement. B4B must retain equivalent UNKNOWN semantics at
the actual PostgreSQL commit boundary and use SELECT-only exact-content receipt
reconciliation without authorizing a second attempt.

## Isolation and cleanup

Unchanged P0 restrictions: network-none, read-only root, src/tests readonly
mounts only, UID/GID1000, drop ALL capabilities, no-new-privileges, one CPU,
1GiB RAM/swap ceiling, 64 PIDs, CPU180 seconds, tmpfs/tmp256MiB, no home,
credentials, database/socket or Docker-socket mount. Inspect validates fixed
argv, full container identity, stdin/no-TTY and absence of image-declared volumes.
The native worker never receives database credentials or network access.

Invocation names and labels exist BEFORE create. A pending creation/start is
separate from owned CLI process-group termination/reaping. Bounded exact-name
and label reconciliation removes only owned containers. There are no volumes.
An ambiguous daemon operation remains CLEANUP_UNCONFIRMED even if currently no
resource is visible. No blanket prune or universal daemon-quiescence claim.
Normal known-complete start/removal can report CLEANUP_SUCCESS. Interruption
cleanup never restores consumed authority. Evidence must use fresh exclusive,
symlink-rejecting paths outside historical directories.

## Validation and next milestone

Contract tests explicitly use stub B3 outcomes and cannot prove genuine origin.
The separately authorized host integration invokes the fixed isolated worker:

```
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python tests/native/test_native_handoff_integration.py --authorized-native-handoff
```

Expected measured native outputs are 498.6732482910156, 649.3267211914062,
498.6732482910156, 649.3267211914062; bytes `2d56f943 e9542244 2d56f943 e9542244`.
The accepted fixture also independently reloads its B1 model for parity. This is
pinned CPU fixture evidence, not universal bitwise stability.

R7-05B4B still needs separately reviewed trusted host admission-to-provider
composition, exact storage content verification, a single authorized database
attempt, explicit transaction linearization and bounded revocation semantics,
real PostgreSQL atomic/rollback/concurrency tests, ambiguous commit
acknowledgement and read-only exact-content recovery, restart verification,
independent native/DB stage isolation and lifecycle cleanup. No production
readiness, durability, consumer activation or Phase 7 closure is claimed here.
Historical overwritten `cleanup-probes-verified/timeout-probe.json` remains LOST;
new evidence does not reconstruct the old file or lost 38-worktree workspace.

The current host API deliberately returns detached no-write DATA only. B4B must
retain the ORIGINAL private host-admission record/capability at its protected
commit boundary; it must not reconstruct authority from this returned dictionary,
a saved frame, a log, or a rerun of the consumed worker grant.
