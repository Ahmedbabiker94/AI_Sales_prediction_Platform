# R7-02: canonical runtime data readiness snapshots

R7-02 is data readiness only. It does not invoke a predictor, calculate engineered
features, create M2 tensors, fit preprocessing, load a native model, perform separate evaluation-label
capture, persist forecasts, or grant model-use/production authority. It changes no
accepted source file, runtime bootstrap slot, resolver, sync job or entrypoint.

## Reused boundaries

```
LocalDatasetRepository (original finalized immutable version and read lock)
  -> CanonicalTrainingDataProvider.open_read / require_current
  -> original CanonicalTrainingPreparationService._capture lifecycle
  -> CanonicalRuntimeSnapshotService.capture
  -> immutable RuntimeInputSnapshot
```

No SQL, PostgreSQL shortcut, new dataset store, alternate revision definition or
parallel feature-engineering implementation is introduced. Repository retrieval is
bounded by captured-row/decoded-byte/deadline limits. It reads one complete
immutable version before projecting the exact history window. This is NOT a
predicate-pushed-down history query or future-storage admission independence.
The provider may scan that same owned immutable version for integrity/quality and
consumption; this is one capture, not independently mutable producer reads.

Publication remains the accepted `TemporalDatasetBinding` / `StoredDatasetReference`.
The existing provider verifies finalized admission, mapping, quality, original
purpose, version, lineage and read permission. The service compares the complete
verified temporal binding against the requested binding, including series,
organization, manifest, admission and series-definition digests. It performs a
fresh `require_current` before return, without a second data capture.

## Protected composition and authority

The host supplies the original canonical provider, pinned
`FeatureSchemaCompatibility`, original `FeatureState`, an independent
`ValueAttestedAvailabilityVerifier`, `authorize_snapshot(request, context, stage)`
and, where required, the accepted `ExternalFeatureRequest` authorization callback.
Factories, callbacks, clocks and source substitutions are never request fields.
This is a trusted-host boundary, not host authentication or a Python sandbox.

Authorization stages are `admit`, `read`, `after_read`, `return`. Cancellation and
elapsed time are checked after callbacks as well as before work. Value/revision
attestation is run on origin-eligible claims and repeated before return. The
immutable `RuntimeSnapshotContext` extends the accepted preparation context with
the canonical request and capture cutoff. Independent attesters must verify the
original values, releases and revision validity at that cutoff using their own
publication-bound ledger; comparing a digest from the request is insufficient.
The existing verifier's `CapturedHistoryClaim` protocol is reused unchanged.

Only the EXISTING publication purposes `production_training` and
`offline_forecast_evaluation` can be requested, and only within their existing
permissions. Data readiness does not widen either purpose into live inference.
An unsupported `runtime_inference` purpose rejects before reading. New live
runtime publication rights require separately approved authority work; R7-02
neither invents them nor claims they already exist.

Authorization/publication/attestation checks are sequential, not atomic leases.
A trusted callback's own side effects are not rolled back. Cancellation/limits are
cooperative, not hard CPU/RSS isolation. The caller must retain the protected
service-to-consumer data flow: reconstructed snapshots and matching IDs are data,
not original-owner/model-use credentials. Durable authority and restoration are
not provided.

## Request and output

`RuntimeSnapshotRequest` v1.0 requires an organization-scoped temporal dataset
binding, explicit origin/capture cutoff, H1, canonical complete schema JSON,
consecutive history length, required static/external field names and hard-capped
resource declarations. No wall-clock date is used to select a forecast origin.

Encode the accepted schema using:

```python
from src.data.contracts.dataset import operational_json
schema_json = operational_json(schema.model_dump(mode='json', warnings='error'))
```

The schema pins configuration, output ordering/dtypes, numeric/availability policy,
training physical schema, executor and frozen state. The service independently pins
the same schema/state at trusted construction. JSON parsing reuses M2's strict,
recursive duplicate-key rejection and sanitized declaration boundary. Existing
D1/D2/D3 and M1-M9 serialization and identities are untouched.

Snapshot and request IDs use separate versioned domains over canonical admitted
content. No timestamps, digests or caller metadata become authorization. Snapshot
output contains:

- The complete immutable request and exact H1 end boundary.
- Series-major, oldest-first consecutive history, with canonical `Observation`
  fields, organization, original row/lineage/release evidence and pre-conversion
  scalar identity. The history record is a frozen narrowed subclass of the
  accepted `Observation`, not a second canonical dataset format.
- Publication-bound canonical identity dimensions, sorted by field name. Arbitrary
  store attributes such as `Size` are unsupported; asking for them rejects.
- Ordered historical covariate fields and H1 exogenous fields, with exact periods,
  values and release/row evidence, all from that same capture.
- Complete/no-padding missingness semantics, original source physical dtype/order
  declarations and their accepted schema digest, and full-capture row/decoded-byte
  resource evidence. Dtype strings are metadata and must never be evaluated as code.

