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
