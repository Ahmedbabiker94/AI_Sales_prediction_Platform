# R7-05B5-2: stored-forecast evaluation, controlled offline only

This additive boundary evaluates an original B5-1 read against independently
permitted, explicitly synthetic revision-pinned labels. No forecasts are generated.
No database result, accuracy table, receipt, grant or operational job is written.
Nothing is attached to an API, dashboard, report, scheduler, Celery or registry.

## Actual-source investigation and operational blocker

`src/providers/postgres_sales_provider.py:PostgresSalesProvider.get_actual_sales`
delegates through `src/services/forecast_history_service.py:ForecastHistoryService`
to `src/database/history_repository.py:HistoryRepository.get_actual_sales`.
That query returns a single `weekly_sales` using store/dept/date with `LIMIT 1`.
`src/database/sales_repository.py:SalesRepository.get_sales_history` likewise uses
store/dept. `src/database/tables.py:sales_history` declares Date, store, dept and
nullable Float weekly_sales, but no tenant/dataset identity, label revision,
published/effective/available timestamps or historical revision preservation.
`src/services/forecast_accuracy_service.py:evaluate_all_pending_forecasts` joins
those legacy values, skips missing actuals, defines zero percentage error as zero,
and writes via `PostgresAccuracyProvider.save_accuracy`. The older
`forecast_evaluation_service.py` also couples actual lookup to accuracy saving.
Neither path is used or modified here.

Canonical data contracts/adapters preserve explicit source extraction metadata and
measurement/time declarations (`src/data/contracts/dataset.py`, `mapping.py`,
`features.py`, `src/data/adapters/base.py`). These are not an independently
authorized historical actual-label publication service. A legacy SQL Date or a
Walmart week identifier does not prove calendar/timezone, units, publication,
availability, tenant authorization or revisions. Generic source extraction is not
label read permission. We cannot reconstruct these missing facts from current rows.

An earlier controlled synthetic path already exists in
`src/services/canonical_forecast_evaluation_service.py`: `LabelRevisionClaim`
and `_verified_label_claim` bind exact captured values and metadata, but a
separate host `verify_label_revision` callback must attest them. Its
`CanonicalForecastEvaluationService` explicitly invokes the original rollout
producer before capturing labels. Its synthetic tests use an independent test
ledger, not an operational actual-label source. It cannot be invoked as this
stored-forecast reader without executing/regenerating forecasts. We preserve it
and do not treat declarations or source available_at alone as revision truth.
Existing exact rational scoring (`src/data/processing/forecast_evaluation.py`,
`src/data/contracts/forecast_evaluation.py`) remains unchanged; this milestone
names its separate binary64 presentation policy explicitly and reruns the pure
arithmetic contract tests without invoking rollout.

Operational actual integration remains **BLOCKED** until an independently reviewed
source supplies immutable historical label revisions, exact scope, measurement,
calendar and trustworthy publication/effective/availability metadata. No invented
revision/timestamp is attached to an existing sale. All new labels explicitly carry
`SYNTHETIC_CONTROLLED_OFFLINE_LABELS_ONLY` and are host-created test data.

## Protected construction and ownership

`build_stored_forecast_accuracy(original_reader, scope, snapshots, operator=...)`
is called only by the protected host. It pins the exact original B5-1 reader and
its original private state, creates an independent label authority and returns
that authority separately from the consumer-facing evaluator. Bootstrap does not
open any connection. No writer credentials, model objects, adapters, callbacks for
fetching arbitrary values, or SQL are accepted by the evaluator.

The host retains operator, label authority, original B5-1 authority, reader
credentials and both private registries. Consumers receive evaluator, original
forecast read grant/selection and original label read grant/selection. Label
principals are independent from forecast principals. Synthetic subject strings
are host labels, not production authentication. A tenant UUID/GUC authenticates
nobody. No production IAM is implemented.

