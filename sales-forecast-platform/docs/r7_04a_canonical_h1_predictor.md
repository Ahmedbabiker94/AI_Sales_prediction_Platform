# R7-04A canonical H1 predictor contract

R7-04A is a **non-executable contract/orchestration boundary**. It produces no
forecast, native model input, artifact, execution capability or production
permission. `CanonicalH1PredictorService.predict` returns only DENIED or
UNAVAILABLE. Its name reserves a migration boundary; it does not enable inference.

## Accepted dependency inventory and ownership

* M2 `RepresentationContract` provides frozen, strict, versioned declarations,
  canonical serialization and recursive duplicate-key rejection. M2 declarations
  and compatibility IDs describe semantics; they do not authenticate buffers.
* M3 `TabularOutput`, `bind_output`, `FrozenTabularPreprocessor.transform` bind
  actual ordered values to original frozen canonical state. Only scaler=None is
  executed. R7-04A neither calls these methods nor reconstructs their outputs.
* M4 `RecurrentOutput` / `FrozenRecurrentPreprocessor` bind window, masks,
  positions and exact numeric/scaler semantics. M5 `TransformerOutput` /
  `FrozenTransformerPreprocessor` additionally bind past/static/future streams
  and publication-time covariate evidence. Their inference transforms are not
  called; structural R7-02 compatibility does not authenticate executable tensors.
* M6 retains original candidate references, metadata and original frozen states.
  M7 verifies those exact live references and candidate-only package bytes.
  Every M7 native publisher/loader is unavailable; its finalized evidence JSON
  explicitly contains NO_MODEL_PAYLOAD / TEST_ONLY_NON_DEPLOYABLE.
* M8 evaluation reports retain synthetic evidence classification. M9 approval
  authorizes only further controlled validation. R7-03 refreshes these original
  owners and original M8 report identity through M9, not detached caller reports.
* R7-02 captures origin-eligible source context using original repository,
  provider, publication, revision/value attestations and per-value external
  forecast authority. It still reads complete immutable storage before filtering.
* R7-03 `RuntimeModelUseService.decide` verifies original ownership, exact scope,
  inspection grants, two authorized snapshot captures and terminal liveness.
  It returns only non-executable DENIED/UNAVAILABLE. Its inspection grant is not
  runtime execution authority. Fresh M9 checks may reread authorized labels.
* R7-01 lazy runtime composition is untouched and is not model-use authority.

Protected host composition supplies an exact original R7-03 service to the new
service. Calls traverse R7-04A service -> R7-03 service/evidence bridge -> accepted
M7/M9/R7-02 services -> their original providers -> repositories. No provider
imports a new service; no SQL, registry, model store or publication authority is
added. There is no new authority issuer or callback-based execution approval.

## Request, outcome and scope

`CanonicalH1PredictionRequest` version 1.0 embeds the existing
`RuntimeModelUseRequest` as `inspection`: candidate/package/family, original-owner
requirement, representation execution-binding and compatibility IDs, frozen and
canonical state/schema, lifecycle transition, evaluation/scoped ensemble evidence,
snapshot ID, complete source request, tenant/series/publication/origin/cutoff,
inspection actor and host-context bindings. No duplicated mutable aliases are
introduced. Source request budgets and original publication purposes retain
exactly their accepted meanings, not inference authority.

The request adds:

* `execution_purpose=controlled_canonical_h1_prediction_v1`.
* `execution_authority_requirement=independent_runtime_execution_authority_required_v1`.
* `executable_input_requirement=original_owner_bound_m2_m3_m4_m5_outputs_required_v1`.
* Strict H1 target start/end: target start equals origin and end is computed using
  the accepted canonical calendar's one-period advance; horizon_step is exactly 1.
* Accepted `RuntimeUseLimits`: request <=256 KiB, outcome <=16 KiB, cooperative
  elapsed limit <=60 seconds. Stricter inner R7-03 limits remain effective.

`request_id` binds the complete validated declaration; `scope_id` binds the exact
snapshot identity/source request and H1 target geometry; `purpose_id` binds the
version, execution purpose and independent authority/input requirements. Digests
provide integrity only. They cannot establish ownership or execution permission.

Supported ingress is an exact typed request, revalidated through strict canonical
JSON at the service boundary. Callers can use direct `model_validate_json` first;
raw dictionaries, detached snapshots/decisions, malformed geometry and unsupported
purposes reject. JSON validation reuses M2's strict parser; no new raw feature
configuration or standalone configuration_digest diagnostic path is introduced.

