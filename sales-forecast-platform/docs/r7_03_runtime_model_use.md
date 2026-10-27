# R7-03: non-executable runtime readiness boundary

This is a **deny-only** same-process, trusted-host, controlled-offline H1
inspection boundary. Neither an immutable request, an inspection grant receipt,
a decision digest nor M9 further-validation approval permits inference.

## Accepted dependency inventory

* M6 `CanonicalAdapterTrainingService._candidates` retains original references,
  metadata and frozen M3/M4/M5 state. M7 `_owned` authenticates the exact live
  reference and trainer; copied metadata/references are insufficient.
* M7 `CandidatePackagingService.verify` uses its provider and local repository
  to verify immutable bytes, original ownership and preprocessing/provenance.
  All five native publishers/loaders are UNAVAILABLE. The only finalized format
  is TEST_ONLY_NON_DEPLOYABLE / NO_MODEL_PAYLOAD JSON. A format declaration is
  not a model artifact or a native-compatibility attestation.
* M8 original evaluator binds candidate packages, retained references, prediction
  batches, truth revision, population, and scoped ensemble evaluation identity.
  Its synthetic predictions are not authenticated native predictions.
* M9 `require_further_validation_decision` authenticates the original lifecycle
  handle, approved state, actor/evidence liveness and original M8 report. Its
  freshness procedure can reread independently authorized evaluation labels;
  R7-03 does not add a label API or execute a producer. Approval is exclusively
  APPROVED_FOR_FURTHER_VALIDATION, not runtime or production permission.
* R7-02 captures canonical source context through accepted repositories/providers,
  independent release/value attesters and separate external-forecast callbacks.
  `validate_preparation_context` checks original retained frozen state. It is
  SOURCE_CONTEXT_ONLY, not model-input construction/attestation. Historical
  exogenous encoder revision limitations remain enforced.
* R7-01 `Runtime` is lazy startup composition, not authentication. R7-03 is not
  wired into API/tasks/scheduler and adds no fallback to operational services.
* Existing production resolver reads `models/production/current.json` and sync
  can contact MLflow/write pointers. Neither is imported or used by R7-03.

## Composition and exact scope

Protected host startup constructs `RuntimeModelUseEvidence(packaging,
original_candidate, lifecycle_service, original_lifecycle_handle,
snapshot_service)`. Accepted owners retain their repository/provider layers;
the owner bridge stays in the service layer. Each accepted service retains its
original Repository → Provider → Service path. There is no reverse provider-to-
service dependency, SQL, new registry, persistent authority or production pointer.

The versioned immutable `RuntimeModelUseRequest` binds candidate/package/family,
M7 execution-representation binding (not an invented per-output M2 identity),
M6 compatibility, original frozen/canonical state/schema, M9 lifecycle/approval
transition, M8 report/scoped ensemble identity, snapshot ID and complete R7-02
request (organization, exact full series set, publication, origin/cutoff, H1,
feature schema, geometry, availability and capture budgets). Model-fit cutoff
must precede or equal the requested origin. Only the explicit
`controlled_h1_runtime_readiness_inspection_v1` purpose is accepted.

`bootstrap_runtime_model_use` returns a host-only operator and caller-facing
service. The host supplies a protected context digest and trusted clocks. The
operator may issue an **INSPECT_READINESS_ONLY** capability for one exact request,
actor assertion and validity interval (at most one hour); it does not issue model
execution permission. Actor strings and context digests do not authenticate a
host. Capability object identity, its pinned original registry and live validity
are required; receipts, copies and foreign issuers reject. Grants can be revoked
and reused for repeated fresh inspections until revoked/expired. There is no
inference token, one-shot execution transition or automatic approval creation.
The operator/constructor must never be exposed to untrusted request handlers.
Arbitrary same-process private attribute mutation is outside the trust boundary.

## Decision and revalidation

`service.decide(request, capability=handle, cancelled=...)` returns only:

| Status | Meaning |
|---|---|
| DENIED | Invalid/missing authority or evidence, cancellation/expiry, resource failure or busy/reentrant composition. No admitted evidence fields are published. |
| UNAVAILABLE | Exact original evidence and inspection grant verified twice, but native payload, attested executable inputs and runtime model-use authority are unavailable. This grants no permission. |

ELIGIBLE_FOR_CONTROLLED_VALIDATION and production approval are deliberately
unrepresentable. M9 registered/evidence-verified (A), approved-for-further-
validation (B), specific runtime permission (C), and production permission (D)
are distinct. This boundary can verify B and report that C is unavailable. D is
out of scope. All results are NON_EXECUTABLE_READINESS_DECISION_ONLY with false
execution/production permission and TEST_ONLY_NON_DEPLOYABLE evidence.

Each operation rechecks its exact host capability around accepted collaborator
calls, performs a complete evidence pass, and repeats it before publication.
Each pass captures one authorized R7-02 snapshot, compares its requested ID,
verifies M7 ownership/package and retained-state compatibility, then refreshes
M9 original approval/evaluation evidence. The exact same candidate reference and
packaging service must be retained by M9's pinned evaluator. Repeated evidence
identities must match. Cancellation/deadline checks follow callbacks, including
final serialization. Exact elapsed >= deadline rejects. No cached success can
substitute for a fresh pass; no partial report is returned.

**Two canonical captures per completed inspection** are intentional: R7-02 has
no original-owner post-capture verification entry point. Detached snapshots are
never accepted as authority. This is not a promise of one shared transaction:
if the second authorized capture differs or fails, the whole decision denies.
M9 may recapture authorized evaluation truth for freshness. No fitting, feature
execution, native loading or inference occurs during inspection.

## Bounds and limitations

At most two evidence passes; one active operation per service; 128 retained
inspection grants; request <=256 KiB; decision <=16 KiB; elapsed <=60 seconds.
Accepted snapshot/M7/M8/M9 row/byte/group/evidence/rational bounds remain in
force. Smaller caller bounds reject. Checks are cooperative; blocking callbacks
cannot be preempted. External evidence and authority checks are sequential,
not atomic revocation leases, and cannot guarantee validity after return.
The host protects collaborators and clocks. There is no durable cross-process
registry, cryptographic authenticity, hard CPU/RSS isolation, or production IAM.
Full immutable history is read before origin filtering, preserving physical-
dtype admission dependence. Error/status text is fixed; public results expose
identity references only, no private models/manifests, raw labels or fitted state.

## R7-04 prerequisites (not implemented)

* Separately accepted native artifact publication/loader and genuine numerical
  compatibility evidence; current M7 artifacts cannot be used as trained models.
* Original-owner executable-input binding for actual M3/M4/M5 outputs with exact
  precision/masks, appropriate runtime publication and model-use authority.
* A separately authorized execution capability/lifecycle with cancellation,
  terminal revalidation and consumption semantics. An R7-03 decision is not it.
* Durable authority/authentication/transactions as required by a future runtime
  integration scope, and explicit production policy before any deployment.

Production resolver/sync, forecast persistence, H2+, Phase 7 runtime wiring,
legacy cleanup, promotion and deployment remain untouched.