Principal/grant maps retain exact handle identity. Opaque handles reject ordinary
copy/deepcopy/pickle; new/equal/serialized handles are not original issuances.
Grants capture canonical selection JSON, binding principal, full ordered scope,
exact revision, exact snapshot digest, target interval and evaluation-as-of.
No latest-label substitution or public snapshot-result submission exists.
Host snapshots are copied/revalidated to immutable strings at construction, with
at most 32 revisions, each bounded by the existing 512 KiB JSON decoder. No caller
mutable snapshot alias is retained. A source snapshot/digest is integrity data,
not authentication. Returned evaluation declarations cannot create read grants,
execution permission, persistence permission or an accuracy write receipt.

This is not a Python sandbox: arbitrary host code, monkeypatching a trusted
reader, coherent private-record replacement, credential exposure or mutation of
bootstrap code violates host integrity. Private underscores alone do not create
an adversarial security boundary. Host governance must exclude consumers from
construction and private state. No durable/cross-process authentication is claimed.

## Authorization cutoff, cancellation and closure

Both original grants and exact requests are checked before the original reader
is invoked. Label denial therefore opens zero reader/writer connections. Forecast
denial uses the accepted B5-1 original issuance checks, also before connections.
There is no label connection factory: labels are immutable synthetic memory data.

Principals/grants last 1--60 seconds; grants cannot outlive their principal.
Expiry equality rejects. This repair adds an OPTIONAL protected B5-1 joint seam,
without changing standalone B5-1 defaults. Joint composition explicitly provisions
a `HostReadClock` at original reader construction BEFORE principal/grant issuance.
B5-2 requires that provisioning; an existing legacy grant cannot be rebound to a
new clock epoch. The original host operator pins both services and clock domains;
forecast and label grants/principals remain separately issued and independently
verified. Distinct clock domains/epochs are supported and never translated.

HostReadClock has a fixed, callback-free sampling implementation. System mode
uses captured builtin wall/monotonic functions. Explicit synthetic mode uses only
host-controlled scalar counters, with monotonic/finite checks. No arbitrary
sampler callable is accepted as a domain. Test-only counter updates require the
original host operator; counter rollback/nonfinite observations fail closed.
Clock callbacks still run during the callback phase, but cannot select or replace
the authoritative clock domain. Their observations must be finite and cannot be
ahead of the final domain sample. Default standalone clock behavior is unchanged.

A protected binding is registered only by host construction, using the original
reader operator and exact reviewed label-state type. It accepts no verifier
callable. Per-evaluation opaque JointReadCheckpoint handles are privately retained
in the original reader state, bound to original forecast/label grant and principal
record identities, exact canonical selections/scope/revision/digests/as-of, peer
issuer and both original domains. Forged/foreign/copied/deserialized handles are
not original issuances. The evaluator always selects its original protected
binding internally; no public evaluator parameter selects a checkpoint.

Sequence:
1. Capture the original grant/principal identities into an ephemeral checkpoint.
2. Run supported label callbacks and the ORIGINAL forecast require() callbacks.
3. Perform fresh callback-free joint validation under ordered locks.
4. Invoke the ORIGINAL reader read(). It runs its original forecast callbacks and
   checks, then validates the joint checkpoint BEFORE provider lookup/connection.
5. After storage, the reader repeats original callbacks/checks, then joint checks.
6. Score the exact immutable rows. Run final label/forecast callback verification,
   then a fresh callback-free joint cutoff for BOTH original grants.
7. Return the immutable outcome; close/invalidate the ephemeral checkpoint in
   finally on success, denial, exceptions and KeyboardInterrupt/SystemExit.

Critical-section order is ALL distinct clock locks by object id, THEN ALL distinct
forecast/label state locks by object id. Every path uses that order. No callbacks,
SQL, model work or user-supplied clock functions execute while those locks are
held. Live forecast checking reuses the accepted checks factored into _ReadState.live;
label checking uses the original label require/principal checks. Samples are fresh
from each original clock domain, then both live records are checked without an
intervening callback. Revocation and synthetic clock updates cannot interleave
with that critical section. Callback phases never hold authority/clock locks.

