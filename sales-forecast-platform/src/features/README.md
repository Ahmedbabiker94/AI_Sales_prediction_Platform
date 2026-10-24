# Additive canonical feature foundation (R-04B)

`CanonicalFeaturePipeline` is deliberately separate from `TreePreprocessor`,
`feature_pipeline.py`, `Predictor`, and active training. No existing model is
assigned the new semantics. There is no model loading, fitting, SQL, network
request, dataset admission, or registry operation in this engine.

## Input and trust boundary

Use the existing bulk Observation fields, with `organization_id` supplied from
the authenticated asset context and configured dimension columns joined from
verified SeriesDefinitions. Required scope columns are `organization_id`,
`series_id`, `metric`, `unit`, `currency`, and `frequency`. History also requires
aware `period_start`, `period_end`, `available_at`, `value`, and all configured input
columns. `available_at` is additional feature-boundary provenance, not a new
Observation field. R-04C must supply trustworthy time-of-knowledge evidence and
must not automatically equate it to period completion or extraction timestamps.
Requests contain scope and dimension fields; target periods are generated.

Input UUIDs, metadata, configuration, fitted state and partition references do
not prove authorization. R-04C must obtain admitted canonical versions through
the authorized provider, bind their metadata, validate trusted model signatures,
and own the reader lifecycle. This module does not implement that handoff.

## Executable semantics

Existing R-01 declarations deserialize unchanged with `execution=None`. They
cannot execute implicitly. New execution requires configuration major version 2,
explicit FeatureExecutionRules version 1.0. The default executor is 1.0.0;
explicit versioned numeric policies select executor 1.1.0.

Supported calendars are Gregorian daily or weekly, with an IANA timezone and an
explicit weekly weekday anchor. Periods are half-open local-midnight intervals:
daily/weekly end is one calendar day/week after start, including DST changes.
Naive dates, monthly/business/fiscal calendars and ambiguous or nonexistent
calendar transitions reject. Calendar calculations explicitly select start/end.
The forecast origin is the first target period's start, after completed history.

Period lags require a complete calendar grid from the earliest supplied history
to the origin. Observation-count lags can explicitly allow gaps, but records must
still have valid period bounds. No missing period becomes a zero observation.
Rolling offset 1 excludes the target; window, min_periods and std ddof (0 or 1)
are mandatory selections. Warm-up outputs use declared reject/zero/nan behavior;
minimum history and cold-start behavior are separate explicit rules.

Both prepare_training and prepare_inference call the same pure computation.
They support one shared origin per call. Historical training/backtests must use
state fitted no later than that origin; multi-origin orchestration is R-04C work.
Actual rows ending or becoming available after the origin never become historical
features. The supplied fit partition must also be available by its cutoff. For a
multi-horizon call, supply canonical-shaped prior predictions for all preceding
horizons. Predictions are separate from actuals and become eligible only before
their subsequent target period. There is no model invocation or DB history reset.

Fit_state learns lexically sorted ordinal vocabulary only from its supplied,
cutoff-bounded training partition. It never learns on transform. Unknown/missing
codes must be explicit, distinct negative integers, or reject. Identity dimensions
cannot be null; nullable categorical auxiliary inputs can use a missing code.
The state binds configuration digest/version, full input dtype descriptors,
organization scope, cutoff, partition reference and immutable vocabularies.

## Exogenous availability

Exogenous input is a separate long table with organization/series/metric, field,
value, period_start/end, and available_at. Duplicate revisions reject. Known-future
inputs require availability by the original origin. Observed-only inputs cannot
be used for a future target. Lagged inputs select a declared earlier period and
must have completed and become available by the origin. Prior target predictions
never manufacture future actual exogenous observations.

External forecast inputs additionally require an opaque evidence_reference and
an injected trusted verifier approving the exact scoped ExternalFeatureRequest.
No token acquisition/network transport is supplied. Callback failures, including
internally typed errors, are sanitized. A declared availability class or timestamp
alone does not authenticate the data; authoritative provenance is R-04C/deployment.

Missing exogenous data rejects unless a zero substitute and its documented
configuration reference are explicitly selected. Numeric missing cells and null
categories have distinct rules. Infinity always rejects. No field-name guessing,
sign-based returns classification, silent currency inference or anomaly repair.

## Precision, compatibility and budgets

Default numerics must be losslessly representable as float64; otherwise reject. This
includes rejecting Decimal('0.1') and non-representable large integers rather
than silently rounding them. Exact supported signed/zero inputs are unchanged;
rolling calculations use explicitly versioned float64 arithmetic. No target
transformation is applied to caller frames. Ordered outputs have declared dtypes;
integer overflow and unsupported coercions reject.

FeatureSchemaCompatibility embeds the full configuration and binds executor,
training schema and fitted-state digests. Descriptor version 1.1 requires an
explicit fixed forecast horizon (consecutive periods, using the configured
frequency/calendar and recursive strategy); there is no horizon range or legacy
descriptor fallback. Describing and verifying require ForecastConfig and an
aware, aligned origin no earlier than the fitted-state cutoff. The origin is
request-specific, so changing it does not change a model's horizon capability.
Verification additionally requires the exact requested organization/series,
metric, measurement and dimension contexts. It checks one row for each generated
period per requested series, in executor order (sorted organization/series, then
ascending periods), rejects missing/extra series or periods, and validates
projected dimension and period columns against the request. UUID/dimension
identity authenticity remains the trusted R-04C integration's responsibility.
It checks exact ordered names/dtypes;
it is not evidence of model provenance, dataset admission, or feature-value
authenticity. Absent legacy signatures do not receive a compatibility fallback.
The existing frozen 32-feature reference and production artifacts are untouched.

Budgets bound rows, columns, series, history span, categories, horizons, cells,
calculation count and estimated combined window/category work, configuration/state
bytes, input frame accounting and output
matrices. Known-future boolean inputs require a declared compatible output dtype.
This is bounded in-memory processing,
not distributed streaming or a hard process RSS/CPU cap. Future supervised training
must enforce deployment limits and cancellation. Engine computations have no
POSIX locking dependency; dataset staging remains Linux/WSL-only and must not use
OneDrive/synchronized roots. Native Windows engine behavior has not been tested.

## Authorized single-origin preparation (R-04C1)

`CanonicalTrainingPreparationService` accepts only an opaque FINALIZED reference
through the trusted canonical provider. It has no CSV fallback, model loading,
model fitting or active entry-point integration. `open_read` exposes detached,
version-bound mapping and SeriesDefinition metadata after full integrity and
admission verification. Existing `read_batches` consumers retain their generator
interface. Normal read-session exit requires complete consumption; preparation
owns iterator closure and releases the snapshot/store lock before transformation.
Fresh read/admission authorization is checked after consumption and before return.

Preparation supports one historical origin and **horizon one** only. It fits
category state on the explicit past partition ending no later than that origin,
binds its partition reference to the dataset/admission/configuration/cutoff/series,
and separates an actual, completed target-period label from feature history.
Incomplete labels reject. Multi-origin backtesting and recursive supervised
training remain R-04C2 prerequisites; future actuals never supply predictions.

Source mappings must preserve `available_at` and `availability_reference` auxiliary
fields. Exogenous rules must explicitly declare their availability/evidence field
names. A trusted verifier independently validates the scoped claims; timestamps,
hashes and self-declared references alone are not authority. External forecast
approval receives both dataset preparation context and the scoped feature request.
Availability and external approvals are checked again before result handoff.
These injected verifiers and authorization functions are trusted application code,
not a Python security sandbox or implemented tenant authentication system.

## Temporal supervised preparation (R-04C2-A)

`CanonicalTrainingPreparationService.prepare_temporal(plan, configuration)` is an
additive, model-independent **horizon-one** API. It accepts a versioned immutable
`TemporalPreparationPlan` with explicit `TemporalFold` declarations and exact
FINALIZED dataset/version, manifest/admission/mapping/policy/series-definition
bindings. These declarations do not establish authorization. Trusted callbacks
receive the complete plan and verified organization context; every dataset must
already be admitted for the exact `production_training` purpose. Diagnostic or
evaluation permissions are not substituted for that purpose.

Only expanding history, `reject_overlap`, and zero embargo execute. Rolling,
purging, and nonzero embargo declarations reject; no split option is ignored.
Higher horizons can be structurally declared with `capability="planning_only"`,
but do not execute or imply verified model capability. R-04C2-B owns predictor-
dependent recursive rollout and any higher-horizon feature/label evaluation.

Each fold explicitly declares ordered training/validation/test origins, the
calibration interval `[calibration_start, C)`, model-fit cutoff M and assessment
cutoff E. C must precede or equal the first training origin; M must follow the
training origins and must not follow the first held-out origin. Target periods
begin at the forecast origin O, and H1 labels describe `[O, advance(O, 1))`.
Existing daily/weekly Gregorian local calendar arithmetic controls period bounds,
weekday anchors and DST. No fixed-duration UTC interval approximation is used.
Plan timestamps support microsecond precision; finer precision and numeric epoch
declarations reject rather than silently becoming aligned origins. Cutoff order
is checked by UTC instants, including ambiguous DST hours, without rewriting the
declared timestamps. Stored canonical timestamp precision remains unchanged.

Each fold and organization fits **one frozen categorical state** on its complete
contiguous calibration prefix. All prefix targets and every time-varying category
must have trusted availability no later than C. Missing or unavailable prefix
records reject; they are not silently dropped. Static dimensions come exclusively
from verified SeriesDefinition metadata. State binds the exact dataset, plan/fold,
prefix, C, series and configuration/numeric policy. Later categories use the
configured unknown-category rule; validation/test never refit state. Previously
held-out origins may become training origins in a later fold only under its new
cutoffs; held-out event origins themselves cannot be duplicated across folds.

Features use only historical actuals completed and verified available by O.
Training labels may arrive after O but must be completed and available by M;
held-out labels must satisfy E. M/E cannot assert future availability beyond the
trusted provider clock. A later origin may use an earlier actual only once its
release is verified. An unavailable historical interval still rejects according
to the existing engine's gap policy. Label eligibility never supplies future
actual targets as predictions. Trusted verifiers must attest the actual stored
value/revision and field release, not merely a source-period timestamp.

One provider read per organization/version/request is fully consumed and closed
before transformation. Dataset locks and private reader snapshots are released.
The detached capture is reused only inside that request, never as an authorization
cache. Fresh publication/read and preparation approvals run after reading, at
fold boundaries, per origin, and before final handoff. Field/external evidence is
rechecked. A final cancellation/deadline checkpoint follows the final callbacks.
Final availability and preparation approvals retain every original fold/origin
context and target-period bound; a global approval cannot replace them. Calibration
category evidence is independently rechecked at C, and external forecasts retain
their original scoped request identities.
Calibration, external, global and read callbacks finish before the terminal
per-origin checks; only the cancellation/deadline checkpoint and return follow.
Any late fold, callback, integrity, cleanup or cancellation failure returns no
temporal result and does not modify FINALIZED publication state.

Results retain original labels and return detached matrix/label copies. Example
identity is `(fold, organization, series, metric, origin, horizon_step, target)`;
the same target in a different fold is a distinct forecast event. Result groups
bind partition, numeric configuration, state and schema IDs and dataset evidence.
The aggregate matrix is a grouped view: different fold/organization states must
not be silently treated as one pooled model-fitting contract.
Rows follow declared fold order, organization order, partition/origin order and
then the existing executor's series order; the index is an identity, not a claim
of global lexical sorting.

