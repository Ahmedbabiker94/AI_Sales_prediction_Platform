# R7-05B2 — independent forecast write intent and B3-backed proposed records

This milestone independently authorizes a proposed controlled-offline H1
evidence write. It performs no database operation. `simulate_consume()` reserves
and consumes a process-local attempt, validates the original successful producer
again, and returns `CONSUMED_NO_WRITE`. That is not a committed forecast, durable
receipt, successful transaction or proof that a logical key is absent in storage.

## Trusted composition and original successful origin

The trusted host constructs `ForecastWriteIntentAuthority` with the ORIGINAL
`SuccessfulB3ProducerService` and a separate private operator object. The issuer
pins that producer instance. No per-call producer/verifier/issuer injection is
accepted. A copied producer or authority fails its original-self identity check.
An operator string and a producer receipt's public values establish no authority.
Another authority instance's grants are not interchangeable at the pinned issuer.
Future consumers must pin this issuer too; accepting a caller-selected issuer
would destroy this boundary.

`issue()` requires the original producer handle, exact original M6 candidate
reference, original native B1 and executable-input B2 envelopes, and exact B3
request. It calls the captured ORIGINAL producer class verifier, ignoring an
instance method alias. It privately retains the original producer issuance
record, which retains the actual successful `ExecutionOutcome`, exact returned
result and consumed execution grant. It never accepts a detached result or rows,
rebuilds successful origin from hashes, calls B3, or adds another native path.
A failed-but-consumed B3 grant cannot produce an accepted producer handle.

The new dependency chain is write-intent service -> original successful producer
-> original B3 owned-execution provider -> original M6/B1/B2 retained evidence.
No new repository, SQL, registry, model store or database provider is needed for
this non-writing milestone. Accepted Repository -> Provider -> Service paths and
all existing implementations remain unchanged. Verification reads retained
evidence; it does not capture source data again or claim present source freshness.

Host control of Python source, constructor calls, private registries, operator
objects, original instances, clocks and the Docker daemon is essential. Private
attributes are not an authentication sandbox. A caller able to coherently rewrite
ALL protected producer records and their pinned encodings is outside this threat
model; this code does not claim to defeat that attack. Mid-operation replacement,
identity substitution and inconsistent mutation are checked, but are not a claim
of durable IAM, cryptographic attestation or cross-process receipt portability.

## Record schema, bytes and identities

`B3ForecastRecordSet` version 1 contains the verified producer declaration, which
embeds the complete B3 request/result: candidate/family, native artifact/digest,
original representation/frozen M3 state, input and authorized canonical snapshot,
dataset/tenant/complete series, original publication/cutoff/origin and H1 geometry.
It additionally records an explicit host-issued idempotency ID, exact original
sample ordering, shape, finite float32 rows, logical forecast IDs and a versioned
row-set digest. It is classified `B3_BACKED_PROPOSED_H1_RECORDS_ONLY`.

Every row keeps the original M2 point, including target interval, dimensions,
metric, unit/currency, series and H1 step. The value is exactly four IEEE-754
binary32 bytes in LITTLE-ENDIAN hexadecimal (`float32_le`). The numeric property
decodes those bytes with `struct.unpack('<f', ...)`; it is never guessed from
printed hexadecimal or converted from a caller's arbitrary numeric value.
Signed zero is preserved. NaN, infinity, wrong lengths and other encodings reject.
Rows must match the original producer rows one-for-one; no sorting, type coercion,
feature synthesis or silent scope broadening occurs here.

The logical key reuses R7-05A's `canonical_candidate_h1_forecast_key_v1` encoding
exactly: tenant, dataset, series, candidate/family, original forecast origin,
exact H1 target interval/step, target metric, measurement and temporal semantics,
and `identity_target_v1`. An executable test compares the new key with the actual
accepted R7-05A property. Publication, cutoff, dataset version, snapshot, native
artifact, inputs and execution remain immutable lineage, NOT alternate logical
key partitions. Duplicate logical keys inside the complete batch reject.

