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