The initial joint cutoff occurs immediately before provider dispatch after all
reader prelookup authorization callbacks. The final evaluation cutoff is the last
joint locked validation after all callback-bearing verification. Final validation
opens no connection. A denied initial cutoff opens zero connections/dispatches;
a denied final cutoff may have one previously authorized SELECT-only read, but
returns no evidence. A checkpoint does not renew either grant, and a closed one
cannot replay. Original read grants remain repeatable before their own expiry.

There is no atomic distributed lease or protection against arbitrary post-cutoff
host actions. Real system clocks are sequential observations inside the locked
cutoff, not stopped wall time; supported callbacks cannot run between those
checks. Revocation after lock release cannot retract data already authorized at
that cutoff. Concurrent source/private-state/code tampering is outside the trusted
host boundary. Constructors, operator, domains, issuer records and credentials
must remain protected. Synthetic host grants are not production authentication.
Ordinary failures are sanitized; KeyboardInterrupt/SystemExit propagate and the
accepted read connection cleanup remains active. No earlier successful response
or detached result substitutes for final original-handle verification.

## Matching and temporal policy

The evaluator invokes the original B5-1 service itself, never accepts a caller's
VERIFIED_RECORD_SET dictionary, and requires the exact expected full batch digest
and original idempotency identity under its original forecast grant. B5-1 provides
verified storage content; neither milestone independently re-attests historic
producer authorization from serialized lineage.

Each label target contains canonical series UUID, exact target start/end, metric,
canonical measurement JSON (unit/currency included), canonical temporal JSON
(Gregorian calendar/timezone/frequency/anchor included), and identity transformation.
Tenant/dataset are bound at snapshot/request scope. Every target must equal its
verified forecast target. The batch retains original candidate/family, origin,
cutoff, publication, immutable lineage, original ordinal/key and revision. No
Store/Dept/date-only matching, neighbor dates, calendar guessing, currency/unit
conversion, forecast recalculation or date imputation occurs. Conservative exact
semantic JSON equality may reject equivalent but differently declared conventions.

`evaluation_as_of` is an explicit host-approved retrospective assessment timestamp;
it is not the forecast cutoff and is not inferred from the machine clock. The
whole H1 target interval must be complete, and revision publication, effective-time
and every row's availability must be <= assessment as-of (equality allowed).
Availability cannot precede snapshot publication. Effective-time describes when
this synthetic revision is applicable, not a replacement for its publication or
availability. No automatic latest/effective revision selection occurs. Operational
sources must substantiate all timestamps separately.

One label per authorized series for the exact interval is required. Duplicates or
foreign series fail snapshot validation. Missing/future labels return
LABELS_NOT_READY without partial metrics. Label storage order is immaterial to
joining; forecast order and ordinals remain unchanged. No denominator shrinkage.

## Metrics, numeric precision and result contract

Authoritative forecasts remain the original four-byte little-endian IEEE binary32
hex strings. `2d56f943` widens to 498.6732482910156; `e9542244` widens to
649.3267211914062. No forecast byte is re-encoded, sorted, rounded or replaced.
Synthetic actuals are canonical finite bounded decimal strings (128 digits,
absolute decimal exponent <= 300); float conversion for scoring is explicit.

For the complete n-row population, e[i] = prediction[i] - actual[i].
MAE = fsum(abs(e[i])/n); bias = fsum(e[i]/n). RMSE is mathematically
sqrt(mean(e[i]^2)), evaluated as scale * sqrt(fsum((e[i]/scale)^2)/n),
where scale=max(abs(e[i])); an all-zero error set has RMSE=0. Scaling avoids
intermediate square overflow/underflow. Arithmetic is Python IEEE binary64 plus
math.fsum/sqrt, version BINARY64_SCALED_FSUM_V1. Decimal-to-binary64 rounding is evaluation arithmetic,
not a change to label or prediction evidence. If finite inputs still cause nonfinite arithmetic, evaluation returns UNAVAILABLE,
never clipped metrics. Signed finite
actuals, including negative values and zero, are explicitly allowed. No MAPE or
sMAPE is provided, avoiding an invented zero-denominator/business policy.