The row-set digest binds the producer receipt ID, original producer result-set
digest and ordered rows. The record-set ID binds the full versioned canonical
record set, including the host idempotency ID. A write-grant ID additionally binds
issuer, ordinal, purpose, record/receipt IDs, lifetime and INITIAL_WRITE_ONLY.
These digests check integrity; public declarations and coherently recomputed
digests still establish no trusted origin or live permission. Protected records
retain immutable canonical strings, not mutable aliases to detached public views.

No native-payload abstraction or other family implementation is introduced.
Record construction uses accepted family/scope metadata; the current genuine
producer remains the accepted XGBoost-only controlled-offline B3 path.
M7 remains TEST_ONLY_NON_DEPLOYABLE / NO_MODEL_PAYLOAD. R7-03/04A and R7-05A receive
no permission upgrade. MLflow aliases and current.json play no role.

## Independent grant lifecycle and failure

Only the host's separate write operator can issue a grant, AFTER original live
producer verification. Neither B3 execution nor successful producer issuance
automatically grants write intent. The purpose is exclusively
`CONTROLLED_OFFLINE_H1_EVIDENCE_WRITE`, with `INITIAL_WRITE_ONLY` and one attempt.
The capability is an opaque nonserializable original handle, with no token string.
Public declaration/view objects, receipt handles, consumed execution grants,
foreign issuers, copies and reconstructed handles are rejected as capabilities.

Requested lifetime is 1–60 seconds, clipped to the producer receipt's expiry.
The write deadline is the earlier of explicit operation deadline and grant expiry.
Exact equality with a deadline/expiry rejects. A past successful producer fact
can outlive its completed B3 execution window, but cannot outlive its separately
bounded producer receipt or the write-intent interval. No execution is renewed.
Monotonic elapsed limits, nondecreasing wall time, encoded-size and registry-count
bounds fail closed. Records, including revoked/expired/consumed grants and their
idempotency bindings, are retained up to the bound without eviction or reset.

`verify()` repeatably inspects a live ISSUED grant. `simulate_consume()` checks the
original producer and owner before reservation, marks the attempt CONSUMED with
a private reservation, then rechecks them before return. Any failure AFTER that
reservation permanently loses the attempt, including cancellation, expiry,
revocation and exceptions. Pre-reservation rejection leaves an unconsumed grant
without native or database side effects. There is no retry restoration.
`revoke()` and `invalidate()` are permanent. Failures raise a fixed sanitized
`ForecastWriteIntentError` without admitted records or partial batch output.

Cancellation, wall and elapsed callbacks run outside record locks. The original
host cancellation source is retained in the grant and polled together with each
operation's cancellation source; a later caller cannot replace it with False.
Cancellation is cooperative operation interruption, not permanent revocation;
only `revoke()`/`invalidate()` permanently disable authority.
The original
producer verifier is invoked at entry and terminal boundaries with composed
interruption checks. After its final callbacks, the authority rereads its LIVE
grant and original producer issuance, consumed execution record and M6 owner via
the accepted callback-free producer evidence seam. No supported callback runs
after those final live checks before return. Tests discover actual final edges
and revoke authority during either layer's clocks. Reentrant operations reject.
These are sequential process-local checks, NOT atomic leases against concurrent
external revocation after validation. Resource/cancellation controls are
cooperative; they do not preempt arbitrary native code or slow host callbacks.

## Idempotency, conflicts and future PostgreSQL boundary

The trusted host explicitly supplies a bounded digest-shaped idempotency ID;
possession of that string grants nothing. This issuer remembers its exact record
content binding. Reusing the ID rejects, for BOTH identical and conflicting
content, even after consumption/revocation/expiry. This is process-local replay
rejection, not durable idempotent retry or evidence of a previous database write.
Different idempotency IDs do not prove distinct logical forecasts or grant any
right to overwrite a stored one.

Only INITIAL_WRITE_ONLY is representable. No correction, supersession, revision
transition, overwrite, current pointer or database CAS is implemented. The
service deliberately cannot check existing forecast keys without a database.
R7-05B3 must separately implement and accept:

- PostgreSQL repository/provider transactions, with no SQL in services and no
  implicit schema initialization in its genuinely read-only verification path.