History is selected using period end <= origin and release <= capture cutoff <=
origin. Every requested series must have every consecutive period. Missing,
duplicate, nonfinite, mis-scoped, conflicting or insufficient history rejects;
there is no zero fill, implicit latest revision, forward fill or cross-series
fallback. Calendar arithmetic, numeric admission, category checks, canonical row
validation and exogenous policies reuse the original feature engine. Numeric
conversion uses the accepted configuration's policy, with exact original scalar
identity retained and checked (including signed zero). No float32 rounding or new
scaler policy is added.

H1 external values use the configured original rule's period/offset. The accepted
availability claims verify their releases; the original `_exo` policy checks
known-in-advance/lagged/observed-only semantics and independently authorizes
`external_forecast` using its exact values and provenance. External authorization
is repeated before return. Missing-as-zero exogenous policies are unsupported.
Only covariate fields/evidence are extracted at H1; target actuals from those rows
never enter the result. Full source capture remains internal trusted work.

Limits: 10,000 full-capture rows, 16 MiB decoded source, at most 4,096 returned
history observations, 512 KiB serialized output and at most 60 cooperative seconds;
requests may tighten these bounds. Projection and metadata amplification also
remain subject to the accepted canonical engine/provider limits.

## M3/M4/M5 compatibility

`validate_preparation_context(request, frozen_state, model_family=...)` revalidates
the accepted M3/M4/M5 state, checks exact pinned canonical schema/state and series
scope, and conservatively checks required raw-history geometry. It returns only
`SOURCE_CONTEXT_ONLY`, binding the request and preparation-state identities.

| Family | Supported context assessment | Deliberately not claimed |
|---|---|---|
| XGBoost / LightGBM | Original M3 state, schema and H1 source context | Tabular output/native prediction acceptance |
| LSTM / GRU | Original M4 state and requested encoder lookback plus feature history | Scaled tensors, masks or native execution |
| Transformer | Original M5 state, observed encoder history, dimension-only static context and authorized H1 future fields | Actual encoder/static/decoder tensors or model use |

Original folds/origins and frozen preprocessing identities are retained. Unseen
runtime origins outside those accepted bindings reject this assessment; no M2
identity is redefined. Recurrent/Transformer encoders that require historical
exogenous streams (including calculated dependencies on them) reject because this
version attests historical fields at the request cutoff, not separately at each
encoder origin. It supplies their values and releases but does not falsely claim
earlier-origin revision authority. Actual model-input binding, per-encoder-origin
execution, strict float32-after-scaling admission and model-use authorization are
R7-03/R7-04 prerequisites, not results of this check.

## Tests and boundaries

Tests use four overlapping canonical series (two location/store identities x two
departments), original finalized local repositories/providers, an in-memory read
repository fake with owned copies, independent release/value ledgers and synthetic
frozen preprocessing states. No estimator is fitted or native model loaded.
The repository's immutable-file protection is relaxed only inside a disposable
fixture to test payload-corruption rejection; no existing artifact is touched.

Coverage includes exact isolation/order/history, future actual independence,
future physical-dtype rejection, schema/tenant/revision substitutions, release
cutoffs, missing context, authorized/denied/late external forecasts, one capture,
mutation resistance, strict serialization, malformed diagnostics, resource and
post-callback cancellation/deadline rejection. Positive family compatibility uses
actual provider-backed M3/M4/M5 preparation; capture-time fitting and feature
execution are trapped. Existing R7-01 and bounded canonical suites are regressions.

Known limits remain: whole-version storage admission before filtering; no production
purpose, arbitrary static-attribute authority, per-encoder-origin covariate revision attestation,
live SQL source migration, model loading/ownership attestation, forecast persistence,
H2+ runtime, legacy recursive migration, sync/promotion or deployment integration.

Verification used `PYTHONDONTWRITEBYTECODE=1`,
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, the existing `/workspace/b1-test-env/bin/python`,
and `-m pytest -q --tb=short -p no:cacheprovider` from the project directory:

| PYTHONPATH | Explicit targets | Distinct passed |
|---|---|---:|
| `.` | `tests/runtime/test_runtime_snapshot.py` | 71 |
| `.` | `tests/data/contracts/test_model_representation.py tests/data/contracts/test_temporal_preparation.py tests/features tests/training/test_canonical_tabular_preparation.py tests/training/test_canonical_recurrent_preparation.py tests/training/test_canonical_transformer_preparation.py` | 615 |
| `.:/tmp/r7-01-deps` | `tests/runtime/test_bootstrap.py` | 42 |

Total: 728 distinct repository tests. Development reruns/subsets are not added to
that count. The 15 bootstrap deprecation warnings remain observable. An additional
bounded supported-service probe recorded one capture, 48 captured rows, four
series, 16 historical observations, 16 historical covariates and four H1
covariates, with fitting/inference execution trapped during snapshot capture.
