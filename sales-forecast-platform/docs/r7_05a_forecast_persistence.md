# R7-05A forecast provenance declarations and non-writing inspection

This milestone has **no forecast writer or genuine prediction records**. It
adds structural DECLARED metadata and a non-writing inspection service. It does
not create an alternative store, registry, execution issuer or legacy adapter.

## Accepted foundations and provenance graph

`ForecastProvenance.prediction` embeds accepted R7-04A's complete immutable
CanonicalH1PredictionRequest, which embeds the accepted R7-03 request. The graph
therefore binds:

* Organization/dataset/series scope, publication/version/admission/mapping and
  series-definition identities; explicit forecast origin/capture cutoff and H1
  target interval, canonical source snapshot and availability policy (R7-02).
* Candidate/package/family; retained M6 representation execution-binding and M2
  compatibility identities; original frozen/canonical state/schema identities.
* Original M8 report and scoped ensemble evidence, M9 lifecycle/approval record,
  and exact inspection actor/host context. These references do not authenticate
  a caller; original services authenticate their retained private handles.
* R7-04A execution purpose and independent execution/input requirements, including
  accepted resource limits. R7-04A remains non-executable and its UNAVAILABLE
  result does not attest a model execution.

M3 binds actual tabular outputs to frozen vocabularies/feature order (scaler=None);
M4 binds recurrent geometry, masks, lengths and train-only scaler state; M5 binds
past/static/known-future groups and original composite state. M2 compatibility
alone is not buffer ownership. M6 retains the original frozen state. M7 verifies
its exact candidate reference and package bytes, but publishes only
TEST_ONLY_NON_DEPLOYABLE / NO_MODEL_PAYLOAD evidence. M9 approves further controlled
validation only and refreshes its exact original M8 evaluation; it may reread
authorized labels. No existing artifact, public digest or approval supplies runtime
execution authority, a genuine prediction value or persistence permission.

The graph above describes required references, not verified execution. Current
inspection is **structural only**, scoped to an immutable expected R7-04A request
pinned by trusted host construction. The pinned request is not authentication,
publication authority or original-owner attestation. Matching it never grants
VERIFIED/PERSISTABLE status, even if all referenced IDs look plausible.

The accepted canonical LocalDatasetRepository._connection helper uses a SQLite
catalog and executes schema/PRAGMA initialization even on existing read paths.
R7-04A/R7-03 delegation therefore cannot provide an unconditional no-SQLite-write
boundary. R7-05A deliberately does NOT invoke prediction, capture, M9 refresh or
any repository/provider. This avoids weakening or changing accepted foundations
and makes unavailable fresh verification explicit rather than using a receipt as
proof. Future genuine verification must retain the accepted Repository ->
Provider -> Service path and separately establish a truly read-only evidence path.
There is no reverse layer dependency or parallel data/authority store here.

## Version 1.0 record schema and identity

`ForecastProvenance` adds versioned requirements for original native artifact
identity, verified loader attestation, owner-bound executable input identity,
independent execution authority and genuine producer execution evidence. These
are requirements, not fabricated attestations or future capabilities.

`CanonicalH1ForecastDeclaration` contains that provenance and exactly one series
from the prediction scope. R7-04A validates the exact canonical H1 geometry:
target start equals origin, target end is the accepted calendar's next boundary,
and horizon step is one. Before any future VERIFIED/PERSISTABLE acceptance, tenant/series/publication,
origin/cutoff, state and candidate bindings require original-owner verification;
structural contract validation is insufficient.

The only constructible state is DECLARED. The following fields must be null:

    prediction_value, physical_dtype, native_artifact_id,
    loader_attestation_id, executable_input_id, execution_authority_id,
    execution_evidence_id, supersedes

Even finite floats, zero, float32/float64 dtype strings and digest-shaped claims
reject. There is no accepted executable producer or verifier today, so a public
value-bearing record, VERIFIED state and PERSISTABLE state are deliberately
unavailable. Future genuine records require a separately accepted contract/path;
R7-05A never manufactures a zero value, execution receipt or execution identity.

All contracts reuse frozen M2 RepresentationContract, strict recursive JSON-key
validation, bounded UTF-8 parsing and canonical operational serialization.
`lineage_id` binds the entire versioned provenance; `declaration_id` binds the
entire versioned declaration, including series and all absent claim fields.
They identify metadata, not an execution, stored row or authority.

## Exact proposed logical key and conflict policy

`logical_key_id` uses the namespace `canonical_candidate_h1_forecast_key_v1` and
the following exact canonical preimage, in order:

1. Organization UUID, dataset UUID and selected series UUID.
2. Original candidate ID and model family (candidate-specific forecast streams).
3. Forecast origin, target start, target end and horizon step.
4. Target metric, the complete accepted measurement declaration, the complete
   accepted temporal policy declaration, and `identity_target_v1` transformation.

IDs/timestamps use their accepted canonical encodings; declaration field order
cannot affect canonical JSON. Different tenants, series or candidate streams
have different keys. All five candidate families may coexist for a target.
Publication revision, cutoff, package, snapshot, frozen-state/evidence and grant
context are included in lineage/declaration identity rather than independently
partitioning this logical key. A different authorized view for the same key is a
conflict, not silent replacement. A different candidate gets its own stream.

The pure `compare_forecast_declarations` utility returns IDENTICAL_DECLARATION,
CONFLICTING_DECLARATION or DIFFERENT_LOGICAL_KEY after structural revalidation.
It has no stored-state lookup and confers no idempotent write authorization.
`ForecastDeclarationBatch` is a bounded metadata collection (1..16 declarations,
unique logical keys in sorted-key order); identical duplicates and conflicting
same-key declarations reject. It is not a write/staging interface. The inspection
service accepts a single declaration, never a batch for partial publication.