- Durable unique logical forecast keys and immutable revisions, plus content-bound
  idempotency that distinguishes identical committed retry from conflicting
  value/lineage. A validated B2 simulation is not evidence of a committed retry.
- Atomic all-series initial insertion, concurrent-writer conflict detection,
  rollback/partial-failure recovery and verifiable audit provenance.
- Expected-current CAS and explicit independent correction/supersession authority
  before any future correction feature; neither exists in B2.
- Host-pinned ORIGINAL write issuer and original producer validation at the real
  transaction boundary; serialized declarations alone are insufficient. A
  cross-process handoff needs a separately approved design.

No API, scheduler, Celery, dashboard or legacy persistence consumer is activated.
Production serving, promotion, synchronization and deployment remain outside
this controlled-offline milestone. This does not close Phase 7.

## Verification and native environment

Contract/fault tests explicitly reuse the accepted mocked-B3 producer fixture and
real M3 input fixture. They do not prove native origin. The separate native module
uses the unchanged genuine B3 fixture: actual original M6-owned XGBoost 3.2.0 fit,
genuine B1 UBJSON export/reload, authorized B2 read-only capture and frozen M3,
original B3 one-shot execution authority, actual successful producer return,
then independently issued write intent and one-shot simulation.

Only the accepted P0 `tests/native/run_recovered_image.py` launcher may run native
tests, with explicit `--use-validated-recovered-image`. It verifies recovered image
`sha256:417063b71e35c7a9889c10ac2516c328f16a483e065ec858df101eb9728c2df2`
and manifest digest
`d199a85c61ed01e3ac8da447ae32a16cd606d2e029b5c42d76aa0d8d0cb0b3f5`.
The original B3 default image guard is unchanged. Network-none, read-only root
and source mounts, UID/GID 1000, dropped capabilities, no-new-privileges, one CPU,
1 GiB memory, 64 PIDs, 180 CPU seconds and a maximum 240-second wall timeout are
retained. Synthetic catalogs live only in private worker tmpfs; no application
or forecast database is accessed. Native payloads remain process-local.

Observed H1 values were `[498.6732482910156, 649.3267211914062,
498.6732482910156, 649.3267211914062]`, shape `(4,)`, physical float32, bytes
`[2d56f943, e9542244, 2d56f943, e9542244]`. Measured absolute and relative
differences against fitted and independently reloaded references were both zero.
This is a pinned CPU synthetic experiment, not a universal bitwise guarantee.
The 4,191-byte UBJSON digest varies with fresh synthetic publication identities.
Write-intent issuance/verification/consumption is exercised with database
connections, fitting, native loader and predictor entrypoints trapped.

Exact commands, distinct case counts, invocation outputs, numerical evidence,
container cleanup and current-workspace preservation inventories are recorded
outside the repository for technical acceptance. Historical lost 38-worktree
evidence is not claimed restored; current recovered evidence is not durable or
portable merely because the launcher is committed.

The final new-suite invocation was:

```text
python -B tests/native/run_recovered_image.py --use-validated-recovered-image -- tests/runtime/test_forecast_write_intent.py tests/native/test_forecast_write_intent_native.py -s
```

It passed all 136 cases in one invocation. Counts below are distinct committed
repository cases, excluding overlapping reruns and fixture calls:

| Category | Passing cases |
| --- | ---: |
| New mocked-B3 contract/fault cases | 124 |
| New genuine native-context cases | 12 |
| Accepted successful producer contract cases | 68 |
| Accepted successful producer native-context cases | 14 |
| Selected accepted native B1 and B3 cases | 66 |
| Complete accepted read-only B2 input module | 51 |
| Complete accepted P0 host unit module | 77 |
| Complete accepted R7-05A structural-only module | 54 |
| Real P0 isolation smokes and accepted worker regressions | 9 |
| Total | 475 |

Not every native-context negative case loads or predicts; its fixture has genuine
original-owner provenance. The 124 mocked-B3 cases make no native-origin claim.
No separate external probe count is added. All final invocations exited zero;
one preliminary CLI invocation exited 2 before creating a worker because pytest's
`-s` needed the launcher's `--` separator. It ran no tests and is excluded.
