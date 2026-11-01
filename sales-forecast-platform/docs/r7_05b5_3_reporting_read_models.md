# R7-05B5-3: controlled-offline reporting projections

Trusted host constructs build_canonical_reporting_reader(original_reader,
original_evaluator=None). The optional evaluator must be bound to that reader.
Consumers supply original forecast grants/ExactForecastRead and, for accuracy,
independent original label grants/ExactLabelRead. Source objects, operator,
authority state and credentials remain protected host responsibilities. This
is not production IAM or a Python sandbox against malicious trusted host code.

read_forecast calls original B5-1 once. read_synthetic_accuracy calls original
B5-2 once; B5-2 performs its one original B5-1 read. No detached source outcomes,
arrays, serialized grants, digest, tenant ID or report ID authorize either method.

AVAILABLE contains FORECAST_ONLY or CONTROLLED_OFFLINE_SYNTHETIC_ACCURACY, frozen
source contracts retaining complete scope, original row ordinal/order, logical
identity, H1 calendar/measurement, receipt, idempotency, lineage and content digest.
No inferred registry version, Production alias, Store/Dept or publication timestamp.
Forecast display_values widens original little-endian float32 bytes, preserving
signed zero; no rounding, clamping, unit conversion, resampling or aggregation.
Accuracy contains exact original AccuracyEvidence, revision/snapshot/as-of and
availability semantics, complete coverage and unchanged binary64 MAE/RMSE/Bias.
No metric recomputation or percentage metrics. Actuals are synthetic only.
Operational trusted actual-label integration remains BLOCKED.

DENIED contains no view. Original NOT_FOUND and LABELS_NOT_READY become
NOT_AVAILABLE; original INCOMPATIBLE_SCOPE and UNAVAILABLE remain distinct with
no view. No exceptions or sensitive evidence appear in negative payloads.
An unconfigured optional accuracy source is statically NOT_AVAILABLE. Original
forecast NOT_FOUND inside evaluator remains evaluator UNAVAILABLE, without a
second probing read. KeyboardInterrupt/SystemExit propagate unchanged.

No cross-request cache, result registry, materialization, persistence, SQL, legacy
fallback, new source provider/repository or operational consumer binding is added.
Original Repository -> Provider -> Service chain remains authoritative. The
presentation digest is deterministic integrity data only, never a capability.
Caller-constructed projection declarations cannot authorize any source operation.

A1 source cutoff is explicit: S<Q<C permits exactly one original preclaimed
read-only lookup; Q<S and Q<P are denied upstream. P<Q<Python return is permitted.
Formatting after original publication adds no new protected P_REPORT, grant
renewal or delivery authority. There is no physical cancellation, Design B
external-delivery fence, distributed lease or crash-durable quarantine. Existing
CPython 3.11/3.12/private-state assumptions remain; new wrapper cannot reset Q.
Stronger report-specific final authorization/delivery needs separate review.

Tests use static immutable batches and independently issued synthetic grants,
never model fitting/prediction/native workers. The historical 77-case execution
incident and entire test_controlled_h1_execution.py remain excluded. PostgreSQL
fixtures alone write to disposable PG16.15; application operations remain reads.
The five-file implementation changes no accepted source, SQL, schema or migration.
API/dashboard/PDF/export/jobs/scheduler/Celery/MLflow remain deferred.

PostgreSQL test execution requires original disposable bootstrap.sql role setup,
then apply_forecast_evidence_migration(target('migrator')) before retained tests.
The reporting test checks schema version read-only; it does not use the foundation
migrated fixture, which intentionally requires an initially absent schema. Schema
bootstrap and fixture setup writes remain separate from application projections.