Immutable AccuracyEvidence carries exact forecast selection, full original batch
and receipt declarations, pinned label selection/revision/as-of and full snapshot,
ordered pair identities/authoritative prediction bytes/actual values, metrics,
population/matched counts and zero missing/ineligible counts. Complete coverage is
mandatory. Canonical versioned UTF-8 encoding has an integrity SHA-256, with a
512 KiB total result budget. This digest is not proof of trusted origin or durable
publication. Reconstructed result declarations are never evaluator inputs.

Outcomes: VERIFIED_EVALUATION includes all evidence; DENIED has no existence or
scope data; LABELS_NOT_READY is available only after authorization; authorized
semantic mismatch is INCOMPATIBLE_SCOPE; missing/corrupt forecast evidence,
wrong pinned content or infrastructure failure is UNAVAILABLE. Non-success
outcomes disclose no rows, counts or diagnostics. NOT_FOUND is intentionally
mapped to UNAVAILABLE for evaluation. No missing receipt implies UNKNOWN write
retry permission.

## Tests and B5-3 prerequisites

`tests/runtime/test_stored_forecast_accuracy.py` is contract-only mocked storage,
not proof of PostgreSQL or native execution. `tests/postgres/run_stored_accuracy.py`
uses the unchanged accepted PG16.15 isolated disposable harness. Test setup writes
synthetic canonical fixtures only; the evaluator uses the accepted SELECT-only
reader role, actual READ ONLY scoped transactions and fresh connections. SQL spies
assert SELECT/SHOW only and unchanged stored content; accepted permission/RLS tests
exercise denied writes independent of read-only transaction settings. No native
model path is imported or executed. No accuracy table is changed. Tests distinguish
synthetic fixture setup writes from authorized reader-only evaluation.

No consumer activation is authorized. B5-3 requires separate review/integration
and tenant-authorized reporting/dashboard read adapters that preserve forecast and
label revisions, units, calendar, as-of, full-population policy and metric meaning.
Operational auth, label-source historical revisions, credential/private-state
protection, durable governance/audit and any separately authorized accuracy
persistence remain unimplemented prerequisites. No legacy fallback is permitted.

## Registration-to-handoff ownership repair

The protected evaluator enters a host-private `joint_scope` before issuing a
checkpoint. The scope owns the original record before its weak registry entry
is published. `issue_joint` requires this original entered scope, issues at most
one record in it, and cannot renew a closed scope. Thus neither the returned
checkpoint assignment nor a successful issuing-function return establishes
cleanup ownership. The scope is already responsible for aborting issuance.

Lifecycle: UNENTERED -> ENTERED_NO_RECORD -> OWNED_REGISTERED -> CLOSED.
The record publication is the registration linearization point. There is no
subsequent ownership-transfer gap: the same original scope remains responsible
until exit. The evaluator's `with` cleanup covers issuance, reader dispatch,
scoring, final paired validation and result construction. KeyboardInterrupt and
SystemExit propagate; they are not successful results. Normal exceptions retain
the existing fail-closed result policy. Close/abort is terminal and idempotent.

The checkpoint is usable only within the original owning thread and protected
`with` body, with the original lifetime object present in that activation. Fixed
CPython 3.11/3.12 frame, instruction-position and exception-table metadata identify
that body; missing/unsupported metadata denies, with no permissive fallback.
This is a narrowly scoped synchronous host interface, not a generic context
manager for generators, async suspension, arbitrary inner exception handlers or
cross-thread handoff. The fixed evaluator's simple scope is the reviewed caller.
Standalone B5-1 callers do not enter this seam or depend on its metadata.

Terminal invalidation precedes cleanup locking. If a further asynchronous
interruption prevents cleanup code from executing, unwinding or leaving the
original protected body independently makes the checkpoint unusable, including
when a traceback or the same caller frame is retained. A subsequent attempted
validation rejects and removes a stale entry. The registry is weak: it cannot
itself retain an abandoned record/owner/grant graph. Physical removal can be
delayed while host-owned tracebacks retain objects; this is not active authority.
The implementation does not promise uninterruptible Python cleanup, crash-durable
state, resistance to coherent host-private/interpreter mutation, or distributed
revocation. SIGKILL/process death discarding memory is not the within-process
safety argument. The paired original-grant clock/lock cutoffs are unchanged.