Request-wide limits cover organizations/versions, series, folds, origin executions,
captured rows/decoded bytes, output cells, serialized fitted states and measured
frame-copy/assembly work. Per-execution FeatureBudgets remain enforced. Work-cell
and assembly-byte counters estimate declared frame processing; they are not hard
RSS/CPU limits, nor a complete accounting of Python/pandas overhead. Returning
copies also consumes caller memory. Linux/WSL on an unsynchronized approved local
filesystem remains the storage environment. Native Windows/OneDrive execution,
hard process controls, real authentication and production tolerance approval
remain deployment responsibilities. No model, registry or runtime cutover occurs.

The default numeric mode remains `lossless_float64` / STRICT_EXACT, preserving
original strict descriptor serialization and executor 1.0.0 identity. Explicit
`versioned_float64` configurations use executor 1.1.0 and require one immutable
NumericConversionPolicy for the target and each numeric covariate. Modes are
STRICT_EXACT and BOUNDED_FLOAT64. Covariates require declared measurement scope;
identifiers/categories cannot receive these policies. Bounded conversion uses
exact rational error comparison against the original Decimal/integer:
`error <= absolute_tolerance + relative_tolerance * abs(original)`.
Finite IEEE nearest-even conversion is allowed only within the configured bound;
overflow, nonfinite values and nonzero underflow-to-zero reject. No units conversion,
clipping, sign reinterpretation or fixed-scale conversion is implemented.

No universal or production tolerance is approved. The trusted preparation
authorizer must approve the exact version/metric/fields/units/configuration digest
and tolerances. Test tolerances are explicitly synthetic. Training and inference
use the same conversion in the shared executor; the whole policy contributes to
configuration/state/schema identity. Original stored Decimal values remain intact.

Results store immutable tuples and serialized contracts; matrix/label accessors
produce new detached frames. The preparation ID binds version, admission, state,
schema, output values and original labels. Constructing a result is not permission
to train. Preparation bounds total decoded bytes (default 16 MiB), input rows,
series, availability/covariate entries, outputs and elapsed checkpoints. Assembly
is bounded in memory, with temporary copies; these are not hard RSS/CPU caps or
asynchronous process cancellation. Linux/WSL unsynchronized storage restrictions,
snapshot retention and production authorization deployment requirements remain.

## R-04C2-B1: unreviewed model-independent rollout foundation

`ForecastEvaluationPlan` and `PredictorCapabilities` are immutable versioned
operational declarations. They do not authenticate fitted-model provenance or
make a Python callback deterministic. The trusted application composition must
supply a deterministic synthetic predictor and trustworthy availability/revision
verifiers. Callbacks are ordinary Python code, not a security sandbox.

`DeterministicForecastRolloutService.rollout()` is additive and unwired from
active training/serving. B1 executes only `recursive_one_step` + `tabular_step`.
Direct-horizon, sequence, canonical-history and statistical declarations do not
activate executors. No legacy feature schema is imposed on future model families.

Evaluation requires a separately admitted immutable publication with exact purpose
`offline_forecast_evaluation`. Purpose vocabulary v1 adds that value alongside
`production_training`; existing publication evidence is not relabeled. Both use
the established quality/admission/consistency/integrity engine and trusted scoped
callbacks. Production training access is not blanket evaluation access, and the
reverse is also prohibited. B/C consistency policy is not relaxed for evaluation.

For each fold and organization, the service fits one categorical state on the
complete calibration prefix at C, with each category's own verified release.
Static dimensions come from verified SeriesDefinitions. Histories are filtered to
completed observations verified available at the original O. M and E bound complete
training/held-out target intervals. No labels are retrieved for scoring or passed
to predictors. Revisions require trustworthy historical evidence; a finalized
latest-value dataset alone cannot establish point-in-time availability.

Each step recomputes and verifies the complete k-period feature prefix at fixed O,
using only validated previous predictions in private origin-specific feedback.
The current-step matrix is independently checked against its keyed request.
Total rollout H, prefix k, all prefix-schema identities, frozen state,
configuration and numeric policy are bound separately. Keyed finite float64
predictions must exactly cover the requested series and current target periods.
Booleans, implicit integer/Decimal conversions and nonfinite outputs reject.
Negative and signed-zero outputs remain intact. An explicit capability magnitude
bound may restrict the finite float64 range. Original canonical values are not
changed; existing numeric conversion rules apply to detached feature views only.

Prediction events retain exact dataset/version, manifest/admission/mapping/policy,
plan/fold/scope, original O, target period, predictor and representation identities,
plus recursive parents for the same series/origin. Generic direct events have no
invented recursive parents, but direct execution is unsupported. These in-process
contracts are not a generalized sequence serialization or network protocol.
Different immutable datasets have different content-bound IDs even if their
causal feature views happen to be identical.

One bounded verified capture per version is fully consumed and successfully
closed before feature or predictor work; storage locks are released. Failures
return no partial result. Final ordering is calibration/external/global/read
verification, then result binding/assembly, then exact original per-origin
availability and preparation authorization, then the cancellation/deadline check
and return. There are no broader callbacks after the terminal scoped phase.
Sequential scoped checks are not an atomic authorization snapshot: concurrent
revocation, or a callback deliberately changing another scope after it was checked,
requires deployment-level policy snapshot/lease semantics. Trusted callbacks must
not be used to claim stronger freshness than this cooperative boundary provides.

Request-wide budgets cover captured rows/decoded bytes, folds/organizations/series,
horizons, predictor calls, prediction events, output cells, fitted-state bytes,
representations, private feedback, assembly estimates and repeated feature-prefix
work proportional to H(H+1)/2. Per-executor limits remain active. Accounting is not
a hard RSS/CPU/disk-isolation guarantee. Cancellation is cooperative; elapsed >=
budget rejects, including changes during terminal callbacks. Storage remains
Linux/WSL on an approved unsynchronized local filesystem, not Windows/OneDrive.

R-04C2-B2 owns authorized held-out label retrieval, baselines, scoring, overlap
aggregation and zero-denominator metric policies. Real model adapters/training,
production tolerance approval, artifact provenance, distributed execution, hard
process limits and deployment authorization integration remain separate milestones.
This rebuild requires independent acceptance before adoption.

## R-04C2-B2a: owned held-out scoring (unreviewed foundation)

`CanonicalForecastEvaluationService.evaluate(scoring_plan, producer_plan,
configuration)` owns one B1 synthetic rollout. It accepts no external events,
serialized receipts or approval flags. A private active-request owner records the
actual immutable B1 result before B1's terminal checks; replacement/replay cannot
start label scoring. The expected full grid is rebuilt from the authorized plan,
verified series definitions and existing calendar executor. Original calibration,
external and origin contexts survive the handoff. Private Python names and trusted
callbacks provide an application composition boundary, **not a sandbox** or fitted
model authentication.

The explicit versioned scoring plan selects validation/test origins and the same
immutable `offline_forecast_evaluation` versions. Production-training admission is
not evaluation admission. Mandatory trusted `authorize_label_use(context, phase)`
and `verify_label_revision(context, claims)` callbacks have no default approval.
Read permission alone is insufficient. The revision verifier must independently
attest the exact captured value identity, source record/lineage, evidence reference,
version and revision availability at the fold's E; declarations, timestamps and
checksums alone cannot establish that historical truth. B2a rejects unavailable,
ambiguous or missing labels, revisions later than E and E later than the trusted
assessment clock. Production verifier/clock integration remains a deployment
prerequisite. Cross-version truth reconciliation is unsupported.

B1 finishes before the separately authorized second complete capture. Both capture
costs count toward aggregate rows/bytes/work limits. Readers close and locks release
before scoring. There is no predictor invocation after label access. Final ordering
is original calibration/external checks, global scoring approval and publication
reauthorization, result assembly/binding, then original scoped history/rollout
checks and scoped revision/label-use/scoring approvals, followed only by the
cancellation/deadline checkpoint and return. Any failure returns no partial result.
Sequential checks are not an atomic authorization lease: concurrent revocation or
a trusted callback deliberately revoking a previously checked scope requires a
future policy snapshot/lease boundary. Cancellation is cooperative, not preemption.

The separate scoring arithmetic policy interprets canonical Decimal/integer labels
exactly and float64 predictions as their exact represented rationals. It bounds
input digits/exponents, operand/accumulator bits, events, groups and assembly work.
Immutable outputs preserve N, SAE, SSE, SAA, MAE, MSE and the exact RMSE radicand;
RMSE is represented as the square root of that radicand, with no hidden decimal
approximation. WAPE is SAE/SAA, explicitly undefined when SAA=0 (including SAE=0).
There is no percentage-accuracy metric or change to model-input conversion.

Default groups retain forecast origin and never pool folds, tenants, series,
horizons, metrics, units or currencies. Combining distinct origins requires an
explicit trusted approval of the versioned `forecast_event_weighted_v1` policy;
every unique forecast event remains represented and receives equal event weight.
No error-based event selection is supported. Pure scoring contracts/arithmetic
confer neither producer authentication nor permission to access labels.

The two new same-named test modules require pytest `--import-mode=importlib` when
collected together. Use bytecode/cache suppression. Hard RSS/CPU/disk limits and
real revision/authorization integration remain deployment responsibilities. B2b
owns baselines and any broader aggregation/evaluation; B2a introduces no model
fitting, artifact loading, registry or active pipeline integration.

