# M9: controlled-offline candidate decisions

M9 authorizes candidate decisions and additional controlled validation only.
Every candidate/evidence record remains TEST_ONLY_NON_DEPLOYABLE. There is no
model loading, fitting, registry registration, model stage/alias/pointer update,
artifact sync, deployment or runtime migration API.

## Inventory and reuse

M6 original references remain private to their original issuer. M7 verifies the
exact original reference, package bytes, plan, schema and frozen state without
native operations. M8 verifies the complete shared-truth evaluation, scoped
ensemble identity, original M6/M7 ownership and independent label/publication
permissions. M9 calls these existing paths; accepted implementations and serialized
identities are unchanged.

M8 does not retain an owned report registry or its verified publication metadata.
M9 therefore NEVER accepts a detached report as verified evidence. It calls the
pinned original M8 evaluator, retains the exact immutable request/report privately,
and issues an opaque original evidence handle. Before positive decisions, evidence
disclosure, or requiring an active further-validation decision, M9 re-evaluates
that EXACT frozen request through the same original M8 service and requires
byte-identical evidence. This repeats independently authorized label reads and
exact scoring, not prediction or native loading. It provides fresh publication,
revision, cancellation and package checks without reconstructing authority from
metadata. One canonical truth capture still applies per M8 invocation; M9 does
NOT claim one capture per whole lifecycle. Changed/revoked/expired evidence fails
closed. Missing M8 disclosure/ownership hooks are not simulated with a Boolean.

Existing model registry/MLflow and production sync code is not imported. Existing
`audit_logger.py` opens operational database sessions; job-status repositories
mutate operational tables. Neither is reused. The new in-memory decision ledger
is a Repository -> Provider -> Service boundary, not a production model registry.
There is no SQL in the new service and no disk/default destination.

## Authority boundary

A trusted host calls `bootstrap_candidate_lifecycle(original_m8, policy, clock=...)`
and privately retains the returned operator. The handler receives only the
service and narrowly scoped capabilities. Bootstrap does not register global
trust. Constructing another authority, copying records or constructing matching
hashes cannot authenticate to the original pinned service.

The operator explicitly issues ActorCapability handles from immutable
ActorAssertion/ActorGrant records: opaque actor ID, host identity context, exact
roles/candidate IDs, bounded validity, original authority and policy. The host
must authenticate that identity assertion BEFORE issuing it. Actor strings and
public grant/transition records are not credentials. Handles require exact object
identity in this original registry, cannot be serialized, and copied/foreign/
reconstructed capabilities reject. Operator/service copies also reject.

The host must protect bootstrap, operator, original M8 composition, clock and
private object graph. Python encapsulation is API separation, not a sandbox or
cryptographic/independently verified IAM. Same canonical metadata may hash equally
in independently created authorities; only the original pinned registry and
private handles establish current authority. Use opaque public actor/context IDs,
never passwords, tokens or personal/customer data. No callback True result can
replace an actual M9 grant, state transition and approval record. Existing M7/M8
callbacks authorize data/evidence use, not M9 lifecycle approval.

Policies are immutable version 1.0, candidate-only/H1, one exact tenant/logical
dataset and assessment cutoff, with effective interval, evidence TTL, bounded
records/bytes and LOCAL_M9_ORDER_ONLY selection assurance. Positive actions check
policy, original ownership, all referenced grants, current evidence and state
version. UTC clocks are host-owned, nondecreasing, and expiry uses now >= expiry.
Reviewer, approver and administrator roles are explicit; there is no role wildcard
or implicit role inheritance. A host may deliberately grant multiple roles to
one actor: independent/four-eyes sign-off is NOT claimed.

## State transitions

| Action | Before | After | Required role |
|---|---|---|---|
| REGISTER | none | CANDIDATE_REGISTERED | reviewer or administrator |
| VERIFY_EVIDENCE | CANDIDATE_REGISTERED | EVIDENCE_VERIFIED | reviewer |
| REQUEST_REVIEW | EVIDENCE_VERIFIED | REVIEW_PENDING | reviewer |
| APPROVE | REVIEW_PENDING | APPROVED_FOR_FURTHER_VALIDATION | approver |
| REJECT | any nonterminal state except approved | REJECTED | administrator |
| REVOKE | any nonterminal state including approved | REVOKED | administrator |

Registration is metadata-only genesis: it checks original M6/M7, not a performance
claim. Every positive evidence/review/approval transition additionally requires
an original M9-owned, freshly verified M8 report and exact evidence ID. The first
verification latches that evidence; it cannot be silently replaced during review.
If it expires or becomes invalid, progress fails closed. A restrictive admin
rejection/revocation remains possible without claiming corrupt/stale evidence is
verified. It uses the existing original lifecycle, scope, policy and CAS version;
its record explicitly has no newly verified evidence. This prevents broken
packages from blocking withdrawal.

REJECTED/REVOKED never reactivate. A distinct new lifecycle requires explicit
registration referencing the previous terminal lifecycle and an administrator.
Old handles remain terminal. Registration retries return the same lifecycle.
Identical decision retries refresh authority and return the original transition
plus CURRENT state, not a new approval. Conflicting duplicates reject. A retry of
an old approval after revocation can return historical data with current REVOKED
state; it cannot pass `require_further_validation_decision`.