Tests inject actual owned-process SIGINT and SystemExit at registration, issuer
return, the caller's STORE_FAST opcode, dispatch, callbacks, scoring, final checks
and close/abort. They also catch interrupted exit in the same frame and verify
that retained handles cannot validate. Disposable PostgreSQL tests distinguish
fixture writes from SELECT-only evaluation, zero preauthorization connections
from earlier authorized reads, and ensure all opened reader connections close.

Operational actual-label history and authentication remain BLOCKED. This change
adds no read scope, execution capability, write capability or consumer activation.

## Interrupt-safe lock ownership repair

All reader-state, synthetic-label-state and HostReadClock acquisitions now use
one private `_locked_call` executor. There are no lock acquisitions in
`ExitStack.enter_context`, or in acquiring `with lock` entrypoints, in this path.
Only fixed local critical bodies in the two reviewed services call the executor;
consumer arguments cannot select a body, lock, ledger, baseline or cleanup.
These mechanics do not constitute principal/grant/checkpoint authority.

Before any acquisition, an immutable ledger records the exact native RLock,
current thread identity and current-thread recursion-depth baseline for every
lock in the operation. Both cleanup regions are installed in the same executor
frame before the first native acquire. The C-level owner/depth increase is the
acquisition linearization point. A Python `acquired=True` assignment is not
needed to establish release responsibility, and no release callback is
registered after acquisition. A pending acquisition or timed-out foreign lock
has no additional owned depth and must not be released.

Cleanup uses only native current-thread depth and nonblocking native release.
It never acquires an authority lock. It releases precisely one owned level above
the original baseline; the baseline itself, a foreign thread's ownership, and
unexpected depth are never reset or force-unlocked. Duplicate cleanup observes
the baseline and does nothing. Wrong-thread cleanup rejects before releasing.
A second, already-protected idempotent cleanup pass handles interruption of the
primary cleanup pass, including an exception during native acquire's return or
native release's return. Genuine KeyboardInterrupt/SystemExit continue to
propagate; a bounded pruning timeout cannot replace an already propagating
interruption with an ordinary success/denial. Close invalidates the exact
original checkpoint before attempting registry pruning.

This combines a pre-acquisition release ledger, native ownership observations,
protected cleanup and bounded waiting; changing Lock to RLock alone is not the
safety argument. Recursive calls restore their own original depth. Acquisition
waits at most one second per lock, with at most four locks in reviewed calls;
contention can therefore return a fail-closed denial. The standalone public
read signature, original principal/grant requirements, scoped results and
independence from label permission are unchanged.

Lock hierarchy is unchanged: distinct clock locks ordered by domain identity,
then distinct authority-state locks ordered by state identity. Issuance needs
only the ordered authority pair; individual observations/registry operations
need one lock. Supported clock/cancellation callbacks execute outside critical
bodies. Paired validation owns both clock domains and states, samples fresh
clocks and checks the original grants/principals without callbacks or SQL.
Checkpoint ownership is checked again inside the paired critical section.

Joint evaluation now explicitly permits CPython 3.11/3.12 only, before entering
its metadata-dependent ownership scope. It still rejects missing compiler
positions/exception metadata. CPython 3.13 metadata-only probes must deny joint
entry; they are not full evaluator validation. Standalone B5-1 reads do not require joint ownership metadata. The additional
A1 metadata ordering protocol below has its own explicit runtime restriction. The native ownership/depth API is a pinned CPython
implementation assumption, not a portable generic locking backend.

