# R7-04B2 — read-only source snapshot to original-owner executable inputs

B2 produces immutable, validated input data only. It never loads/fits/predicts a
native model, issues runtime execution permission, persists a forecast or changes
production state. It is not wired to API, Celery or scheduler entrypoints.

## Architecture and exact read path

    Existing private local catalog and Parquet chunks
      -> ReadOnlyDatasetRepository
      -> existing CanonicalTrainingDataProvider
      -> existing CanonicalRuntimeSnapshotService (R7-02)
      -> OwnedInputPreparationService
      -> allowlisted preprocessing adapter
      -> existing FrozenTabularPreprocessor / actual M3 TabularOutput

No accepted implementation is changed. The new repository reuses the accepted
candidate/publication resolver, Parquet codec, envelope/mapping verification and
quality-evidence semantics. It replaces three write-producing reader mechanisms:
SQLite schema/PRAGMA initialization, disk snapshot spooling and the temporary SQL
quality-key index. Existing writers, providers and publication purposes retain
their accepted behavior; no new canonical dataset or publication authority exists.

ReadOnlyDatasetRepository requires an existing private owner-controlled root,
original dataset reference and complete original series set. Every root component,
existing writer lock, catalog, attempt directory and chunk is opened without
following symlinks. Missing storage, unsupported scope, recovery journal/WAL/SHM
files or invalid schema fail closed. No file, directory, lock or database is
created. The shared lock is acquired through an existing read-only descriptor;
cooperative writers' exclusive locks exclude changes during each capture.

SQLite uses a pinned existing file descriptor and URI mode=ro. A read transaction
provides one catalog view; no COMMIT or write PRAGMA is issued. The authorizer
rejects writes, PRAGMAs, attachment and schema operations. The exact accepted read
queries are allowlisted. Chunk ordering is performed on a bounded in-memory list
rather than SQLite sorting/temp storage. Canonical bytes are verified completely
before visibility and retained only in bounded RAM; quality counts use a bounded
in-memory index with the accepted duplicate-count semantics. No SQL is in services.

Provider authorization, admission purpose, publication/revision binding, quality
checks and original release/value attestation all remain active. Content digests
are integrity checks, not read authority. This implementation supports Linux local
storage and its cooperative lock protocol; it does not implement PostgreSQL runtime
retrieval, network filesystems, crash recovery, or protection against a privileged
host rewriting files. Full-version integrity/physical-dtype admission precedes the
accepted origin filter, exactly as in R7-02. It is not a new SQL history-pushdown API.

## Original owner, requests and actual transformations

The original M6 trainer retains an opaque CandidateReference, immutable metadata
and the fitted preprocessing state. The bridge requires that original live tuple,
not a copy, reconstructed reference, M7 manifest, B1 envelope or public digest.
Original-owner discard revokes subsequent preparation and data-handle retrieval.
The model object itself is never accessed. B2 works with retained M6 references
without claiming they possess a native artifact: B1 verification remains separate.

ExecutableInputRequest v1.0 binds candidate/family, frozen state, compatibility,
exact R7-02 request and snapshot ID, controlled-offline input purpose and budgets.
The nested accepted request binds organization/dataset/series, publication,
origin/cutoff, H1 geometry, feature schema and source availability policy. B2
requires the original publication and complete original series set. It does not
broaden to new datasets, tenants, arbitrary subsets or future origins.

Only original validation/test origins at or after model_fit_cutoff are supported.
At most 32 series fit the accepted single M2 batch. Insufficient history, changed
schema/order, missing static context and unauthorized covariates reject rather
than fabricate context. The original state/schema/vocabulary identities remain
unchanged. Request ingress is capped at 256 KiB. Prepared output defaults to
128 KiB (ceiling 512 KiB), elapsed defaults to 30s (ceiling 60s); source capture
retains its own accepted limits, with at most 16 MiB canonical bytes in RAM and
an 8 MiB estimated quality-index ceiling. Limits reject, not truncate.

The host supplies authorize_inputs(metadata, request, stage) for admit/return,
plus the original source authorities. Exactly True is required. The service pins
a request-local instance of the existing provider to the read-only repository,
preventing a changed original provider.store from activating the writer path.

First capture independently obtains the authorized snapshot and checks its exact
identity. The adapter rehydrates the accepted observation/request/exogenous row
interfaces from that snapshot, with an explicit supported physical-dtype
allowlist and lossless checks. It preserves historical/H1 release evidence,
measurement context and per-series dimensions. It does not synthesize features.
The unchanged M3 frozen kernel performs the actual canonical feature execution;
no fit/scaler/vocabulary routine runs. Output is bound to the complete M2 tabular
declaration, original frozen M3 state and snapshot identity as historical evidence.

Physical columns retain M3's ordered per-feature dtypes (the test fixture uses
int8, float32, int8). native_ingress_dtype=float32 describes the separately
validated homogeneous ingress, not a claim that every physical column is float32.
Accepted D2 strict_float32 checks raw values, Boolean rejection, exact conversion,
signed zero and its bounded magnitude policy; no lossy rounding is introduced.
Unknown-category handling remains the frozen canonical policy, not recoding.

Initial supported policy is lossless_float64 source conversion and explicitly
supported object/numeric/timezone physical dtypes. Other physical encodings or
numeric-conversion policies fail closed. Non-external known-in-advance/lagged
covariates can use the existing M3 path. external_forecast is UNAVAILABLE: the
unchanged M3 frozen kernel has no external-forecast authorization injection point.
R7-02's separately authorized source values cannot replace that missing execution
policy. No accepted authority semantics are relaxed to manufacture support.

