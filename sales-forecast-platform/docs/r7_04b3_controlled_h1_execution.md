# R7-04B3 — controlled-offline canonical H1 native execution

This is a genuine synthetic XGBoost execution experiment, not production serving.
It is an additive boundary; accepted M2–M9, B1/B2, R7-03/04A deny-only services and
R7-05A structural-only persistence are unchanged. No API/job/bootstrap registration
is added. There is no forecast write, model store, registry or pointer operation.

## Accepted seams and compatibility

The original M6 trainer retains `(trainer, reference, metadata, fitted model,
frozen state)` in its private candidate record. B1 retains its original envelope
and that exact record in its issuance map. B2 retains its original data envelope,
original reference, record and immutable data encodings. OwnedExecutionProvider
requires all these original object identities and unchanged encodings, then
revalidates canonical manifests, payload digest/size, M3 state, M2 representation,
feature schema and B2 request/binding. Copies or digests alone are insufficient.
Private attributes are an explicit trusted-host seam, not a Python security sandbox.
No model object is exposed in public metadata.

B1's existing verifier accepts validation outputs only. B3 does not weaken or call
that verifier as a runtime executor. The test first performs its genuine B1
round-trip, then a separate B3 adapter reloads the same original B1-issued UBJSON
under B3 authority. B2's actual M3 output preserves ordered location/lag1/promotion
columns with int8/float32/int8 physical dtypes. The unchanged D2 strict_float32
kernel validates exact homogeneous ingress; no feature reordering or lossy cast is
allowed. The native fit, manifest and loader are pinned to XGBoost 3.2.0 CPU,
reg:squarederror, exact original feature names, native attributes and tree count.

Only B2's original publication, complete original series set and accepted post-fit
validation/test origins are supported. The genuine experiment uses its validation
origin and four overlapping store/department series. H1 means the accepted M2
convention: target_period equals forecast_origin and target_period_end is the
canonical next boundary. No new future origin, H2 recursion or external_forecast
policy is enabled. B2's unavailable external-forecast execution hook stays unavailable.

## Independent authority and consumption

Trusted bootstrap supplies a fresh opaque `object()` as the operator root when
constructing ControlledExecutionAuthority over the original provider. The root
must be retained only by the host, never populated from request actor strings.
The host explicitly calls issue(root, reference, native, inputs, request).
No capability is manufactured by the execution service. M9 approvals, inspection
grants, public grant declarations, M7 manifests and result digests do not authorize
execution. An actor string cannot be configured as the root.

A capability is an uncopyable/unserializable process-local handle. Its protected
registry record binds exact owner and envelope objects, candidate/family, B1
artifact ID/digest, full B2 binding, dataset/organization/series, publication,
origin/cutoff/H1 geometry, representation/frozen state, purpose and limits.
The public versioned grant declaration binds the same request plus issue time,
expiry, execution deadline and max_executions=1. It is evidence, not a bearer token.

Lifetime is at most 60 seconds. The execution deadline cannot exceed expiry;
elapsed execution is additionally bounded to at most 30 seconds. Exact boundary
`now >= deadline` or `elapsed >= limit` rejects. Wall time cannot move backwards
within an authority, including between failed and later attempts.

The first valid checkpoint atomically changes ISSUED to CONSUMED and obtains a
private operation reservation. Every subsequent checkpoint requires that exact
reservation; another service cannot replay it. Failure after reservation consumes
the attempt, including missing framework, loader error and cancellation. An entry
failure before reservation performs no native operation and leaves the grant
unconsumed; the host can revoke it explicitly. REVOKED is permanent and never
restored. At most 128 grant records are retained; exhaustion rejects. Restart loses
all authority. No durable IAM, cryptographic authenticity or audit store is claimed.

Cancellation, elapsed-clock and wall-clock callbacks complete outside the registry
lock before the live grant and original-owner checks. Checks occur before native
load, before prediction and after result construction immediately before return.
No host callback follows the final live validation. Callback-triggered revocation
therefore cannot publish an admitted result. External concurrent revocation after
completed validation remains outside an atomic lease; authority and external
sources are not one transaction.

## Family-neutral composition

    Existing read-only source -> R7-02 -> B2 / real frozen M3 -> owned input
    Original M6 native fit -> B1 original native UBJSON envelope
                            |
                  OwnedExecutionProvider
                            |
                  ControlledExecutionAuthority
                            |
                  ControlledH1ExecutionService
                            |
                   fixed execution adapter allowlist
                            |
                  XGBoost 3.2.0 native load/predict

The shared service contains no XGBoost imports or feature conversion. Family and
candidate selection are checked against original M6 metadata. Only the XGBoost
adapter implements validate/load/predict. Known LightGBM/LSTM/GRU/Transformer owners
return UNAVAILABLE before native access; unknown or mismatched families reject.
Future unavailable requests may omit executable inputs; they cannot receive a
grant or successful result. Future adapters require a separately reviewed owner,
artifact and executable-input implementation, not filename or plugin discovery.
B1 remains an XGBoost-specific envelope, not a universal artifact contract.

## Numerical evidence and output