Tests cover the original acquired-before-ExitStack-registration gap on the
preserved rejected source, native acquire-return/release-return interruptions,
pending foreign-thread locks, signals during blocking acquisition, partial
multi-lock acquisition, primary/backup cleanup, reentrant depth, concurrent
close/abort, same-frame catches, six-thread mixed grant validity and nested
reader/evaluator callbacks. Tests enforce process deadlines. Genuine PostgreSQL
cases distinguish zero preauthorization connections from earlier authorized
reads, preserve actual role/RLS enforcement, and close connections on final
failure. Original unsafe reproductions are not repaired acceptance passes.

Limits: this is process-local synchronous trusted-host code. It does not promise
crash-durable coordination, uninterruptible Python execution, recovery after
SIGKILL/interpreter failure, or progress when multiple independent termination
interruptions abort both cleanup passes. Such host termination
requires process shutdown, not continuing consumer activation or publicly
resetting locks. Finite stress/fault injection is not an arbitrary-schedule
proof. Host code, private state, credentials and the tested interpreter remain
protected deployment prerequisites. No schema, SQL, source/model adapter,
forecast execution, accuracy persistence or operational activation is added.

### Fail-stop reader-host quarantine (focused cleanup-escalation repair)

The protected reader **process** is one host-health domain. This deliberately
includes all its reader authorities, independent label authorities and clocks;
constructing another authority in that process is not recovery. The host alone
protects this module, its private state, credentials and original handles. This
is not a Python sandbox or production IAM.

`HEALTHY` permits the original checks. `SHUTDOWN_REQUIRED` combines terminal
quarantine and the requirement for a clean host-process restart. `ReadAuthority`
exposes only a non-secret read-only `host_health` status. There is no reset,
unlock, serialized recovery token, credential replacement or new-authority escape.
The gate enforces loss of all protected authorization/read/evaluation capability;
it does not kill an unrelated hosting process or pretend to release stranded locks.
Trusted lifecycle control must terminate this host and re-establish original
principals/grants in a clean process, never copy them across restart.

Every private lock executor publishes an operation obligation inside its two
prepared cleanup regions **before native acquisition**. It retains the exact
executor frame, owning thread and immutable native lock/baseline ledger. Cleanup
marks that obligation safe only after verifying all native depths restored. A
successful backup handles an interrupted primary; an interrupted backup after a
verified primary leaves harmless bookkeeping that health inspection prunes.
Neither case requires quarantine. A genuinely unresolved obligation is never
removed, even if test code manually releases native locks later.

An unresolved executor no longer on the actual owning thread's live stack is
pre-armed terminal shutdown evidence. Traceback retention does not make it live;
retaining the frame prevents frame-identity reuse. Health inspection uses captured
CPython live stack metadata, without acquiring any possibly stranded authority
lock. A terminal latch caches the finding; the latch is not the sole boundary.
SIGINT/SystemExit before or during latch publication leaves the original orphan
obligation intact, so subsequent inspection/authorization still fails closed.
No garbage collection, destructor, finite cleanup retry count or successful
caller assignment establishes this protection.

The obligation's publication is the ownership-record linearization point. If its
executor unwinds without verified cleanup, the retained obligation is the terminal
quarantine evidence; the first subsequent gate rejects before any new lock
baseline is sampled. Legitimate **live** recursive executors remain supported;
an unwound executor's stranded depth cannot be adopted by a subsequent operation.
All twenty acquisition sites still use the same reviewed private executor and
native thread/depth checks. Clock locks precede authority-state locks, each sorted
by identity. No additional lock or callback is introduced by health inspection.

The earlier independent status-sample gates do not serialize dispatch or result
publication. They remain useful denial/diagnostic checks, but the A1 protocol below
is now the authoritative S/Q/P order. An orphan obligation is durable in memory;
its detection requests Q in that protocol before caching SHUTDOWN_REQUIRED.

The supported joint runtime remains CPython 3.11/3.12 with original frame/opcode
metadata and native RLock recursion semantics. Python 3.13 is not newly enabled.
Finite signal/concurrency tests do not prove every schedule. Arbitrary private
state/interpreter mutation, replacing signal handlers to ignore interruption, or
calling SQL directly from hostile host code is outside the trusted-host boundary.
Repeated interruptions can prevent housekeeping/diagnostic delivery, but cannot
clear a prepublished unresolved obligation through any consumer interface.

