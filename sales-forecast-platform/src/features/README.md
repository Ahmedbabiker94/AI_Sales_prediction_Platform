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