For separately approved R7-05B genuine records:

* Identical replay must match logical key, exact value bits/physical dtype and
  all verified lineage/execution identities, with fresh permission. It must not
  create a second row or infer authority from a matching hash.
* Same key with a different value, dtype or lineage is a conflict. Fail closed;
  do not use a last-writer-wins upsert or implicitly overwrite a row.
* Correction/supersession requires a separately authorized append-only transition,
  exact prior genuine record ID and expected revision/CAS. Keep the prior row
  auditable; do not reactivate or replace it through ordinary replay.
* The database uniqueness/transaction/migration design, revision counters and
  write authority are not implemented here. Schema version 1.0 is not a database
  revision number. No supersession reference is accepted today.

## Non-writing service and timing

`CanonicalForecastPersistenceInspectionService(expected_prediction).inspect(...)`
revalidates declaration/size and compares the complete prediction request against
the host-pinned immutable canonical request. Candidate/package, tenant/series,
publication, origin/target, state and representation substitutions reject relative
to that exact declared scope. The constructor does not authenticate that scope;
all metadata remains DECLARED/unverified. It makes no original-owner, revision-
liveness or genuine-execution attestation. Those checks are prerequisites, not
claims justified by a matching digest.

Every non-None execution_evidence or write_authority object rejects without
interpretation, truth-value coercion or deserialization. Inspection grants,
R7-04A UNAVAILABLE outcomes, M9 decisions, actor strings and hashes cannot become
execution evidence or write authorization. There is no producer/evidence verifier
or writer to invoke. Missing evidence remains unavailable, never approved.

DENIED returns only fixed reason codes and no admitted identity references.
UNAVAILABLE binds the DECLARED metadata's declaration, logical key, lineage and
prediction-request IDs (not a fabricated predictor/execution receipt). Both force
state=DECLARED, verified=False, persistable=False, executable_permission=False and
writes_performed=False. No forecast value escapes. Repeated structural inspection
remains non-writing UNAVAILABLE. Revocation of an unrelated runtime inspection
grant does not affect this metadata-only result; no grant is consumed or claimed.

Limits reuse the pinned prediction limits: declaration <=256 KiB, admitted outcome
<=16 KiB, cooperative elapsed <=60s. The accepted JSON parser also enforces its
1 MiB ingress cap. One active inspection per service; no cache, stored rows or
duplicate-decision registry. Callback cancellation/deadline checks run at entry
and after bounded output construction; exact elapsed >= deadline rejects. No
partial references return after failure. There is no authority callback/lease to
validate because execution/write authority is unconditionally unavailable. The
fixed denial control message is outside the admitted-result byte budget. Clocks
must be finite/monotonic; blocking callbacks are not preemptible. No hard CPU/RSS
or return-time bound, durable authority, or atomic external lease is claimed.

## Legacy migration gaps (read-only findings)

* `ForecastPersistenceService` directly constructs ForecastRepository, bypassing
  a provider. `PostgresForecastProvider` delegates the same legacy save signature.
  ForecastStorageProvider/ForecastProvider expose store/department/date APIs.
* ForecastRepository inserts store, dept, forecast_date, predicted_sales and
  model_version. Its SQL has no canonical tenant, series UUID, origin/cutoff,
  snapshot/publication, state, package, execution evidence or write authorization
  columns/checks. `model_version='production'` is a string, not owner evidence.
* Accuracy lookup joins on store/dept/date and omits candidate/model version,
  forecast ID and canonical origin/revision. Distinct candidate forecasts can be
  ambiguous. Accuracy persistence also writes legacy date/value fields; it does
  not bind M8 exact evidence or canonical label revisions.
* ForecastAccuracyService fetches actuals by store/dept/date, skips missing values
  and uses legacy percentage-error conventions. ReportService/DashboardService
  consume legacy aggregates and defaults. No scoring/report migration occurs.
* Recent/by-ID forecast queries do not express canonical tenant/publication
  scope. Physical database constraints and deployed DDL are not verified by this
  offline audit; repository SQL is not proof of durable uniqueness/idempotency.

These are migration dependencies, not changes or new production exposure in
R7-05A. No legacy persistence, accuracy, report, job, resolver/current.json, sync,
API or deployment code is imported as a fallback, activated, cleaned up or edited.

## R7-04B / R7-05B prerequisites and limits

R7-04B needs approved genuine native artifact publication/loading, owner-bound
executable M2/M3/M4/M5 inputs and original frozen state, independent execution
capabilities and terminal revocation/consumption rules, and genuine numerical
compatibility tests. Producer evidence must bind exact scope, source revision,
artifact/loader/input/authority identities, output order, physical dtype and exact
value bits (including applicable float32 widening/signed-zero semantics).

R7-05B additionally needs a genuinely read-only original-owner/provenance verification
path (or explicitly authorized storage semantics), an original-producer evidence
verifier, independent
write authorization, real scoped repository/provider schemas, atomic create-only
idempotency/CAS and correction policy, migrations and consumer compatibility,
and production-independent tests of genuine record publication/failure recovery.

Accepted runtime authority remains trusted-host/process-local, not durable IAM
or cryptographic attestation; R7-05A neither consumes it nor asserts freshness.
Its controls are cooperative. Accepted external checks remain sequential, and
R7-02's full-history physical-dtype admission limitation remains for future runtime
integration. TEST_ONLY
candidates remain non-deployable. There is no native inference, real forecast
persistence, execution or production approval, Phase 7 closure or deployment.