Fresh subprocess tests inject actual SIGINT and SystemExit in both cleanup orders,
with further interruption immediately before/after terminal-latch publication.
They prove within-process same-thread and cross-thread denial, original/copy
checkpoint denial, principal/grant issuance denial and replacement-authority
rejection while native depths remain stranded. They use no manual unlock or
product recovery. Process exit occurs only after these assertions. Genuine PG
cases use original grants and the accepted SELECT-only reader, separately from
setup writes. Operational trusted actual-label integration remains BLOCKED.

## Explicitly authorized A1 ordering (supersedes status-sample admission)

S is an irreversible dispatch claim; Q is process-wide terminal quarantine;
C is physical driver.connect; P_READ/P_EVAL are protected successful-result
publications. **S < Q < C is permitted**, for one exact previously approved read.
Q < S and Q < P are forbidden. P < Q < Python return is permitted. There is no
physical-connection cancellation, external-delivery fence, receiver or ACK API.
Publication means admission of the original immutable outcome into protected host
metadata, not its eventual Python return or observation by a recipient.

`src/providers/_forecast_read_order.py` retains one private process journal shared
by every reader/label authority. An immutable node links to at most one successor.
The actual linearization is the native exact-builtin `dict.setdefault(0, node)`
that inserts that successor. All keys are exact integers, never objects with user
hash/equality callbacks. All nodes, event contents and release-independent owning
scopes exist before insertion. The cursor is a hint only: a delayed writer follows
immutable links and cannot overwrite or bypass a winning Q. I (original principal,
grant or joint-checkpoint activation), S, P_READ, P_EVAL and Q use this SAME protocol. No cached
status sample or acknowledgement of insertion is authority.

Q first records permanent shutdown intent, then appends Q. If interrupted after
intent, any subsequent metadata operation helps append Q instead of succeeding.
The Q insertion is the terminal linearization point; intent alone is not falsely
reported as a completed Q. A second Q publisher observes the same terminal node.
No reset exists. An interrupt before intent creates no fictitious Q; an orphaned
native-lock obligation still cannot disappear. The journal has a 65,536-node
lifetime budget, including I/S/P/Q. Exhaustion forces Q, requiring clean process
restart, rather than unbounded memory or recycling authority. Original activation
maps are likewise lifetime-bounded by this budget.

This narrow native atomicity assumption is **GIL-enabled CPython 3.11/3.12 only**,
with protected interpreter/extensions (including native dictionary watchers),
source/modules and host-private state. Exact PyDict_SetDefault, integer hash and
lookup do not invoke Python callbacks, release the GIL or poll Python signals in
the reviewed native insertion region. Allocation/proposal preparation and Python
signal/trace/profile boundaries may interrupt BEFORE or AFTER it. An interrupted
acknowledgement can lose a successful local return but never undo the committed
node. This is not a portable Python language guarantee, free-threaded runtime,
malicious-extension sandbox or claim of CPython 3.13 evaluator support. Both the
new A1 metadata gate and existing joint-runtime gate fail closed outside the
allowlist. Healthy standalone compatibility is tested on 3.11 and 3.12; standalone
reads need no labels or joint debug-position metadata.

### Claim ownership and exact provider use

The trusted original reader factory registers a private, opaque operation origin,
pinning its original state/provider and fixed service owner code. The evaluator
factory independently pins the original reader and synthetic label state. Only
protected bootstrap holds the registration credential; consumers receive neither
origin registries nor credentials. Constructing/copying/deserializing declarations
cannot register an origin. This assumes the same protected-host/private-state
boundary as original grants, not adversarial access to arbitrary Python globals.

The original service enters a fixed-code, thread/stack/exception-region owning
scope BEFORE claim registration. No authority exists during that context's return
handoff. Its exact reviewed with-body must remain live; a retained traceback or
same-frame resumed code outside that body does not make it live. There is no
security reliance on GC, weakref finalization or caller assignment. A normal exit
closes and prunes it; interrupted pruning can leave physical metadata, but never
valid orphan authority outside its original owning scope.

