# M8: controlled-offline test-only evaluation and deterministic ensembles

M8 is family-neutral evaluation infrastructure, not genuine model-quality
validation, candidate approval, automatic model selection or production use.
All five M6 families are admitted through original live M6 references and M7
TEST_ONLY_NON_DEPLOYABLE packages. Real-model evidence remains UNAVAILABLE.
No model framework, native loader, estimator or prediction callback executes.

## Reused boundaries

- M2 guarded immutable contracts provide strict Python/JSON/string validation,
  recursive duplicate-key rejection, canonical serialization and identities.
- M3/M4/M5 original private frozen states provide target configuration and temporal
  provenance. M8 does not refit, transform features, load weights or relax their
  numeric policies. Different families retain their own representation/state IDs;
  comparable targets do not require identical model-specific input schemas.
- M6 original reference/owner records remain private. M7 verification checks exact
  live references, manifests, state/schema and publication bindings, without a
  native model operation. Detached metadata, copied references/services and hashes
  are not credentials. M8 deliberately couples to the current private M6/M7 owner
  interface; future changes need a reviewed owner interface, not reconstruction.
- Existing `CanonicalTrainingDataProvider` authenticates FINALIZED publications,
  purposes, read permissions, immutable snapshots and fresh admission evidence.
  Its existing repository remains the only data storage path. M8 adds a thin
  provider wrapper; it adds no SQL, database repository, accuracy store or job.
- B2a `_capture_label_rows` and `_verified_label_claim` are reused for exact row
  alignment and revision assertions. A private single-use adapter lends one open
  canonical session to that helper. Its `require_current` check is deferred until
  the outer read session closes to avoid recursive filesystem-lock acquisition.
  The original provider checks access throughout reading; fresh require_current
  follows immediately after close and before output. This is ONE canonical read
  session, not a claim of one physical disk pass: provider integrity/quality
  verification can read storage internally before exposing that session.
- B2a `rational`, `bounded`, `statistics`, `ratio` and exact ratio contracts are
  reused. M8 adds only sMAPE/bias accumulation and rational weighted means over
  the same exact arithmetic. Legacy `src/training/evaluator.py` uses sklearn and
  rounded floating metrics; operational accuracy services construct database
  providers/save results and may silently skip missing rows. They are not invoked.
  Accepted B2c synthetic-only APIs, full-fold coverage and scoring remain intact.

## Trusted composition and lifecycle

An operator constructs `CandidateEvaluationProvider(original_canonical_provider,
authorize_labels=..., verify_label_revision=...)`, then
`CandidateEvaluationService(provider, candidates, authorize_evaluation=...)`.
`candidates` is an operator-pinned tuple of `(original_packaging_service,
original_candidate_reference, exact_package_id)` entries, not request metadata.
The provider, callbacks, destination and clock must never come from request data.
Python object encapsulation is process-local API separation, not host/cryptographic
identity or an independently durable approval registry. The host must protect the
composition and independently attest label revisions, including exact values.
Publication-read permission alone is not label-use or evaluation approval.

Supported untrusted ingress is guarded M8 contract validation, `evidence()`
(non-authoritative conversion), and `service.evaluate(request, cancelled=...)`.
The service accepts the exact typed request, a JSON-compatible dictionary, or
strict UTF-8 JSON text/bytes. Dictionaries pass through the same guarded JSON
boundary. Raw TypeAdapter/compiled/deprecated validation and underscore helpers
are not request ingress. Independently invoking embedded foundation-type
validators is not guarded M8 ingress; use the enclosing M8 contracts. Errors
detach context/cause and contain fixed text;
no malformed payload is logged and no global warning suppression is installed.

For each non-reentrant invocation:

1. Canonically revalidate/freeze request, all test predictions and fixed ensemble
   configuration; verify original owners and exact M7 packages; admit the grid.
2. Independently authorize evaluation, then label use before canonical capture.
3. Capture one shared truth snapshot, exhaust/close it and authenticate exact
   revisions as of the original fold assessment cutoff E. Labels must be actual,
   end by E, and have availability <= E; E must not be in the provider's future.
4. Align by full key identities and compute all model/ensemble scores from that
   same truth. No producer is called, before or after labels become visible.
5. Recheck owners/packages, publication and label permissions; authorize every
   individual key's `return:<key_id>` scope and global return. Final label/publication
   freshness checks follow evaluation callbacks, then local owner liveness checks.
   Failure returns no partial report. Cancellation and elapsed >= deadline reject.

Repeat evaluations are allowed, but require fresh authorization and a new shared
capture each time. This does not consume or revive D3 execution/session handles.
There is no general public label-read API; truth remains inside the private wrapper.
Callbacks and clocks are trusted and checks sequential, not atomic revocation
leases. Indivisible filesystem/arithmetic calls are not preempted. The returned
report is an immutable snapshot, not continuing authorization.

## Versioned contracts and alignment

Version 1.0 defines EvaluationKey, CandidateEvidence, PredictionObservation,
GroundTruthObservation, CandidatePredictionBatch, CandidateEvaluationRequest,
MetricResult, MultiCandidateComparison, EnsembleConfiguration and EnsembleResult.
An EvaluationKey reuses ForecastScope: tenant/series, fold, plan digest,
evaluation publication version, dimensions, frequency/timezone, target metric,
unit/currency, origin, H1 interval, partition and identity-target semantics.
CandidateEvidence binds family, candidate/package, M7 retained representation
binding, M2 compatibility, original frozen state and preprocessing schema IDs.
This does not claim retained transient M2 output IDs or portable native artifacts.