A state string, receipt, digest or copied audit chain is not ongoing permission.
Consumers must pin this service and freshly require its original lifecycle's
further-validation decision. That method rechecks active state, policy, approval
actor, evidence actors, original package/ownership and exact M8 evidence. It
never issues native-model-use authority. No automatic M6/M8 job execution or
production enforcement migration is wired by this milestone.

## Precommit and disclosure scope

Validation evidence is captured under an original lifecycle and reviewer grant.
Before M9's FIRST test-capture attempt in this authority, an operator-authorized
reviewer may precommit an exact test request and recipe against original owned
validation evidence. Membership, source/state/package identities, original plan
and recipe must agree. There is one immutable record per test scope; changed
weights/requests/validation context conflict. Matching retries before use are
idempotent; expired or consumed records reject.

Test capture requires the exact original precommit handle, before its expiry.
It consumes the handle and records a conservative may-disclose-test-truth event
BEFORE invoking M8. Even a failed/denied attempt prevents reuse and new local
precommits. Previously frozen precommits may execute their exact requests; no new
recipe can be committed after the first local test attempt. Subsequent decision
verification only re-evaluates the original frozen evidence, never replaces its
recipe or prediction inputs. Validation evidence, precommit, disclosure attempt
and captured test evidence have strictly ordered local sequence numbers, even
when clock times are equal. The operator can inspect immutable precommit and
disclosure audits; decisions bind the exact precommit/evidence IDs.

This guarantees ordering INSIDE THIS ORIGINAL PROCESS-LOCAL AUTHORITY only. M8
and source providers may have disclosed test results earlier/outside it; trusted
capture also reads complete immutable storage before selecting requested rows.
Earlier authorities, process restarts, external knowledge and externally chosen
weights cannot be ruled out. GLOBAL_PRETEST_VERIFIED/leakage-safe selection claims
are unsupported and fail closed, rather than being inferred from timestamps or
hashes. Weight optimization is absent. No claim of global cross-run test-selection
prevention or genuine performance validation is made.

## Ledger, auditing and concurrency

New immutable versioned records cover policy/authority/actor assertions and grants,
registration/decision requests, lifecycle snapshots, evidence references,
precommits/disclosure attempts, transition outcomes and chains. Canonical accepted
M2 serialization/SHA-256 identities bind all declared fields. Audit transitions
bind exact policy, actor grant, request, candidate/source and verified evidence,
reason, before/after state/version, predecessor and previous global audit head.
Authority grant/revocation records have a separate immutable hash chain.

The repository stores read-only mapping snapshots and immutable transition tuples.
Under a local lock, it validates expected state version, duplicate fingerprint,
predecessor/head and capacity, constructs the new state/audit/idempotency maps,
then publishes ONE copy-on-write snapshot. No callback or I/O lies inside the
commit. Injected failure before the swap leaves all old state/audit intact.
Concurrent conflicting decisions cannot both advance the same version; identical
requests are idempotent. Service operations are serialized/non-reentrant; operator
revocation may reenter during an existing M8 callback, and fresh local checks
prevent approval under revoked/expired grants after it returns.

`verify_lifecycle_audit(records, expected_head)` checks bounded canonical records,
chain/index/state/role/version continuity and terminal predecessor linkage against
that head. A head must come from a trusted observer; hashes alone do not prove
issuer authenticity or external tamper-resistance. Public audits contain no
private capabilities, full manifests, training data or raw truth. Authorized
`describe_evidence` returns only the already permitted M8 test-only report, whose
small-group metrics may reveal information; no new privacy guarantee is claimed.

The ledger is PROCESS-LOCAL AND NON-DURABLE. A crash/restart loses decisions and
capabilities. No import/restore path can revive them; default is deny until a new
explicit trusted lifecycle. There is no power-loss durability, filesystem audit
anchor, distributed consensus or cross-process authority. Auxiliary authority/
evidence/precommit logs are local bounded provenance, not durable transactions.
Only lifecycle state+transition audit+idempotency publication is one atomic
repository transaction. Memory exhaustion and process failure are not durable
recovery guarantees. No parallel production registry or production pointer exists.

Limits: at most 16 actor grants, 16 precommits, 16 test attempts, configurable
16 (maximum 32) evidence records, 32 (maximum 64) lifecycles and 256 (maximum 512)
transitions. Existing M8 row/byte/arithmetic/elapsed caps apply to each evaluation.
TTL checks and memory/work limits are cooperative, not hard CPU/RSS or blocked-I/O
preemption. Current data permissions and external source checks remain sequential,
not atomic revocation leases. There is no M9-wide hard execution deadline.

Supported untrusted ingress is the service's guarded requests, guarded M9 model
validation, and structural audit verification. Operator/bootstrap are protected
host interfaces, not request factories. Raw TypeAdapter/compiled/deprecated or
direct embedded foundation validation and underscore helpers are not ingress.
Errors detach cause/context, log no payload, and do not globally suppress warnings.
The pre-existing standalone configuration_digest diagnostic issue is unchanged
and has no new unvalidated M9 path.

Deferred: durable/auditor-signed ledgers, deployment IAM, independent sign-off,
global pre-test exposure enforcement, native evidence/loading/fitting, production
approval/promotion, Phase 7 runtime migration, registry integrations and legacy
cleanup. This milestone does not itself declare Phase 6 production-ready.