The positive test genuinely trains through the original M6 NativeBackend with
four bounded boosting steps and a fixed seed, exports B1 UBJSON, captures B2 inputs
through the real read-only repository and original M3 kernel, reloads a Booster and
executes H1. It independently compares against the original fitted XGBRegressor
and a second direct native reload. All receive the same authorized matrix and no
validation labels. Finite float32 shape and bounded magnitude are checked.

The observed comparison is bitwise float32 equality (absolute and relative
tolerance zero) in this pinned CPU fixture; this is a demonstrated local property,
not a guarantee across frameworks/hardware/versions. Four-series lag values are
32, 132, 1032, 1132. Genuine prediction values are recorded in the test output.
The versioned result binds the complete request, grant declaration, adapter,
float32 values and payload digest. Row order is the original M2 sample order in
the retained B2 output, referenced by output/representation/binding IDs. A public
result declaration alone is not authenticated producer evidence for another system.

Classification is CONTROLLED_OFFLINE_SYNTHETIC_NATIVE_H1, production_permission=False,
persistable=False. It is genuine native execution on synthetic data, not a genuine
model-quality or deployability claim. M7 remains TEST_ONLY_NON_DEPLOYABLE /
NO_MODEL_PAYLOAD; native M6 candidates still cannot be published through M7.

## Isolation and verification

Run explicit tests only:

    python tests/native/run_b3.py tests/runtime/test_controlled_h1_execution.py -s

The launcher verifies the accepted image ID
`sha256:8adc380a27f6dfde49066ac0ed323678783ca58e553272d3409e0fe9d04cf7c8`
and reuses B1's disposable worker: network none, read-only root and src/tests mounts,
UID/GID 1000, all capabilities dropped, no-new-privileges, 1 GiB memory/swap ceiling,
1 CPU, 64 PIDs, 180-second CPU ulimit, 240-second default parent wall timeout and
256 MiB tmpfs. No Docker socket, home, data/models/artifacts, credentials or Git
mounts are exposed. Synthetic catalogs exist only in worker tmpfs and are removed
with the worker. Required packages are already pinned; nothing is installed into
a shared environment. Tests inspect actual worker mounts/cgroup/capability/network
settings, and exercise timeout/cancellation cleanup. The host launcher always
removes its exact container in finally; no persistent native payload is published.

Native library calls themselves are not preemptible by cooperative Python
callbacks. The worker is the hard CPU/memory/process/wall-time boundary; protected
host composition must use it. An environment marker is a test precondition, not
proof of isolation or execution authority.

## R7-05B and further work

B3 input-handle require() verifies retained data integrity and current owner, not
fresh source authority or a new capture. Its snapshot remains the exact historical
snapshot authorized by B2. New publications/origins and current-source freshness
require separate explicit policy. Native artifacts and grants are process-local;
no durable publication, cross-process owner transfer or production authorization
exists. Other four native families remain unavailable.

R7-05B must separately define trusted producer verification of genuine results,
independent write authority, durable uniqueness/CAS and correction policy, and
legacy consumer migration. R7-05A must continue rejecting values/evidence today.
Production serving, API/job activation, model promotion, synchronization, genuine
business-data training, durable lifecycle and Phase 7 closure remain outside B3.

## Recorded verification

All commands use the launcher above and explicit test selections, with no broad
repository collection. Final complete B3 module: **77 passed in one invocation**.
Full B1 module: **53 passed**. Full B2 module: **51 passed**. Full M2 plus selected
M3 frozen replay, five-family M6 ownership/preparation, R7-02 external authority,
R7-03 terminal revocation and R7-04A terminal wrapping: **248 passed**. R7-05A's
54 cases passed in three disjoint selections: contracts/identity (29), substituted
lineage/authority (20), terminal/non-writing boundaries (5). Total: **483 distinct
repository cases**; exploratory and overlapping reruns are not added.

An initial combined R7-05A invocation exited 137 under the unchanged worker limits
before completion; it is not counted as passing evidence. The smaller disjoint
runs completed without increasing those limits. An additional actual 0.1-second
parent wall-time experiment returned 124 and left no container behind. The exact
kill cause of the initial 137 exit was not captured; no Python assertion failure
was reported in that incomplete run.

The final native fixture exported 4,191 UBJSON bytes, digest
`ce692bb41ab49395f3fd2423d14483740f1009bfbb61f137192936772a9b6e2c`.
Its four H1 values were `498.6732482910156`, `649.3267211914062`,
`498.6732482910156`, `649.3267211914062`, in original M2 sample order.
Prediction bytes digest:
`a9880d3921f3cd23fc088caa8bb7ec65fe008cef57ba012e3f04e4374e112510`.
Artifact/candidate/snapshot IDs differ for newly finalized synthetic publications;
identical complete evidence and a newly explicitly issued equivalent grant produce
byte-identical result serialization. Such declarations never substitute for the
original opaque capability.

B3's positive/terminal/parity cases execute genuine native operations. Future-family
dispatch is contract-only, backed by original M6 estimator-stub references, and
makes no native-family readiness claim. Framework/error/tampering tests inject
failures around genuine owned material; they are not alternate synthetic success
paths. Prior accepted evidence is preserved but not added to the executed count.