Reproducible relevant-suite command from `sales-forecast-platform/` (using the
approved dependency environment's Python):

```sh
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$PWD:$PWD/tests/features:$PWD/tests/data/staging" \
python -m pytest --import-mode=importlib -p no:cacheprovider \
  tests/features tests/data/adapters tests/data/contracts \
  tests/data/processing tests/data/staging tests/training
```

The two explicit legacy test-directory paths preserve existing sibling-module
imports while importlib mode distinguishes the two B2a test module filenames.

### Opt-in authoritative captured-history binding (B2b prerequisite)

Historical target `AvailabilityClaim` now carries an additive, pre-conversion
`value_identity` and `value_identity_version=captured_history_value_v1`. Identity
uses exact Decimal (sign, digits, exponent), integer decimal digits or binary64
bits. Decimal scale, Decimal signed zero, float signed zero and numeric types are
distinct; Boolean, nonfinite and arbitrary object values reject. No conversion,
rounding or scoring policy is changed. Existing target claims retain these tuples
through their original scoped terminal verification; category/exogenous release
claims keep their existing semantics.

`ValueAttestedAvailabilityVerifier` is an opt-in adapter for the **existing**
`verify_availability` callback slot. Trusted composition must supply both the
existing release verifier and `verify_captured_values(context, claims)`. No missing
verifier or non-True decision has a fallback. The latter receives immutable
`CapturedHistoryClaim` declarations binding publication/version/manifest/admission,
source/mapping, tenant/series/metric/measurement, record/lineage/period/field,
original exact value identity, O, C, plan/fold and release evidence. A None O is
only an initial capture check, never historical eligibility. Each original scoped
check runs again at handoff, at the same point in the accepted callback ordering.
No repository lock is retained and no new late callback phase is added.

The value authority must compare against **independently held publication-bound
expected values and revision validity at the requested cutoff**. A hash recomputed
from a changed row, matching timestamps, a caller's identity declaration, or an
always-True callback is not authoritative evidence. A record with a later revision
must not be attested as the earlier value merely by retaining its old release
metadata. Future rows in the full publication are not eligible historical inputs
until period completion and verified release at O. Their presence in a claim tuple
does not authorize their use. Authorities must validate exact scope and revisions,
including calibration use at C, and fail closed for unsupported historical truth.

Legacy B1/B2a callbacks remain accepted within their previous trusted integration
boundary; they **do not satisfy the value-attested baseline prerequisite**. Future
baseline composition must explicitly require this stronger protocol and an
approved independent authority. No baseline is implemented here. Real authority
integration, policy freshness/atomic leases, concurrent revocation and hard process
limits remain deployment responsibilities. Trusted Python callbacks are not a
security sandbox. The synthetic fault test substitutes a detached value after a
legitimate complete verified read; it demonstrates captured-value attestation,
not a bypass of physical Parquet/manifest integrity or an external client exploit.

### C0 operator-owned baseline-ready evaluation composition

`CanonicalBaselineEvaluationService` is constructed **only by trusted application
bootstrap**. This is an explicit trust assumption, not Python authentication or a
sandbox. Bootstrap supplies a canonical provider, a fixed synthetic producer,
validated capabilities/configuration, read/feature budgets, authorization hooks,
and three `BaselineAuthorityBinding` declarations: release availability v1,
captured history value v1, and scoring-label revision at E v1. Binding IDs/versions
are operator audit references, never proof that an authority is authentic. Each
binding fixes the same exact approved dataset/version/manifest/admission/mapping/
policy/series scopes and `offline_forecast_evaluation` purpose. Missing authorities,
wrong protocols, contradictory scopes or incompatible representations reject at
construction, before reads or prediction. Expected values/revisions must come from
independent, operator-approved source/publication evidence.

The supported **untrusted request** interface is only:

```python
service.evaluate(scoring_plan, producer_plan, configuration, cancelled=cancelled)
```

Requests cannot supply a provider, verifier, wrapper, authority selector, registry,
rollout/evaluator, or trusted flag. Requests are fully revalidated and compared
with the operator's immutable scope/configuration/capability snapshots before
capture. Rollout/scoring request budgets may tighten operator-owned ceilings but
cannot raise them; read and feature budgets also remain bootstrap-owned. Unknown
attributes added to top-level request declarations by unchecked `model_copy`
reject. Authorities
are retained from bootstrap; modifying another legacy service or the bootstrap
argument container does not reconfigure the owned instances. An arbitrary caller
constructing their own service at Python bootstrap is not an authenticated client
of this service. Malicious application operators and arbitrary in-process Python
remain outside this boundary; deliberately installing an always-True authority
can still lie and is explicitly not claimed to be detected cryptographically.

C0 composes the real B1 and B2a classes and mandates
`ValueAttestedAvailabilityVerifier` for historical targets. It adds no reader,
feature arithmetic, prediction-event import or scoring pipeline. B1 closes the
single owned history capture before features/prediction. B2a finishes prediction
before its separately authorized scoring-label capture. Both retain their existing
scoped original-claim checks, purpose/admission binding, cancellation/deadline
checks, sanitized exceptions and all-or-nothing results. C0 returns directly from
B2a without adding callbacks after its terminal verification. Legacy B1/B2a APIs
are unchanged and remain release-only when their operator selects that old mode;
that mode is never selected by a C0 request.

There are **no baseline algorithms, baseline feature factory, grouping changes or
multi-producer comparison** in C0. Tests use a fixed trusted dummy predictor only;
they prove composition readiness, not last-observation/seasonal-naive correctness.
B2b owns those algorithms and their independent acceptance. Authentic external
revision integration, configuration/credential protection, process isolation,
atomic/concurrent authorization semantics and hard CPU/RSS limits remain deployment
responsibilities. No active runtime entry point is wired to this service.

### B2b trusted deterministic period baselines

B2b adds `BaselineDefinition` version 1.0 and a fixed trusted factory in
`features/forecast_baselines.py`. The supported algorithms are
`last_observation_v1` (period lag 1, no seasonal parameter) and
`seasonal_naive_v1` (explicit positive integer season S). S must be at most 4096,
the declared lookback and the configured history budget. There is no automatic
business season. A weekly S=52 means 52 calendar weekly periods; it does not
mean the same ISO week number in the preceding year. Unsupported declarations,
missing predecessor/seasonal reference, gaps and insufficient history reject.

Trusted application bootstrap can construct `CanonicalBaselineEvaluationService`
with `baseline_definitions=(...)`, the approved feature template and its existing
operator-owned release/history-value/label-revision authorities. In this mode
`predictor` and `capabilities` must be omitted. At most eight distinct immutable
profiles are constructed and retained; no dynamic imports, mutable registry or
request-supplied callback is supported. Ordinary requests use only:

```python
service.evaluate_baseline(baseline_definition, scoring_plan, producer_plan,
                          cancelled=cancelled)
```

The declaration must exactly match an operator-approved profile. Producer plans
must contain its exact generated configuration/capability identities and remain
independently authorized. `build_baseline_profile()` supplies declarations for
planning; invoking that pure factory or its adapter directly grants no data,
authority or scoring permission. The original fixed C0 `evaluate()` API remains
unchanged for its original bootstrap mode; it cannot bypass baseline selection.
Baseline mode permits one active evaluation per service, refusing concurrent or
reentrant requests without an implicit waiting queue. No cross-request snapshot,
feedback, fitted state or approval is cached.

Each profile has one float64 period-lag output. It inherits the template's exact
measurement, dimensions, calendar, sign semantics and target conversion policy.
Its identity includes the full approved template and baseline definition. Only
Gregorian daily/weekly start-anchored, gap-rejecting, cold-start-rejecting period
semantics are supported. Templates with exogenous/time-varying categorical inputs
reject rather than silently ignore availability rules. Unused static category and
rolling outputs are absent from the new lag-only representation; dimensions still
come from verified SeriesDefinition metadata. B1 fits the real fold-specific state
and verifies full causal prefixes at the unchanged original origin O. The callback
validates bound calculation semantics, schema, numeric policy, prefix and exact
keyed scopes, then returns that lag. B1 owns recursive feedback: last-observation
repeats the last actual; seasonal-naive uses prior predictions when t-S reaches the
same origin's forecast interval. Source records are never modified.

STRICT_EXACT remains the default. An explicit operator-approved target conversion
policy is retained unchanged in configuration/schema identity; no business
rounding or universal tolerance is introduced. Synthetic approximate-conversion
tests do not approve production tolerances. Negative values, zero and float64
signed zero retain their meaning/sign. Original canonical Decimals are unchanged.

All contributing historical targets remain value-attested against independent
operator authorities at O, including the real calibration partition. Recomputed
identities cannot authorize substituted values. B1 prediction completes before
B2a's separately authorized label capture and revision verification at E. There
are two complete authorized captures per dataset version, no per-origin rereads,
no serialized external-event admission and no second reader/scorer. Existing
terminal original-claim checks, broad-before-scoped ordering, final checkpoint,
cleanup, purpose authorization and all-or-nothing results are preserved. The only
post-delegation operation is releasing the composition's non-storage call lock.

Grouping-order normalization is deferred: changing event-id ordering can change
accepted group identities. B2b does not alter B2a identities or grouping behavior.
Earlier B2a aggregate admission, atomic authorization snapshots, independent real
revision integration and hard CPU/RSS controls remain separate operational work.
Trusted bootstrap is still an explicit assumption, not Python authentication;
callbacks are not sandboxed. Linux/WSL unsynchronized staging remains required.
B2c joint producer comparison, model fitting/loading, sequence/direct/ensemble
adapters, production serving and active runtime migration are not implemented.

### B2c-C1 shared, label-free prediction foundation

`CanonicalForecastComparisonService.execute(JointPredictionPlan, cancelled=...)`
executes exactly two operator-approved profiles: one fixed synthetic B1 candidate
and one fixed B2b last-observation or seasonal-naive baseline. Despite the service
name, C1 returns **only label-free diagnostic predictions**, not comparison scores,
labels, signed deltas or model-selection decisions. No active runtime is wired.

Bootstrap fixes the actual provider, candidate callback/capabilities/configuration,
up to eight baseline alternatives, independent release and captured-value/revision
authorities, authorizers and read/feature/rollout/joint resource ceilings. Requests
cannot replace any dependency, inject events or capture handles, increase ceilings,
register callbacks, or declare a trusted/completed seal. Baseline selection uses
exact operator-generated identities, not feature-column-name conventions. Neither
Python classes, private names nor hashes authenticate an operator or fitted model.

A request opens one complete authorized FINALIZED `offline_forecast_evaluation`
history capture per version. The existing preparation service's private read
lifecycle is shared, not duplicated. All batches must exhaust and close, including
snapshot cleanup, before projections, fitting states or prediction. Native read
authorization/checkpoints remain inside the existing provider's read lifecycle;
no storage lock is held during predictor or post-capture verification callbacks.

The coordinator freezes supported scalar records, explicit dtype descriptors and
verified publication metadata. Decimal digits/scale/sign, float64 signed-zero bits,
UUIDs and timezone-aware periods retain exact identities. Parameterized Arrow
Decimal dtypes retain their bit width, precision and scale. Mutable object cells
and implicit pandas categorical snapshots reject. Detached producer DataFrames
are materialized privately; no generic public capture constructor/input exists.
The full first capture may physically include future observations. These remain
coordinator-private and cannot enter a predictor's causal history before O.

The approved B1 executor performs all feature arithmetic and independent recursive
feedback. Each producer gets its own actual calibration-fitted state, with an
additional joint-plan/profile/capture partition binding. C/O/M/E, field-specific
category releases, exogenous availability, independent historical value attestation,
numeric conversion and full-prefix schema verification remain mandatory. Candidate
and baseline configurations, producer plans and state IDs may differ. Unsupported
direct, sequence/statistical execution and P>=3 reject without fallback.

The common grid is derived from verified metadata and explicit plans, excluding
producer-specific plan/state/schema IDs. It binds exact publication, fold/partition,
tenant, series/dimensions, measurement, calendar/timezone/anchor, O, horizon step,
target bounds and E. Both actual in-memory B1 handoffs must independently cover
that grid, preserve full producer provenance and recursive parents, and pass their
recorded owner/fingerprint checks. Serialized/replayed events are never seals.
Private phases are CAPTURING, PREDICTING, ALL_PREDICTIONS_SEALED and CLOSED.
The existing private B2a owner has a prediction-only seal operation; C1 never
invokes its legacy consume() transition into label use.
Any second-producer or late-verification failure discards the whole result.

Declaration geometry is checked before capture; observed rows, widths, bytes,
configurations, calibration/category sizes and H(H+1)/2 prefix work are admitted
jointly before either predictor. Shared runtime counters enforce rows/bytes,
assembly, work, state, prefix cells, predictor calls, events/lineage representations,
verification claims/calls/bytes and output bounds. Existing per-producer limits
also apply. One request-wide clock/checkpoint prevents resetting the deadline at
producer boundaries. Admission estimates and `usage_phase='before_terminal'`
counters are diagnostic observations; final callbacks remain runtime-charged but
cannot mutate an already assembled immutable diagnostic. These are cooperative
operation/byte budgets, not hard process CPU/RSS guarantees. C1 reserves no C2
label/scoring capacity and grants no label-use permission.

After both producers seal, original calibration/external checks and broad joint
and publication/read authorization precede diagnostic assembly. Then every original
producer/origin claim and rollout permission is reverified, followed only by the
final cancellation/deadline checkpoint and return. The wrapper only closes private
owners and releases its non-storage call lock. Calls are sequential per service;
concurrent/reentrant requests reject. No snapshot, approval or feedback survives
request closure. Future C2 must reuse the active internal ownership phase, never
accept a diagnostic returned by a previous execute() as a trusted handoff.

Trusted callbacks are not sandboxed. Sequential verification does not make policy
revocation atomic; concurrent changes or a later trusted terminal callback revoking
an already checked scope require a deployment-owned lease/snapshot boundary.
Production authority authenticity, protected bootstrap and hard limits remain
operator/deployment duties. Existing B1/B2a/C0/B2b public APIs and B2a grouping IDs
are preserved. Joint scoring, truth alignment, signed deltas, generic grouping
normalization, model fitting/loading, promotion and serving are outside C1.

### B2c-C2 trusted joint validation scoring

C1 `execute()` remains label-free. The opt-in `compare(JointComparisonPlan)` starts
one new owned lifecycle at the beginning; a prior diagnostic, serialized events,
labels, flags or handoffs are never accepted as inputs. Bootstrap must separately
fix a `JointComparisonPolicy`, a publication-scoped `captured_value_as_of_E_v1`
label authority, joint-comparison permission, per-producer evaluation/scoring
permission and common-label-use permission. Existing history authorities and
C1 authorizers are still mandatory. Python declarations do not authenticate those
authorities; protected operator bootstrap is an explicit trust boundary.

Only the fixed synthetic candidate and one approved Last-observation or
Seasonal-naive profile execute. Supported comparison is validation-only, with
expanding histories and the existing calendar/horizon/split checks. Test partitions,
replayed outputs and unimplemented representations reject. Existing contracts
allow one exact dataset version per organization, up to eight organizations;
multiple versions for one organization and cross-version/measurement pooling are
unsupported. Producer-specific configurations, numeric policies and real fold
category states remain separate; no universal tabular schema is imposed.

The private phases are CAPTURING → PREDICTING → ALL_PREDICTIONS_SEALED → LABELS →
SCORING → TERMINAL → CLOSED. Exactly one complete history read per version serves
both producers. Both owned handoffs must seal and independently cover the full
common semantic grid before any separate scoring-label reader opens. Then exactly
one complete authorized label read per version exhausts and closes successfully.
B2a and C2 share the private label reader and canonical alignment helper. All
predictor work precedes labels; no storage lock is held during predictor, revision
or post-capture scoring callbacks. Historical captures can physically contain
future rows, which stay coordinator-private and cannot enter causal history early.

C2 derives the validation grid from verified publication metadata and the common
plan. Exact original-origin events stay distinct, even when they target the same
observation. Every selected key must have a candidate prediction, baseline
prediction and independently verified canonical label revision at E. Unselected
history rows in a full label read are not extra forecast events. Missing,
duplicated, misassigned, late or unverifiable labels reject the entire request.
The trusted assessment clock must satisfy E <= now. The same immutable truth and
claim binding is used for both producers. Source values stay exact until the
approved model conversion; scoring always uses the original canonical truth.

B2a `score_point`, `group_scores`, statistics, exact rational arithmetic and group
identities remain unchanged. C2 feeds them deterministic semantic order separately
for each producer. Default grouping keeps fold, organization, series, measurement,
horizon and original origin separate. `forecast_event_weighted_v1` pooling across
origins is available only when fixed in the operator-approved policy before the
request. Generic B2a grouping-order normalization is still deferred. C2's new
versioned identity binds its plan/pairing/policy, common truth/revisions, separate
producer evaluations and deterministic semantic group comparisons.

`SignedRatio` is a bounded canonical reduced signed rational. Deltas are exactly
candidate minus baseline for SAE, SSE, MAE, MSE, and WAPE when defined. Both
zero-denominator WAPEs remain undefined with no delta. A denominator mismatch is
an integrity failure. RMSE is retained as B2a's exact radicand; there is no RMSE
delta, percentage-improvement claim, winner selection or promotion.

After bounded history capture, aggregate admission reserves the complete second
read, alignment, both score sets, groups/deltas, immutable serialization and final
verification before either producer callback. Original C1 row/byte/work/call/state
limits remain request-wide, including both captures. `JointScoringBudgets` adds
capture, truth, scored-event, group and arithmetic-work ceilings. Arithmetic units
are conservative bounded bit-square operation allowances, not CPU instructions.
Runtime charging continues through label reading, exact arithmetic and final
checks. One elapsed/cancellation budget covers the entire request. `usage_phase`
is before terminal checks; those checks remain charged after output assembly.
These guards are not hard process RSS/CPU isolation or forced cancellation.

Final ordering is calibration/external checks → broad C1/joint-comparison and
per-producer evaluation authorization → fresh publication/read authorization →
complete immutable result validation/accounting → every original producer/origin
history and rollout check → every common truth group's revision/label-use and
both producers' scoped scoring checks → last cancellation/deadline checkpoint →
direct return. Any failure discards the whole result. Private cleanup only follows;
no broad verification callback or mutable scoring operation follows the terminal
phase. Sequential checks still cannot provide an atomic revocation snapshot;
authorization leases, authentic external authorities and hard process controls
remain deployment-owned. No model fitting/loading, serving, operational access,
MLflow, automatic selection or further milestone is wired by this foundation.

### R-04D1: owned training-only preparation (no model fitting)

The opt-in `CanonicalTrainingPreparationService.prepare_training(plan,
configuration)` owns issuance of a `TrainingOnlyHandoff`. Trusted bootstrap must
fix an immutable `TrainingHandoffScope`, the publication provider, preparation
permission callback, a `ValueAttestedAvailabilityVerifier` with independent
release/value/revision authorities, and a separate `verify_training_labels`
authority. Ordinary requests cannot supply a prepared result, fitted state,
authority, numeric tolerance or claimed provenance in place of this execution.
Python types, scope IDs and matching hashes do not authenticate authorities.
Bootstrap and in-process Python collaborators remain trusted, not sandboxed.

Only separately authorized FINALIZED `production_training` publications enter
this path. The complete original temporal plan remains bound in the output,
including held-out origins, C/O/M/E cutoffs and overlap checks. Only declared
training origins execute, with H1 supervised examples and expanding history.
Validation/test matrices and labels are not prepared or exposed. Future target
actuals are excluded from the feature-engine input view; only required H1
exogenous periods are supplied, using the executor's existing calendar arithmetic.
The complete bounded canonical capture still undergoes integrity and authority
checks, so held-out content is not a separate, unverified storage shortcut.

Exactly one actual feature state is fitted on the verified calibration prefix
per fold/organization. Every time-varying categorical release must pass its own
availability check at C **before** fitting. State JSON is exported verbatim; the
original partition hash binds the unchanged full plan, publication/series
identity, feature configuration and calibration bounds. Later origins use that
frozen vocabulary; they never refit on held-out or newly observed categories.
Ordered schema/dtypes, H1 coverage and numeric-policy identities remain those of
the existing executor. Default conversion remains STRICT_EXACT. Approximate
conversion is possible only when explicitly selected in the bootstrap-approved
configuration, with the existing measurement-scoped policy; no production
business tolerance is approved here.

Training labels are separated from histories at O. Each `TrainingLabelClaim`
uses `captured_value_as_of_M_v1`: exact publication, manifest/admission/mapping,
full plan/fold, series/dimensions/measurement, O/target period, release/evidence,
lineage and original Decimal tuple/integer/binary64 identity. The independent
label authority must validate the **exact captured revision as of M**, not just
its timestamp or a digest recomputed from the claim. A label may be released
after O, but must be complete and independently attested by M. Revised/backdated
values must also be rejected by the historical value authority when reconstructing
features at O or calibration at C. Original canonical labels retain their type,
Decimal scale and signed zero; only detached model-input copies are converted.

All dataset versions are completely consumed and successfully closed once per
request before fitting feature state or transforming examples. Snapshot cleanup
and lock release precede computation. Existing calibration/external/global/read
reauthorization runs before immutable result assembly. Terminal checks retain
every original training-origin history/permission context, then every original
label-at-M claim and its preparation permission. A cancellation/elapsed checkpoint
follows all callbacks and directly precedes return. Any failure returns nothing;
no partial handoff, held-out fallback, CSV fallback or background worker exists.
Sequential checks are not an atomic authorization snapshot: an earlier scope can
be revoked concurrently or by a later trusted scoped collaborator. Deployment
must supply stronger leases/snapshots if required. Cancellation is cooperative,
not hard callback preemption.

Existing temporal row/byte/origin/series/output/state/assembly/work limits remain
active. Operator scope additionally caps request-wide verification calls, claim
counts, serialized verification work and immutable handoff bytes. Full-plan
bounds are conservative even though held-out transformations do not execute.
These counters are not process RSS/CPU isolation. Scope/provenance JSON and exact
labels may be sensitive and must not be logged as public diagnostics.

`PREPARED / NOT_MODEL_FITTED` is the only completion status. Returned matrices,
labels and parsed states are detached copies; the stored tuples and audit
provenance are immutable. A serialized or caller-created handoff is **not** a
fitting credential or proof of authentic fitted-model provenance. No API in D1
accepts it as permission to train. A future owned trainer must freshly authorize
its use and bind the exact emitted feature state/representation to a deliberately
trained artifact. Real training, artifact persistence/loading, validation/test
assessment, registry operations, promotion and runtime migration remain later
milestones. Existing `prepare()`, `prepare_temporal()` and B1/B2a/B2b/B2c APIs keep
their accepted behavior.

### R-04D2: controlled offline XGBoost candidate (not evaluation or serving)

`CanonicalXGBoostTrainingService` is an additive, operator-owned bootstrap. It
fixes one approved fixture, temporal plan, fold, organization, measurement scope,
feature configuration, D1 scope, authority callbacks, and bounded training
configuration. `train()` accepts only cooperative cancellation. There is no
handoff, matrix, label, model URI, output path, registry or callback request
parameter. A service instance permits one training attempt. Another attempt
requires a fresh owned instance and complete D1 authorization/capture.

D2 constructs and invokes accepted D1 itself. It does not accept a serialized or
caller-created PREPARED object as fitting permission. Original successful
release/value/label/external verification contexts and claims are retained in a
bounded request-local ledger. Returned handoff values, original label claims,
state/schema JSON, exact event hashes, feature order and scope are checked before
fitting. There is one calibration state for the single selected fold and
organization; no feature-state fit or transform is performed during native model
loading. The full original plan and held-out boundaries remain in provenance,
but only declared H1 training examples enter the DMatrix. No validation/test
features, labels, eval sets, early stopping or model selection are used.

The initial model family is XGBoost 3.2.0 CPU only. The fixed algorithm is
`reg:squarederror`, `gbtree`, `hist`, eta 0.25, full row/column sampling,
min_child_weight 1 and max_bin 64. Seed, rounds and depth are declared and bound;
threads are fixed to one. Limits are at most 512 training rows, 32 features,
8,192 input cells, 16 rounds, depth 3, 1 MiB native artifact and 2 MiB bundle.
Evidence records/bytes are bounded separately. Iteration callbacks and final
checkpoints enforce a cooperative elapsed/cancellation budget, not hard CPU/RSS
isolation or interruption inside a native operation.

D1's STRICT_EXACT/default or explicitly approved numeric policy remains unchanged.
D2 adds the stricter, model-specific `strict_exact_float32_v1` ingress rule:
finite integer/float inputs and labels, absolute magnitude <= 1,000,000, exactly
representable in float32, preserving signed zero. Object/Decimal/string/Boolean
arrays are not model input views. Original canonical Decimal labels and identities
remain in D1 provenance. This policy deliberately rejects many ordinary float64
features, including irrational rolling standard deviations. The approved synthetic
fixture selects location, lag1 and mean4 explicitly; it does not change the
executor's calculations or legacy 32-feature schema. Broader float32 conversion
requires a separately approved policy/version, not an implicit cast. Histogram
binning and native floating-point model arithmetic are declared algorithm
semantics, not claims of exact real-number model fitting.

Native serialization uses `Booster.save_raw(raw_format='ubj')` and the matching
`Booster.load_model(bytearray(...))`. No pickle/joblib deserialization is used.
A new booster reloads the actual native bytes and predicts the same approved
training rows. Shape, float32 dtype, finite bounded outputs, native ordered
feature names/count, round count and embedded provenance bindings must match.
The comparison policy is `bitwise_float32_v1`, with zero numerical tolerance.
This is a serialization check on training inputs, not a validation score,
accuracy claim or general production inference interface. Identical artifact
bytes across platforms/library builds are not promised.

`CandidateBundle` contains immutable canonical JSON bytes: a versioned manifest
and canonical base64 of the actual native UBJSON. SHA-256 identities are computed
from those actual bytes. The manifest carries complete D1 provenance and original
state/schema strings, state/native artifact digests, operator invocation and
fixture approval identifiers, all authority identities, actual fixed/native
parameters, dataset/admission/mapping/purpose/plan bindings, original training
origins, frozen ordered dtypes, exact model input/label/prediction bytes, runtime
versions and selected implementation source hashes. Source identity records the
actual Git HEAD plus hashes of the listed implementation files. During precommit
synthetic tests `source_matches_commit` can be false; this explicitly identifies
an uncommitted source snapshot, not a claim that HEAD contains it. Postcommit
verification must produce a fresh bundle with `source_matches_commit=true`.
The flag covers the listed implementation files, not every repository file.

Bundle assembly and publication are **in memory only**, by one complete immutable
return. Native partial serialization, reader snapshot cleanup failure, denied
permission, budget exhaustion or cancellation yields no issued candidate. No
filesystem artifact writer, mutable production alias or durability protocol is
introduced. Applications that export bytes must provide their own separately
reviewed storage lifecycle; no file-write/crash-durability guarantee is claimed
here. Reader locks are released before fitting. Broad/calibration/external/read
checks precede complete bundle binding validation; original scoped evidence and
preparation/training permission are rechecked terminally, followed by the final
checkpoint and return. Sequential checks remain non-atomic authorization leases.

`verify_candidate()` only reloads the exact object issued by that service owner;
a byte-identical public reconstruction or a matching digest is not loading
permission. It performs fresh authority/publication checks and never refits state.
`CandidateBundle.validate()` is pure bounded structural/content-integrity checking;
it does not deserialize the native model or authenticate an artifact producer.
An independently transported bundle needs an independently authorized loading
boundary before any native parser. In-process Python collaborators and the
application bootstrap are trusted, not sandboxed; names, types and hashes cannot
establish authoritative trust by themselves. Bundles contain sensitive training
provenance and must be treated as private artifacts.

The only status is `CANDIDATE / NOT_APPROVED_FOR_PRODUCTION`. There is no MLflow,
registry, promotion, production pointer, API/scheduler/database integration,
B2c producer widening or legacy-model migration. Real held-out evaluation,
production business conversion approvals, durable artifact transport/authenticity,
atomic authorization snapshots and process resource controls remain later work.
The accepted D1 malformed-declaration warning P2 is unchanged in its source.

### R-04D3-A fitted-candidate evaluation declarations

`src/data/contracts/model_evaluation.py` adds a separate, frozen D3 contract
branch. `FittedCandidateCapabilities` version `1.1` declares a fitted XGBoost
candidate, tabular H1 representation and validation-only coverage. It is **not**
an extension of synthetic-v1 `PredictorCapabilities`: existing B1/C1/C2 bytes,
identities and accepted implementation kinds remain unchanged. Existing synthetic
executors reject the new declaration; no prediction or scoring adapter is added.

`declare_evaluation_eligibility()` checks bounded candidate **content** and makes
a detached, label-free `ModelEvaluationEligibility` version `1.1`. It binds the
original D2 bundle/artifact identities, canonical manifest SHA-256, D1 invocation
and complete original temporal plan, original state identities and ordered feature
schema, conversion configuration, training authority references, runtime/source declarations and explicit
training/evaluation publication relationship. The relationship currently requires
the same organization, source, exact mapping, series definitions and quality
policy; publications must be different immutable versions with distinct purposes.
Declared measurement, dimensions, calendar and schema must match original training
provenance. Verified publication metadata and independent relationship evidence
must still be established by the future operator-owned service.

The full training/validation/test plan is retained. Only explicitly selected
original validation origins may appear in D3 coverage. Calendar arithmetic is
reused from the accepted executor: aligned daily/weekly start periods, H1 target
starting at O, C <= M <= O, target end <= E, and no training/test target overlap.
No partition rewriting, state refitting, test execution or H2/H3 fallback exists.
The numeric requirements are `strict_exact_float32_v1`, finite native float32
outputs and exact float32-to-float64 widening. The D3 numeric-policy identity also
binds the original configuration digest, including canonical conversion scopes
and tolerances. Configuration is revalidated through the accepted constructor;
raw inputs never go directly to the standalone `configuration_digest()` helper.

**Authorization issuance is NOT_IMPLEMENTED.** Every result says
`DECLARATION_ONLY`. Neither a valid declaration, a matching hash, reconstructed
bundle bytes, a source-match flag nor a named authority proves original issuer
ownership or permission. Candidate-shaped inert metadata can pass content checks;
this does not prove genuine native training. D3-B must require the same process
and original live D2 issuer, authenticate the relationship and exact FINALIZED
purpose admissions, independently measure runtime/source compatibility and make
fresh scope-specific, revocable evaluation-use checks before loading, per origin
and before handoff. Cross-process/archival import is excluded. Ordinary requests
must not select their own authorities. These are obligations, not implemented
permission checks or an evaluation handle.

The returned `LabelFreeCandidateProvenance` is an explicit allowlist: plan,
invocation/authority identifiers, series/measurement scope, typed feature schema,
runtime/configuration identities and bounded size metadata. State bytes and fitted
vocabularies remain private; only their integrity identities are exported. The
manifest digest is SHA-256 of the original canonical `model_dump_json()` UTF-8
bytes. The future owner must recompute it against its original private manifest.
It is an integrity comparison, never a loading or evaluation credential.

The complete D2 manifest is inspected transiently during content validation and
is not retained in the returned declaration. There is no manifest accessor, JSON
payload, training matrix, row-index payload, training predictions, numeric labels
or exact label-value claims in its reachable supported object graph. Original
label-bearing records remain with the D2 owner. Rejected manifest-bearing D3-A
version `1.0` declarations are not accepted by the label-free `1.1` wire contract.
Accepted synthetic-v1 and D1/D2 schemas/artifacts are unchanged. No validation
labels are captured here. Conservative resource ceilings reject oversized
declared geometry/content; actual callback/capture/representation accounting, cancellation/deadline checks
following terminal callbacks, atomic authorization and hard CPU/RSS enforcement
remain future service/deployment responsibilities. Trusted in-process Python is
not a sandbox. The standalone digest-helper diagnostic P2 remains deferred.

D3-A public declaration deserialization uses local fixed-error guards for direct
construction, `model_validate()`, `model_validate_json()` and
`model_validate_strings()`. Original validation exceptions are discarded before
raising the public error; their input-bearing args, cause and implicit context
are not retained. The public error also explicitly detaches any ambient exception
the caller was already handling. Extra fields still reject, including attempts to select
`extra='ignore'` or `'allow'`. No warning filter is installed. The constructor
guard is a local metaclass boundary rather than a custom Pydantic initializer,
so compiled nested validation and strict/context dispatch remain unchanged.
A D3-local `RuntimeIdentity` facade protects the runtime fragment's direct APIs
while preserving its fields, JSON Schema and wire bytes; D2's type is unchanged.
The factory accepts the original D2 runtime declaration and snapshots it locally.

These guards cover D3 declaration APIs and nested validation reached through
them. They do not redefine standalone APIs on embedded accepted foundation
objects (plans, configurations, schemas, dataset bindings or training invocations).
Those objects must enter untrusted D3 deserialization through the guarded enclosing
declaration. Calling Pydantic `TypeAdapter`, raw `__pydantic_validator__` or
`BaseModel` implementations directly bypasses the D3 public guard; these are not
supported D3 import entry points. Deprecated Pydantic v1 `parse_*` and
`from_orm` APIs are also outside this supported D3 boundary. `model_construct()` and unchecked
`model_copy(update=...)` do not validate inputs or establish authority. String-mode
validation retains Pydantic's existing limitations for complex list/tuple fields;
Python/JSON validation provides the full declaration round-trip. No authorization,
loading, prediction, scoring or new resource enforcement is implemented here.

### R-04D3-B0 operator-owned publication relationship approvals

`bootstrap_relationship_authority()` in
`src/services/training_evaluation_relationship_authority.py` creates a private,
process-local approval ledger and two separate capabilities. Trusted application
bootstrap retains the `RelationshipApprovalOperator`; ordinary requests receive
only the pinned `RelationshipApprovalVerifier`. Bootstrap must authenticate the
operator and protect that separation. Constructing another authority, using the
same name, knowing a digest, or having a Python type does not establish trust.
Trusted Python is not a sandbox; this is not production identity infrastructure.

The operator explicitly reviews and approves an exact, guarded D3-A declaration
with an independent operator decision reference and issuance context. Neither
constructing a declaration nor requesting verification issues a record. The
immutable private record snapshots the entire label-free declaration, including
candidate/artifact/invocation, original plan, state/schema/configuration/runtime,
both immutable publications and purposes, tenant/series/measurement/calendar,
and exact validation H1 origins/targets. Approval identity is computed from the
actual record scope and issuance provenance. The independent operator ledger,
not a matching digest or individual read permission, supplies relationship
approval. There are no wildcard approvals or request-selected authorities.

Issuance and every verification call reuse the existing canonical provider's
`require_current()` for BOTH fixed publications. It reauthorizes exact purposes
and verifies FINALIZED admission, immutable manifest, mapping, series metadata,
and unexpired publication evidence. All verified bindings and series dimensions
must match the approved relationship. B0 opens no data reader, captures no labels,
and fits/loads/predicts no model. Existing providers and admission rules are not
changed. Operator-provided metadata is not trusted merely by its type: it must
match the provider's freshly reconstructed publication metadata.

Approval uses the original opaque handle object in its original registry. New,
copied, serialized, foreign-registry, or audit-receipt claims confer nothing.
Revocation is permanent and checked before live verification and again after all
provider/clock/cancellation callbacks. Expiry rejects at `now >= expires_at`;
backward/nonfinite clocks reject. No stale receipt bypasses a fresh check. Records
are re-checkable until expiry/revocation; cancellation aborts the current check,
not the operator's decision. Future D3-B execution handles must separately enforce
single-use terminal execution state. Sequential checks are not atomic leases.

Conservative cumulative ceilings bound records, stored declaration/metadata
bytes and verification attempts (including failed admitted attempts). Each call
has a cooperative elapsed deadline. Tombstones retain capacity; records cannot
be reactivated or reapproved in place. A busy registry rejects reentrant/concurrent
issuance or verification. Registry locks are not held during provider callbacks;
revocation remains possible during those callbacks. These controls do not impose
hard operating-system CPU/RSS limits, provide durability, or coordinate processes.

`RelationshipApprovalEvidence` is a label-free `RELATIONSHIP_ONLY` audit receipt,
not an evaluation-use or model-loading credential. Integrity references are not
anonymization; keep receipts/declarations within the controlled operator scope.
D3-A stays `DECLARATION_ONLY`
with authorization issuance `NOT_IMPLEMENTED`. B0 does not authenticate native
training or transfer D2 ownership: D3-B must independently pin the original live
D2 issuer, verify its private original issued candidate, establish model-use
permission and enforce prediction lifecycle rules. Raw manifests, training rows,
labels/predictions, native artifacts/Boosters and fitted-state payloads are never
accepted or exported by the B0 API. Existing D3-A/synthetic-v1 wire identities are
unchanged. All request validation goes through the guarded D3-A boundary; B0 adds
fixed errors with detached cause/context and installs no global warning filter.

### R-04D3-B owner-authorized frozen-state validation prediction

The original live `CanonicalXGBoostTrainingService` exposes the trusted-bootstrap
`bootstrap_validation_prediction()` hook. It accepts only its original issued
candidate object. Its new ownership/calibration check never replays training-row
predictions or exposes the full manifest/handoff. The private execution owner
receives only original native bytes, frozen state/schema and label-free bindings.
D2 training/verification behavior and accepted synthetic-v1 contracts are unchanged.

Trusted bootstrap pins the original B0 verifier/approval, evaluation provider,
independent evaluation-purpose release/value authorities and separate evaluation-use
authorizer. It retains the returned operator; requests receive only the pinned
executor and original opaque handle. No digest, receipt, reconstructed bundle,
request-selected provider/clock, or declaration authenticates model use. Arbitrary
Python running in the same process is outside this capability separation.

Each composition admits one bounded, single-use execution. Issue, capture, native
load, each origin/prediction and return check original ownership, B0 and independent
evaluation-use permission. Handles expire, can be revoked/cancelled by the operator,
and become terminal after execution success/failure; there is no reactivation API.
An additive private B0 composition gate rechecks its original registry after later
scoped callbacks without invoking publication callbacks again. It is only liveness,
not publication verification or model-use permission; full B0 verification remains
mandatory at bounded operation boundaries.

One complete, bounded, integrity-verified evaluation-publication history capture
closes before native loading or feature execution. The canonical reader necessarily
consumes the complete immutable version; no scoring-label capture or target-at-origin
label selection is performed. Future rows are excluded from origin-specific history.
Only that eligible history and the original D1 state enter `prepare_inference()`.
No fit/refit or validation vocabulary learning occurs. This initial adapter rejects
exogenous and non-identity category configurations rather than inventing availability
semantics. It supports only original approved validation H1 periods, never training,
test, H2/H3 or recursive real-model execution.

Typed feature scalars are checked before NumPy can erase Boolean identity. The
existing strict exact float32 input policy remains mandatory. Native finite float32
outputs are widened exactly to Python float64 (including signed zero); immutable
`FittedH1PredictionResult` records use canonical forecast scopes, explicit fitted
implementation kind, native bit identities and label-free candidate/publication,
relationship, state/schema, runtime, request and historical evidence references.
Results are `PREDICTED_NOT_SCORED`, never model-use credentials or accuracy evidence.

The last checked boundary before model loading/prediction verifies original owner,
B0, evaluation-use policy, applicable history claims, cancellation/deadline and the
original handle ledgers. Final result assembly precedes broad checks, terminal
per-origin history checks and final liveness/checkpoint/terminal publication.
Sequential authorities are not atomic leases. Revocation/cancellation during an
indivisible native call is observed afterward and prevents result publication;
no hard preemption or CPU/RSS isolation is promised. Bootstrap/clocks must remain
trusted and read-only. Limits cover capture rows/bytes, origins/native calls, input
cells, cumulative representations, artifact/state size, verification calls, and a
conservative repeated-history work ceiling. No durable/cross-process restoration,
labels/scoring/comparison, registry, serving or deployment integration is provided.

D2 additionally retains a private issuance seal (original candidate/manifest
integrity references and original immutable native bytes). D3 checks that seal
and original object identity, including on later operations; it cannot reconstruct
ownership from a caller-held manifest. Candidates need the live, current-code
D2 issuer; no legacy issuer import or retroactive reconstruction is supported.
Cumulative retained historical-evidence accounting is limited by the declared
decoded-byte ceiling, and the complete label-free output has a fixed 2 MiB cap.
These byte-accounting bounds do not represent Python heap or native RSS isolation.
The seal also binds the exact issuer instance, so a shallow-copied training
service is not an original issuer. Declaration and result publication references
are detached through guarded JSON round-trips; raw malformed result identity
access uses the existing D3 fixed-diagnostic identity boundary.
The existing feature executor's calculation-cell ceiling also applies to the
aggregate origins × series × per-target calculation work, admitted before loading;
individually small origins cannot each obtain a fresh request-wide allowance.
Final `scoped_return` evaluation-use checks cover every original validation
origin AFTER broader owner/B0/read/global-return callbacks, alongside terminal
historical attestation. Global permission cannot replace any origin's permission.
No partial/narrowed result is returned if a later callback revokes an origin.

### R-04D3-C0 original-owner joint H1 sealing (no labels or scores)

Trusted bootstrap may call the original live D2 issuer's
`bootstrap_joint_h1_prediction()`. It keeps the joint operator private and pins
its executor/verifier. The hook reuses D2's original-object/private-seal checks
and D3-B's existing native prediction executor, authorization, numeric conversion
and original D1 fitted state. No public result, hash, reconstructed bundle or
caller-built baseline batch becomes a producer credential.

One bounded, exhausted and closed historical capture is frozen using the accepted
B2c immutable capture implementation. The model and fixed operator-selected B2b
last-observation or seasonal-naive adapter use detached views of those same bytes.
Only origin-eligible rows reach either calculation. The category-free baseline
adapter invokes the existing canonical lag calculation kernel without fitting or
inventing a FeatureState; ordinary B2b callbacks and synthetic-v1 are unchanged.
An original baseline owner records its actual tuple; the original model executor
records its actual result. Both identity and content are checked before a joint
seal. Raw snapshot views are released after sealing or failure; retained revision
claims remain private for fresh authorization checks.

C0 uses separately versioned `JointValidationH1Coverage`/`SealedJointH1Result`
declarations for the exact approved validation-only H1 grid. It does not trim or
rewrite the original training plan, fabricate training-origin predictions, or
widen B2c's synthetic/full-fold APIs. The public sealed session is an opaque
original-object reference. Its guarded verifier returns detached, immutable,
label-free `SEALED_NOT_SCORED` diagnostics, never proof of model-use authority.

Prediction handles become terminal. Post-seal verification checks the original
D2 owner, B0 approval, publication and independent model/baseline/joint-use policy,
original handoff identity/content, original historical evidence and each return
scope. It performs no native loading, prediction or training-row replay. Operators
can revoke/cancel sessions; expiry, cancellation and failed admitted execution or
verification fail closed. Verification never revives prediction capabilities.
Future C1 must consume the original session through its pinned owner, not import
these reconstructable diagnostics, and must add separately authorized label use.
No label capture, scoring or C1 consumption is implemented in C0.

Bootstrap, providers, clocks and authentic authorities remain host-owned security
assumptions, not something Python privacy/hashes authenticate. Supported ingress
uses guarded D3 validation; raw compiled/TypeAdapter/deprecated validation remains
unsupported. Sequential callbacks are not atomic revocation leases. Native calls
are indivisible, cancellation cooperative, and accounting limits are not hard
CPU/RSS isolation. Limits include shared snapshot/copies, aggregate baseline work,
input/origin/native-call budgets, retained evidence, output and verification work.
A full immutable history version is still captured before filtering: future values
do not enter accepted feature vectors, while future physical dtype differences
can cause frozen-schema rejection before native prediction. No claim of complete
future-storage admission independence is made. The standalone digest-helper P2
remains deferred; raw configurations do not enter it through this boundary.
C1 will require a separate original-session consumption transition and explicit
label-use authority; verification alone does not issue either. It must preserve
C0's terminal prediction state and may not reconstruct ownership from result JSON.

### R-04D3-C1: original-session one-shot scoring

The trusted bootstrap retains C0's original verifier and calls
`bootstrap_evaluation(session, authorize_labels=..., label_revision_authority=...)`. It must retain the returned label operator privately
and pin the evaluator. The existing `BaselineAuthorityBinding` pins the exact evaluation publication,
revision protocol and authority ID/version. These callbacks originate in independent operator-owned
label permission/revision ledgers; declarations, IDs and receipts grant no access.
The publication provider, metadata, grid, history identity and clock are pinned
from the original C0 owner, never supplied by prediction requests.

C0 consumption follows SEALED → CONSUMPTION_CLAIMED → LABELS_AUTHORIZED →
LABELS_CAPTURED → SCORED → TERMINAL. A separate owner ledger and nonblocking
claim lock prevent repeated or reentrant consumption across consumers. Failure
never restores eligibility. Repeatable non-native C0 verification remains available
but does not authorize another consumption. Separate label-use handles are
original-object, one-shot, revocable/cancellable and expire at `now >= expires`.

Only after both original handoffs are sealed does C1 capture the evaluation
publication once through B2a's bounded reader and exact revision-claim helpers.
Both producers are scored against those same independently attested rows using
`score_point`, `group_scores`, and `JointScoreDelta`. RMSE retains its exact MSE
radicand; WAPE with zero denominator stays undefined. No RMSE delta or winner is
introduced. Output contains label-revision identities and exact sufficient
statistics, not raw label records or private training/model payloads. Statistics
can themselves reveal information about truth; this is authorized evaluation
output, not a confidentiality or differential-privacy guarantee.

Fresh non-native original-owner/B0/publication/model-use and per-origin return
checks run before capture, after capture, and before publication; independent
label permission/revision checks also run at final return. No native model,
baseline or history capture operation is permitted after sealing. Admission
bounds full label reads, cumulative representations, arithmetic, groups, work,
output and elapsed time. Sequential checks do not provide atomic revocation
leases, and native-call preemption, hard CPU/RSS enforcement, cross-process
restoration and durable authority are not implemented. C0's full-history physical
dtype admission limitation is unchanged. D3-D and production approval remain out
of scope. The standalone configuration-digest diagnostic P2 remains deferred;
C1 accepts no raw configuration ingress.

### R-04D3-D: tests-only end-to-end acceptance candidate

`tests/training/test_end_to_end_acceptance.py` exercises the existing trusted
entrypoints with a fresh genuine XGBoost candidate and isolated synthetic
FINALIZED publications. Instrumentation starts before setup and training; its
trace distinguishes setup reads, D1/D2 training, C0 shared history and C1 shared
evaluation labels. D2 invokes D1 itself. Original issuer, bundle, relationship
approval, joint owner, sealed session and label-use handles remain in one process.
No runtime orchestrator or public capability restoration is added.

The scenario runs separately for last-observation and seasonal-naive baselines.
Its standard-library Fraction oracle uses the approved raw source ledger and
observed original predictions, independently of production scoring helpers.
It checks exact sufficient statistics, origin-scoped ordering, RMSE radicands,
supported signed deltas, zero-denominator WAPE, native float32 widening, replay,
concurrent consumption and continued non-native verification. Post-seal traps
forbid native loading/prediction/training, feature fitting, baseline execution
and historical recapture. Fresh negative sessions cover cancellation, independent
label-expiry equality while C0 remains live, revocation, revision denial, provider
cleanup failure, late scoped denial, sanitized diagnostics and no returned result.

The additional C1 resource test measures a successful two-point run, then uses
fresh original sessions with the exact required allowance and one unit less for
rows, decoded bytes, points, groups, work, arithmetic reservations, intermediate
representations and output bytes. Declared geometry/arithmetic admission is
checked before capture; observed bytes/work are checked when charged.

Run from `sales-forecast-platform` with Python 3.12, XGBoost 3.2.0 and compatible
repository dependencies plus pytest. Test tooling belongs in an isolated virtual
environment; no production dependency-manifest change is required. For example:

```bash
PYTHONDONTWRITEBYTECODE=1 /path/to/test-env/bin/python -m pytest -q -s \
  --import-mode=importlib -p no:cacheprovider --basetemp=/tmp/d3d-acceptance-run \
  --junitxml=/tmp/d3d-acceptance-run.xml \
  tests/training/test_end_to_end_acceptance.py
```

The positive scenarios write bounded allowlisted `acceptance-*.json` reports
inside their pytest temporary directories and print paths and SHA-256 identities.
Reports are generated after scenario assertions and record `passed_assertions`;
the completed pytest/JUnit outcome remains the authority for test-run success.
They contain source/fixture/runtime identities, public lineage identifiers,
exact score ratios, supported deltas, lifecycle evidence and named checks. They
exclude raw label rows, full manifests, fitted state, native bytes, private
capabilities, credentials and exception payloads. Exact authorized score output
can still reveal information about truth. A report is test evidence, not durable
audit, cryptographic attestation, a runtime credential or production approval.

Independent executions must agree on the semantic score projection and fixture
hashes; run-specific candidate/seal/revision identifiers are retained separately.
Fresh UUID-bearing executions are not claimed to have byte-identical reports.

The focused compatibility manifest is the following explicit collection, avoiding
repository-wide discovery and unrelated production integrations:

```bash
PYTHONPATH=.:tests/features PYTHONDONTWRITEBYTECODE=1 /path/to/test-env/bin/python -m pytest -q \
  --import-mode=importlib -p no:cacheprovider --junitxml=/tmp/d3d-compatibility.xml \
  tests/training/test_end_to_end_acceptance.py \
  tests/training/test_canonical_model_training_preparation.py \
  tests/training/test_canonical_xgboost_training.py \
  tests/data/contracts/test_model_training.py \
  tests/data/contracts/test_model_candidate.py \
  tests/data/contracts/test_model_evaluation.py \
  tests/training/test_training_evaluation_relationship_authority.py \
  tests/training/test_canonical_model_prediction.py \
  tests/data/contracts/test_model_prediction.py \
  tests/training/test_joint_model_prediction.py \
  tests/data/contracts/test_model_joint_prediction.py \
  tests/training/test_joint_model_evaluation.py \
  tests/data/contracts/test_model_joint_evaluation.py \
  tests/training/test_deterministic_forecast_rollout.py \
  tests/training/test_forecast_evaluation.py \
  tests/data/contracts/test_forecast_evaluation.py \
  tests/training/test_forecast_baselines.py \
  tests/training/test_baseline_trusted_composition.py \
  tests/training/test_forecast_comparison_shared_execution.py \
  tests/training/test_forecast_comparison_joint_scoring.py \
  tests/data/contracts/test_forecast_comparison.py \
  tests/data/contracts/test_forecast_comparison_scoring.py \
  tests/features/test_canonical_feature_pipeline.py \
  tests/features/test_numeric_conversion.py \
  tests/features/test_feature_schema_compatibility.py \
  tests/data/contracts/test_configuration_contracts.py \
  tests/training/test_reference_baseline.py
```

The import mode avoids duplicate test basenames across contract/training suites;
the explicit test-only `PYTHONPATH` supports the existing feature tests' direct
`conftest` imports. Neither setting changes runtime imports in application code.

The frozen reference fixture remains SHA-256
`c7e42decc737296195db63af82f01437940270804daed1d7bddbcd587cd60adf`.
Scope remains controlled-offline, trusted-operator, process-local, validation-only
H1. Sequential authorization is not atomic revocation; budgets are cooperative,
not hard CPU/RSS isolation. Full-history physical-dtype admission, whole-publication
label reads with validation-only retention and the deferred standalone
`configuration_digest()` diagnostic P2 remain unchanged. Deployment, production
inference, registry promotion, H2/H3, distributed transactions and cross-process
restoration are excluded. This candidate requires separate independent review
before D3-D acceptance or promotion.

The test evidence writer requires the original live `NativeGuard` associated
with the original report object. This association stays private in test memory;
it is not a JSON field or a restorable capability. Foreign/copied guards and
detached report copies cannot authorize a write. The single writer validates the
unchanged public schema, encodes the report, then checks the guard's persistent
violation latch immediately before opening the output path. A handled guard
exception cannot restore permission. Validation/guard failures leave no evidence
file. Clean-report JSON bytes and semantic contents are unchanged. This is a
sequential check in trusted test code, not an atomic lease against hostile Python
threads or an atomic filesystem transaction.

For the pinned XGBoost 3.2.0 Python API, `Booster.predict` uses
`XGBoosterPredictFromDMatrix`; `inplace_predict` uses the Dense, CSR, Columnar,
CudaArray or CudaColumnar `XGBoosterPredictFrom*` entrypoints. All six appear in
the existing guard inventory. The genuine CPU negative controls exercise saved
`predict` and `inplace_predict` methods through the guarded C entrypoints. CUDA
execution is not tested. This repair does not widen the native inventory or claim
complete C API interception: direct legacy calls and the unlisted custom-training
`XGBoosterTrainOneIterWithSplitGrad` path remain outside that inventory. Normal
public `Booster.boost` calls are guarded; saved custom-training implementations
are not covered by that statement. The controlled acceptance scenario uses the
existing standard training and CPU prediction paths.

## Phase 5/6 M2 — versioned model representation declarations

`src/data/contracts/model_representation.py` adds a separate `1.0` representation
family. It does not extend synthetic-v1 capabilities, D1/D2 manifests or D3
execution contracts. Existing services do not automatically accept these types.
All roots have `status="DECLARATION_ONLY"`; neither a declaration nor its digest
is fitting, model-use, publication-read or evaluation permission.

The contract reuses the accepted guarded D3 declaration base (including its fixed
`EvaluationDeclarationError` diagnostics), `TemporalPreparationPlan`, forecast
point fields, `FeatureState`, `FeatureSchemaCompatibility`, canonical JSON and
canonical calendar arithmetic. It does not call the standalone configuration
helper on raw input, fit feature/scaler state, transform observations, read labels,
import model frameworks or access operational services.

The three roots are:

* `TabularRepresentation`: `(batch, features)`, explicit ordered `InputFeature`
  declarations, shape, missing mask and latest input availability per example.
* `RecurrentRepresentation`: `(batch, time, features)`, homogeneous float32 or
  float64 dtype, window, lengths, padding/missing masks and availability per slot.
* `TransformerRepresentation`: the recurrent fields describe the `past_observed`
  stream; separate `static` and `known_future` schemas, axes, shapes, masks and
  availability describe the other streams. Static and future groups may be
  explicitly zero-width. Future offsets must cover `0..H-1`. Each future feature
  must match an original canonical `known_in_advance` or `external_forecast` rule
  and carry an opaque evidence identity. Actual release verification remains an
  owner/provider responsibility, including external-forecast authorization.

`RepresentationBinding` retains the original full plan, selected fold/partition,
canonical state/schema and historical revision/evidence identities. Every batch
example carries a complete ordered horizon grid of `RepresentationPoint`s.
Organization/series/origin ordering, partition membership, calendar alignment,
measurement/dimension scope and exact target intervals are checked. Multi-horizon
metadata requires the original plan's existing `planning_only` capability; it
never enables multi-horizon D1/D2 training or D3 execution.

Tensor roots require a `ScalerStateReference`, even for an explicit identity
scaler. It binds training-only partition/origins, original plan/fold, model-fit
cutoff, organization scope and canonical state/schema identities. Policy names
`identity_v1`, `standard_v1` and `minmax_v1` are declarations, not implementations
or proof that parameters were fitted correctly. No fitted parameter payloads or
input/target tensor values are stored in these contracts.

Window `stride` means sampling historical calendar periods, not spacing training
examples. `lookback = 1 + (window - 1) * stride`; offsets run from `-lookback` to
`-1`. They describe fixed slots relative to each origin. Left/right padding marks
absent slots without shifting other slots' declared times. True means padding or
missing. Padding cells must all be missing; missing non-padding cells require an
explicit feature `missing="mask"` policy. All-present-cell availability claims
must be aware and no later than the origin. A wholly absent slot has no release
claim. Availability is the latest release among present cells, not an attestation.

Feature dtype/numeric policies must agree. Categories explicitly reference the
original vocabulary, Boolean-to-numeric coercion is disallowed, and float-to-int
coercion is not implied. Fill values are explicit finite Python floats; float32
fills must be exactly representable. The default is `0.0`; signed zero survives
serialization. Actual buffer finiteness, representability, values, alignment and
mask truth must be verified by M3–M5; M2 cannot establish them from metadata.

Canonical use, with already owned/validated foundation objects:

```python
from src.data.contracts.model_representation import (
    RepresentationBinding, RepresentationPoint, InputFeature, OrderedFeatures,
    TabularRepresentation, validate_representation, require_compatible,
)

binding = RepresentationBinding(
    plan=original_plan, fold_id="fold-1", partition="validation",
    canonical_schema=original_descriptor, canonical_state=original_state,
    historical_revision=owned_history_revision,
    historical_evidence_digest=owned_history_evidence_digest,
)
point = RepresentationPoint(**approved_h1_scope.model_dump())
representation = TabularRepresentation(
    binding=binding, samples=((point,),),
    feature_schema=OrderedFeatures(features=(InputFeature(
        name="lag1", dtype="float32", numeric_policy="strict_exact_float32_v1",
    ),)),
    shape=(1, 1), missing_mask=((False,),), available_at=(point.forecast_origin,),
)
restored = validate_representation(representation.model_dump_json())
assert restored.representation_id == representation.representation_id
assert require_compatible(representation, restored) == representation.compatibility_id
```

`representation_id` hashes the full canonical declaration, including batch and
capture metadata. `canonical_json` and UTF-8 `canonical_bytes` use existing sorted
canonical JSON; Pydantic `model_dump_json()` remains a supported round-trip wire
format but is not the mapping-order-independent canonical byte API.
`compatibility_id` pins family/version, ordered schemas,
window/dtype/policies, original plan/fold and frozen/scaler state, excluding batch
origins, lengths, masks, releases and history revision. It therefore proves
structural compatibility only, not matching input data or authority.

Supported untrusted ingress is `validate_representation()` (dict, JSON or a model
that is serialized/revalidated) and the inherited guarded constructors/validation
methods. Unknown fields reject. Global `strict` overrides are unsupported rather
than permitting callers to weaken new strict fields or rewrite foundation wire
semantics. Complex `model_validate_strings` inputs retain Pydantic's limitations;
JSON is the round-trip wire format. Raw compiled validators, TypeAdapter,
model_construct/model_copy and deprecated parse APIs are not canonical ingress.
These boundaries are process-local validation, not a hostile-Python sandbox.

All M2 JSON ingress shares a bounded strict UTF-8 preflight for strings, bytes and
bytearrays. Duplicate object keys (including escaped-equivalent keys) reject at
every depth before family dispatch or Pydantic validation. Malformed JSON, invalid
UTF-8 and nonstandard numeric constants reject through the same sanitized boundary.

Declaration limits are 32 examples, 64 features per stream, 512 historical slots,
4096 lookback periods, 65,536 mask cells per stream and 1 MiB serialized metadata.
They are conservative validation bounds, not runtime admission or hard CPU/RSS
limits. Feature count is model-specific; no new universal 32-feature schema exists.

M3–M5 still need explicit execution decisions: sample-generation stride, scaler
parameter serialization and per-feature/group scaling, supported tensor backends,
actual missing-value filling, category handling, trusted buffer/release checking,
and any versioned numeric policy needed after scaling. M2 does not relax accepted
D2 exact-float32 behavior, choose model architectures or implement these algorithms.


### M3 — provider-backed frozen tabular preparation (local controlled offline)

`CanonicalTabularPreparationService` composes the accepted
`CanonicalTrainingPreparationService`, which already owns canonical provider
capture, publication/availability authorization, per-fold calibration and
origin-aware execution. It adds no SQL, capture protocol or model authority.
`prepare(plan, configuration, model_family="xgboost", dtype="float64")` also
supports the `lightgbm` representation family, without importing either model
framework or implementing LightGBM training.

Each `TabularOutput` contains immutable actual scalar rows and an unchanged M2
`TabularRepresentation`. Its shape, exact organization/series/target index,
ordered schema, masks and raw numeric identities are checked before dataframe
conversion. Integer and Boolean identities remain explicit. Float32 conversion
is STRICT_EXACT: nonrepresentable rolling calculations reject, never round.
Float64 is a preparation policy, not a promise that a future model adapter will
consume float64 internally. D2's existing strict float32 policy is unchanged.

`TabularPreprocessingState` v1 stores the original temporal plan, fold,
publication binding, canonical configuration/schema, actual original
`FeatureState` (including vocabulary), and ordered M2 feature schema. State and
output identities cover deterministic canonical JSON. No training labels or
model artifacts enter state. Vocabulary is sorted and fitted only on the
explicit calibration prefix ending at C, before every training origin. It is
reused across training, validation, test and frozen inference. Unknown/missing
categories follow the configuration's existing explicit reject/reserved-negative-
code rules. Neither transform nor deserialization has a fit operation.

`FrozenTabularPreprocessor(state).transform(history, requests, representation,
model_family=..., exogenous=...)` is a pure kernel for trusted provider/service
composition, not authenticated public history ingress. It validates the original
state, exact request dimensions/grid and schema, selects origin-eligible history,
and invokes the accepted canonical inference executor. Availability claims and
M2 metadata are not authorization. Exogenous release evidence must be verified by
the composing provider/service, as in temporal preparation. Inference is limited
to H1 origins explicitly present in the original plan; arbitrary new horizons or
future-origin plans are not silently manufactured. Provider-backed preparation
still requires labels at each partition's authorized assessment cutoff; the
load-only inference kernel does not read labels.

`PreparedTabularPartitions.partition(name)` returns detached X/y and rejects
implicit mixing of folds or organizations with different learned state. Services
may iterate those states explicitly. Origin histories can include earlier
held-out observations only after their real release at O (expanding historical
backtesting), never the current target or unavailable future values. Complete
immutable publication storage is captured before origin filtering; future
physical dtype can still cause safe admission rejection. No future-storage
admission-independence guarantee is added.

The legacy `run_training_pipeline` now requires explicit `tabular_service`,
`temporal_plan` and `feature_configuration` arguments. With this composition,
the compatibility CSV path is never read. Raw-only CSV invocations retain M1
quality admission before rejecting missing temporal provenance; passing that
quality check alone no longer starts preprocessing or training. Raw TreePreprocessor
fit/transform fails closed rather than concatenate partitions or recode an
inference batch. Unsupported training families still reject before data access;
training remains explicit, XGBoost-only and unregistered. The wrapper is not the
owned D2 fitting authority. Row-index cross-validation is NOT_RUN: accepted
training-label evidence is as-of M, not as-of invented intermediate CV origins.
Operators must use explicit canonical temporal folds. New CV orchestration and
model/pipeline artifact packaging belong to M6, not this preprocessing change.

M1 reporting remains opt-in, allowlisted, bounded and separate from fitting.
Canonical dimensions are not guessed to mean Store/Dept. Reporting additionally
requires caller-supplied `report_context` with the exact test index and original
report allowlisted columns; absent/incompatible context returns reporting ERROR
without discarding the model. No registry, promotion or sync is invoked.

Example (trusted composition has already configured provider and callbacks):

```python
tabular = CanonicalTabularPreparationService(canonical_preparation)
partitions = tabular.prepare(plan, configuration, model_family="lightgbm")
output = partitions.outputs[0]
state_json = output.state.canonical_json
matrix = output.matrix  # independent copy with explicit per-column dtypes
```

M4/M5 remain sequence/Transformer execution work. M6 owns actual model adapters,
framework conversion enforcement, artifact packaging and legacy save/load
integration. These declarations and detached values confer no model-use or
production authority. Accepted D1/D2/D3 and M2 files/identities are unchanged.

### M4 — canonical recurrent preprocessing (LSTM/GRU, controlled offline H1)

`CanonicalRecurrentPreparationService` composes the accepted M3 canonical
preparation service. Repository capture, publication-purpose authorization,
availability verification, category fitting and feature formulas remain owned by
the accepted provider/temporal executor. M4 neither reads a second source nor
implements another feature pipeline. Its intermediate M3 tabular buffers contain
numeric canonical features; they do not imply an XGBoost model or prediction.
The final representation explicitly declares `model_family="lstm"` or `"gru"`.
No model framework, estimator, registration or production operation is invoked.

```python
request = RecurrentPreparationRequest(
    model_family="lstm", fold_id="fold-1", origins=approved_sample_origins,
    feature_names=("location", "lag1"),
    window=dict(lookback=3, window=3, stride=1,
                past_offsets=(-3, -2, -1), padding="left"),
    tensor_dtype="float64", scaling="standard_v1", missing="reject")
prepared = CanonicalRecurrentPreparationService(tabular_service).prepare(
    original_plan, original_configuration, request)
output = prepared.outputs[0]
state_json = output.state.canonical_json
array = output.tensor  # a new independent Batch x Time x Features NumPy array
```

Origins must be explicitly selected from the original H1 plan. The canonical
feature at each past slot t was calculated as-of t, not using target actuals at t
or future observations at O. A window slot is fixed at O + its declared offset.
M2 stride means spacing between slots, not example-generation stride. Windows
are keyed by organization/series and cannot cross declared Store/Department or
other series dimensions. Category vocabularies remain the original train-prefix
state; category codes are converted exactly and are never standardized.

The source feature grid consists of the original plan's prepared origins. M4
does not silently invent earlier feature rows or execute state before categorical
cutoff C. Callers must provide sufficient declared warm-up origins and select
sample origins accordingly. Entirely padded windows reject. Missing leading or
trailing slots use the declared padding side without moving observations to
other temporal positions. Interior or opposite-side gaps require explicit
`missing="mask"`; they remain missing time steps, not padding or genuine zero.
Sequence length counts non-padding positions, including explicitly masked
interior time steps. Masks use M2's True=missing/padding convention. Fill is an
explicit finite value applied after normalization, and never enters scaler fit.

`RecurrentPreprocessingState` v1 serializes the complete original M3 state,
request/ordered feature schema, exact training-row evidence digest, per-feature
counts, and finite binary64 centers/scales. Fitting requires complete original
training-origin/series coverage. The service passes only training feature
buffers, and the fitter excludes non-training buffers before touching their
numeric payloads. No labels or overlapping-window duplicates enter statistics.
Parameters are pooled within the declared fold/organization training scope,
never across organizations. Held-out rows never contribute to statistics.

Supported scaling is `identity_v1` or `standard_v1`. Standard scaling computes
population mean and variance using exact rational interpretations of the
captured binary64 values, then stores binary64 mean and square-root scale.
Normalization uses the versioned binary64 `(value-center)/scale` policy. Exact
zero variance explicitly uses scale 1; variance underflow/overflow or nonfinite
results reject. These are numeric preprocessing parameters, not model weights.
All categorical columns retain center 0/scale 1. All-missing training features
reject. Boolean features are unsupported in the homogeneous floating tensor;
select numeric/categorical fields explicitly rather than implicitly coerce them.

A separately bound M2 `ScalerStateReference` names the actual serialized M4
state and original plan/M/canonical schema/state. Output validation requires
that exact reference, dtype, window, positions, masks, feature order and values.
M3's scaler=None-only policy is unchanged. Float32 is STRICT_EXACT conversion of
computed values, including fill: typical normalized fractions may reject.
Callers can select binary64 or exactly representable identity-scaled float32;
M4 never rounds to satisfy float32 and does not relax D2's numeric policy.

`FrozenRecurrentPreprocessor(loaded_state).transform(feature_batches)` is a pure
load-only kernel and has no fit operation. The owning provider/service must
supply authenticated canonical feature buffers. It does not capture history or
labels; reconstructed declarations, state digests and buffers grant no model or
publication authority. Inference can use label-free M3 frozen feature outputs
for origins already approved in the original plan. Runtime capture/composition
is not introduced here. The preparation service returns separately authorized
targets for only the selected sample grid; neither fitter nor kernel receives
that target collection.

Outputs serialize deterministically and bind a new preparation identity to the
original source preparation plus ordered M4 output identities. Public arrays are
copies; frozen objects contain immutable scalar tuples. Aggregate cells, source
representations and output bytes are bounded; each output must fit the inherited
1 MiB JSON ingress limit, otherwise preparation rejects. Cancellation and elapsed
checks surround bounded work; they are cooperative, not hard CPU/RSS isolation.
As in M3, complete immutable storage is read before origin filtering: future
physical dtype can cause safe admission rejection, while accepted earlier
sequence values remain independent of future actuals.

M5 owns Transformer stream execution. M6 owns recurrent model adapters/training,
model/native precision policy and artifact integration. Higher-horizon real
execution requires an explicit extension of the existing H1 foundation; M4 does
not reinterpret H1 as H2/H3. Phase 7 runtime migration and production lifecycle
remain separate. Existing D1/D2/D3 and M1/M2/M3 source and identities are unchanged.