States: UNCLAIMED -> CLAIMED at S -> IN_FLIGHT on original one-use consumption ->
FINISHED on provider unwind. Any failed/unwound ownership or expired unconsumed
claim is ABORTED. S is permanently spent even if the handle return is interrupted.
There is no retry/renewal/reset path. Original read grants retain their accepted
repeatable-before-expiry behavior; a separate read call must obtain a new S while
healthy. A consumed S cannot obtain an additional lookup even when its grant is
still valid.

Checkpoint registry publication remains owned before activation; a pending record
cannot validate unless its original I activation committed before Q. An interrupted
activation cannot leave a usable orphan. S is appended inside original authority locks, after original callback-bearing
checks and live-record validation. Joint requests additionally hold ALL original
clock locks then authority locks in their existing identity order; fresh original
clock samples and both original grants/principals are checked without callbacks.
Standalone requests retain their accepted clock observations, with callback-free
pinned-domain sampling where supplied. The one additional standalone admission
executor uses the original interrupt-safe ledger (20 old sites retained, 21 total).
No new lock is introduced by the journal. No SQL or callback runs in S/P insertion.

The claim binds original origin/provider, principal/grant record identity, canonical
selection, tenant/dataset/COMPLETE ordered series scope, original idempotency and
expected content digest. Its consumption deadline uses captured builtin monotonic
plus the approved remaining grant/principal budget (at most 30 seconds); the scope
must still be live. A claim expires conservatively and cannot extend original
permission. `ForecastReadProvider.lookup_claimed` verifies the original registry
identity/provider/idempotency/scope, burns a native single-use marker BEFORE its
one original lookup, and marks completion in finally. Foreign/copied/new handles,
wrong scope/idempotency and repeated consumption reject before storage. There is
no new SQL, connection or writer object. Legacy provider.lookup is retained as
host-private infrastructure compatibility, never exposed as consumer authority.
The original reader consumer path always uses lookup_claimed.

Once S commits before Q, only that immutable one-shot SELECT-only operation may
start C afterward. It creates one fresh connection and transaction, never a retry,
additional lookup, scope substitution or new authorization. Existing driver
connection/read deadlines and finally closure remain unchanged. No journal guard
is held during C, SQL, callbacks, computation or external code. Q never waits for
an authority lock, an admitted query or native cleanup. Read completion after Q
cannot authorize P. In-flight data is discarded through the denied outcome.

### Verified publication

Original reader verification and original evaluator scoring/final paired grant
cutoff prepare the immutable result outside coordination. A private fixed-owner
scope validates the outcome type and exact expected batch identity, burns a
one-use publication marker, prepares its content hash, and appends P_READ/P_EVAL
through the same terminal journal. The host record binds original origin, provider,
operation/grant identities, original canonical selection and immutable result
SHA-256. Consumers cannot submit results to this operation through either public
value-return API. A copied result or digest is data, never publication authority.

Q winning first denies publication. P winning first is a genuine publication even
if Q or a signal follows before assignment/return. Such a signal still propagates;
it is not converted into ordinary success. No further health check retracts an
already-published value. Any earlier callback-bearing/final authorization denial
still prevents P. B5-2 uses only its original reader VERIFIED_RECORD_SET and has
one public verified-result path, the original fixed evaluator publication scope.
No reader/evaluator publication is inferred from detached dictionaries.

Finite deterministic barriers and actual SIGINT/SystemExit at native call/return,
claim consumption and scope closure test the documented boundaries. Six-thread
claims/quarantine, concurrent Q publishers, journal exhaustion and orphan-scope
rejection are bounded tests, not a proof for arbitrary schedules or repeated host
termination. Unsupported metadata denies before dispatch. There is no distributed,
crash-durable, fork/reload recovery or external-delivery guarantee. Clean exec plus
fresh protected authority is required after terminal quarantine. B5-3 consumers,
production IAM, trusted operational labels, durable audit and delivery contracts
remain separate milestones. Operational actual-label integration remains BLOCKED.