Initial scope is one original plan/fold, one logical dataset/tenant, H1, validation
OR test. Training diagnostics, H2/H3, transformed targets and pooling different
plans/calendars/units reject. The evaluation publication must be distinct from
training, for offline_forecast_evaluation, with the same logical dataset, source,
map and complete series definition scope. These are intentionally conservative
compatibility rules. It is never relabeled from a training publication.

Keys are unique and ordered by fold, tenant, UTC origin, series and full key ID.
Each candidate must have exactly the declared key grid in exactly that order.
No array-position-only joins, implicit intersections or row dropping occur.
`exact_grid_v1` covers every series/origin in the selected original partition.
`declared_common_subset_v1` accepts an explicitly supplied subset for ALL producers,
with eligible/evaluated/excluded counts and exact keys in the report. Each producer
and deterministic per-series group reports its actual sample count.

Prediction and truth values use strict finite binary64, Boolean rejection, no
numeric-string coercion. Accepted np.float64 truth is widened exactly to native
float; integer/Decimal truth is intentionally unsupported rather than silently
cast. Underlying preparation strict float32 semantics are unchanged. Actual
label values are not accepted as request fields and are not returned as raw truth.

## Metrics, ranking and ensembles

For N explicitly aligned forecast events, error e = prediction - actual:

- SAE = sum |e|, SSE = sum e^2, SAA = sum |actual|; count N.
- MAE = SAE/N, MSE = SSE/N; RMSE is represented exactly as sqrt(rmse_radicand),
  with rmse_radicand = MSE. No rounded approximate root is substituted.
- WAPE = SAE/SAA as a ratio, not percent; SAA = 0 is explicitly undefined/None.
- sMAPE = mean(2*|e|/(|prediction|+|actual|)), ratio [0,2], not percent.
  A both-zero pair contributes 0 and remains in N; no observations are dropped.
- Bias = mean signed error, prediction minus actual.

Binary floats are interpreted as their exact binary rationals. Fraction-based
accumulation uses the accepted bounded arithmetic policy; reports contain reduced
exact ratios, not rounded float sums. Groups are deterministic per producer/series
within the SAME fold, partition and key grid. Overall statistics and group sums/
means must agree. No mixed split/horizon pooling, intervals or significance claims.

Ranking is exact MAE ascending with candidate ID lexicographic tie-break, for the
same shared population. It is descriptive TEST_ONLY evidence, not model selection
or approval. The ensemble is reported separately and has no automatic winner claim.

Ensembles are equal means or fixed nonnegative EXACT rational weights summing to
one. Membership must equal the complete requested candidate list. Weights and
configuration identity are frozen/authorized before labels are captured. Outputs
are exact signed rationals, including nonrepresentable binary64 means (e.g. 1/3),
so no silent ensemble rounding occurs. Optimization, learned weights and any
validation/test label-based tuning protocol are explicitly unavailable. The host
must not misdescribe externally tuned weights as independently fixed.

## Limits and remaining work

At most 8 candidates, 256 keys, 256 groups; configurable bounded full-capture rows,
raw/canonical/chunk/decoded bytes, intermediates, logical work, report bytes and
elapsed time. Both storage budgets and post-read accounting apply. Group count
and prediction-work admission precede label capture. These are cooperative caps,
not hard CPU/RSS/disk isolation; full immutable storage verification precedes
selected label filtering, so physical dtype/quality of unselected storage can
cause safe admission rejection. This does not imply future-storage admission
independence or alter the accepted D3-B temporal limitation.

Public report: request/population/revision identities, source/package/state refs,
keys and coverage, exact metrics/groups/ranking and exact ensemble predictions.
No full manifest, training labels/rows, raw frozen state, weights, private handles
or raw evaluation truth is exported. Small-group statistics can still reveal
information; digests/statistics are not encryption or a privacy mechanism.

TEST_ONLY predictions are operator-approved supplied fixture values, not
owner-attested native producer handoffs. Candidate ownership does NOT establish
that supplied numbers were generated by the candidate or before their origin.
Fixtures may derive numbers from synthetic known truth solely to test arithmetic;
these are NEVER genuine performance claims. Genuine/model-quality evidence
needs separately authorized original-owner prediction handoffs and native
verification; M8 cannot upgrade M7 non-deployable packages. M9 approval/promotion,
Phase 7 runtime migration, native loaders, portable state, learned ensembles,
metrics persistence, cross-process ownership and legacy cleanup remain deferred.

## Scoped ensemble evaluation identity

`configuration_id` remains the unchanged reusable recipe identity. It is no longer
used as the ensemble metric producer ID. `EnsembleEvaluationBinding` version 1.0,
policy `m8_ensemble_evaluation_v1`, canonically binds that recipe to the admitted
request, original plan digest and assessment cutoff, full evaluation-publication
binding, ordered H1 keys/target semantics/split/fold, coverage policy/eligible
count, ordered member source/package/state/representation references, prediction
batch IDs, captured label-revision root/population and exact scoring-policy ID.

The service constructs this binding only after original-owner/package/grid
admission and authorized truth capture. Its canonical digest is the required
serialized `ensemble_evaluation_id`, used by the ensemble metric and its series
groups. Result validation checks the digest and binding against the enclosing
report; legacy scopeless ensemble outputs reject. Recipe/request semantics and
metric values do not change. The report identity now includes this scoped evidence.
No caller-provided scoped ID is accepted on requests. A matching declaration or
digest remains integrity evidence, not authority.

An immutable publication cannot legitimately acquire a different value revision
in place. Revision tests use separately finalized, independently authorized
publications with unchanged prediction values and new revision evidence. Cutoff
changes require an original M6 plan/candidate binding for that cutoff. Unsupported
fold/unit/transformation rewrites still reject; binding sensitivity tests do not
claim that arbitrary constructed declarations are owner-authorized evaluations.
Cross-run external weight selection and the other limitations above are unchanged.