## Model-agnostic selection and owned data

InputAdapterSelection v1.0 is derived from validated original M6 family,
representation and exact frozen-state class/identity. A fixed immutable allowlist
implements only canonical_m3_xgboost_inputs_v1. Shared orchestration delegates
context validation and transformation; it contains no family-specific feature
algorithm. No filenames, MLflow aliases, arbitrary import strings, plugins or
production resolver participate.

| Family | Accepted contract foundation | B2 adapter |
| --- | --- | --- |
| XGBoost | M2 tabular / frozen M3 | Implemented, within limits above |
| LightGBM | M2 tabular / frozen M3 | UNAVAILABLE |
| LSTM / GRU | M2 recurrent / frozen M4 | UNAVAILABLE |
| Transformer | M2 grouped inputs / frozen M5 | UNAVAILABLE |

Unknown or mismatched families are DENIED; supported declaration families with no
adapter are UNAVAILABLE before capture. Future adapters can reuse the same
repository/provider path and delegate their own geometry without modifying the
shared Predictor or accepted M2-M9 declarations. No universal native artifact
contract or runtime model registry is introduced. B1's XGBoost envelope is separate.

PREPARED_INPUTS returns an original, immutable process-local OwnedInputEnvelope
containing actual M3 output and a versioned canonical ExecutableInputBinding.
Binding identity includes request, validated selection, output and representation
IDs; runtime_permission and production_permission remain False. DENIED/UNAVAILABLE
return no envelope. Public declarations or fabricated outcomes confer no ownership.
require(envelope) validates original issuance, unchanged data and live candidate
ownership. It is not a source-freshness check or model-use capability. Up to eight
envelopes per service are retained; there is no durable store or restart recovery.

## Terminal validation and trust limits

The second accepted R7-02 capture must match the first byte-for-byte and match the
requested identity. Outer cancellation/deadline checks are composed into the
request-local capture clock: source clock and outer clock callbacks both complete
before a live original-owner lookup. Exact elapsed >= limit rejects. Final source
approval/attestation remains the accepted sequence; no extra host callback runs
after terminal capture before final owner validation and envelope issuance.
Failures return fixed reason codes and no partial data handle. Cancellation,
source/owner denial, changed snapshots, closed stores and resource failures do not
populate the issued-handle map.

All of this is trusted-host, process-local composition. Source authorities and
provider checks are sequential, not a transaction across authorities or an atomic
revocation lease. Callback results attest their checked point, not perpetual
permission; a malicious host or callback that rewrites other authorities is not
an authentication sandbox. Clocks/callbacks are cooperative and not preemptible;
no hard in-process RSS/deadline guarantee is claimed. The disposable test container
provides separate wall-time, memory/CPU/PID and network bounds.

## Verification and remaining prerequisites

Run the explicit B2 module in the accepted disposable pinned B1 test image:

    python tests/native/run_b1.py tests/runtime/test_owned_executable_inputs.py

Do not invoke that runner without an explicit B2/non-native test selection: its
B1 default performs native experiments, which are not authorized in B2.

B2 fixtures create/finalize synthetic local publications and frozen states before
runtime write/fitting traps. Original M6 references are issued with estimator
stubs; no genuine model fitting/loading is performed. Actual R7-02 capture and M3
frozen transformation are retained, including a four-series/two-store/two-department
case with overlapping periods. Runtime tests trap filesystem writes, non-read-only
SQLite connections, fitting and native/registry imports; they compare storage
bytes/mtimes before and after success/failure. They test missing/corrupt storage,
symlinks, SQL write rejection, scope substitutions, exact numeric rejection,
terminal revocation/deadlines and future-family fail-closed behavior.

R7-04B3 must combine the same original owner across B1 native material and B2 input
handles, establish independent scoped load/execute authority and terminal
revocation/consumption, and perform separately authorized genuine numerical tests.
A B2 PREPARED_INPUTS outcome is not execution permission or native compatibility
attestation. New publications/origins and richer external/encoder context need
separate authorized compatibility work. M7 remains TEST_ONLY_NON_DEPLOYABLE /
NO_MODEL_PAYLOAD; R7-03/04A deny-only contracts and R7-05A structural-only inspection
are unchanged. No persistence, production eligibility or Phase 7 closure is claimed.

## Recorded verification

The complete B2 module passed 51 cases in one final invocation. Full M2 contracts
plus the non-native B1 stub-owner rejection passed 236 cases. Full M3 plus selected
M6 ownership/isolation/checkpoint regressions passed 51 cases. Selected R7-02,
R7-03 and R7-04A capture/authority/revocation regressions passed 19 cases.
Total: 357 distinct executed repository cases; overlapping exploratory reruns are
not added. Native/MLflow imports were rejected before pytest execution in each
final invocation. The accepted B1 native 53-case evidence is reused, not rerun or
included in the executed count; B2 authorizes no native fitting/loading.

An additional bounded proof returned a real 4x3 M3 output for four overlapping
series: ordered location/lag1/promotion columns with int8/float32/int8 physical
dtypes, and per-series lag values 32, 132, 1032 and 1132 (original UUID ordering).
Both authorized captures matched; 30 traced SQLite statements were SELECT/BEGIN
only. Source file sizes, bytes and mtimes remained unchanged. Runtime permission
was False and native operations were trapped. This probe is not an extra pytest
case or an execution-authority attestation.