`CanonicalH1PredictionOutcome` has no prediction-value field or SUCCESS state.
Its versioned identity binds all public outcome fields. DENIED exposes no admitted
references. UNAVAILABLE binds the request/scope/purpose, original inspection
decision identity and exact candidate/package/snapshot/family. Both force false
execution/production permission and execution_performed=False. Both remain
SOURCE_CONTEXT_ONLY / TEST_ONLY_NON_DEPLOYABLE with unavailable payload, loader,
execution authority and executable inputs.

## Fail-closed invocation and timing

```python
service = CanonicalH1PredictorService(original_inspection_service)
outcome = service.predict(request, inspection_capability=original_inspection_handle)
```

The service admits exact request geometry/size and pins original R7-03 composition.
R7-03 performs all owned candidate, frozen-state, publication and snapshot checks.
DENIED inspection yields DENIED prediction admission. Valid UNAVAILABLE inspection
can yield only UNAVAILABLE, because no accepted independent execution-authority
verifier, native payload/loader or executable-input binder exists.

The optional `execution_authority` argument is deliberately unsupported for every
non-None object. Inspection capabilities/receipts/decisions and M9 approvals reject
without interpretation, introspection, truth-value coercion or deserialization.
None means missing authority, never implicit permission. R7-04A does not fabricate
an issuer, validator, positive approval or placeholder execution token.

One active call per service; no success cache. One R7-03 inspection per valid
admission (two authorized source captures internally). Outer cancellation and
elapsed checks are passed into R7-03 so its repaired final live grant validation
follows those callbacks. After R7-03 returns, only bounded, callback-free contract
validation, serialization/size checking and outcome construction occur. No new
host clock/cancellation callback runs after the accepted terminal authority check.
Observed interruptions are latched so lower-layer sanitized denials retain the
CANCELLED_OR_EXPIRED reason without post-inspection resampling.
The final cooperative time sample precedes that bounded outcome construction;
there is no hard return-time or callback-preemption guarantee. Oversized results
fail closed; the fixed denial control message is outside the admitted-result
byte budget. Errors contain fixed reason codes, no private object repr or paths.

## Future executable inputs: requirements, not implemented capabilities

| Family | Required accepted output/binding for a separately authorized executor |
|---|---|
| XGBoost | M3 TabularOutput + M2 TabularRepresentation; Batch x ordered Features, exact feature schema/dtypes and missing mask, origin/H1 samples, original frozen categorical vocabulary, scaler=None. M6 XGBoost precision is strict exact float32. |
| LightGBM | Same M3/M2 tabular ownership and ordering requirements; no invented scaler. M6 permits declared float32 or float64; no silent conversion is authorized. |
| LSTM | M4 RecurrentOutput + M2 RecurrentRepresentation; Batch x Time x Features, exact lookback/window/stride/past offsets, series/origin/H1 alignment, lengths, padding/missing masks, original frozen scaler parameters/reference and category state. |
| GRU | Same M4/M2 recurrent geometry/state requirements, with exact GRU family binding rather than LSTM substitution. |
| Transformer | M5 TransformerOutput + M2 TransformerRepresentation: encoder past-observed BxTxF, static BxF, decoder known-future BxHxF (H=1), exact group feature order/dtypes, positions, lengths/masks, per-origin availability and original composite frozen scaler/state references. |

M4/M5 permit declared float64 or STRICT_EXACT float32 after the declared numeric
transformation. Standard scaling does not generally yield exact float32; rejection
is required rather than rounding. M6 capabilities are not proof of native numeric
acceptance. All future buffers require original-owner provenance/value binding,
not just equal schema hashes. No source context is reinterpreted as those outputs;
missing context, unavailable earlier-encoder revisions, unknown category policy or
incompatible frozen state must not be guessed, filled, refitted or synthesized.

## Legacy boundary and R7-04B prerequisites

Legacy `src/inference/predictor.py` constructs model/preprocessor factories and
loading/enrichment paths. `RecursiveForecastService.__init__` constructs Predictor
and operational providers. They are inspected as migration dependencies only;
neither is imported, instantiated, used as fallback, cleaned up or migrated here.
API/Celery/scheduler, production resolver/current.json and synchronization remain
unchanged. A missing canonical dependency never falls back to those paths.

R7-04B requires separate approval of native artifact publication and verified safe
loaders, original-owner executable preprocessing/input bindings, independent
runtime execution authority, revocation/consumption/lifecycle rules, and genuine
model-level precision/numerical compatibility tests. A future positive executor
must not reinterpret an R7-03 or R7-04A negative decision as a capability.
Trusted-host, process-local identity, sequential evidence checks, cooperative
limits, authorized M9 label rereads and full-history physical-dtype admission
limitations remain. There is no cryptographic IAM, atomic external authorization
lease, durable restoration, genuine-model performance claim, production approval,
persistence, H2+, deployment, Phase 7 closure or legacy cleanup in this milestone.
