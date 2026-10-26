# R7-01: runtime bootstrap isolation and characterization

This is an import/composition boundary, not native inference migration, model-use
authorization or production readiness. No default factory loads models, opens a
database/broker, registers/promotes models, or synchronizes production artifacts.
No accepted canonical preparation, training, package, evaluation or lifecycle
implementation/identity is changed. Production resolver/sync implementations,
XGBoost persistence quarantine and raw TreePreprocessor rejection are unchanged.

## Baseline characterization, before implementation

Source inspected: API/schema, Celery/task, scheduler/registry/all four jobs,
ForecastService/RecursiveForecastService, Predictor, model/preprocessor factories,
logging/metrics, database/provider initialization and production resolver/sync.
Operational imports were trapped before execution. Baseline isolated-source import
checks failed for API, Celery app, tasks, scheduler, registry and three job modules
because they imported operational dependencies. The old sync-job module itself
was import-safe (its explicit run method imported sync). Three initial route/schema
characterization tests passed, including negative tests for existing defects.

Before:

```
api.main -> celery_app -> tasks -> global RecursiveForecastService -> Predictor
api.main -> global dashboard/report/monitoring -> DB/PDF/logger initialization
API startup -> ForecastService + RecursiveForecastService -> native constructor/load
scheduler -> JOB_REGISTRY -> eager Forecast/Accuracy/Report jobs -> services/DB
Celery decorators/application configuration -> task names and dispatch
```

After:

```
api.main -> static FastAPI routes/schemas + empty Runtime + standard logging
celery_app -> task declarations + empty Runtime (application/broker settings unchanged)
scheduler -> inert job metadata + deferred .run callbacks + empty Runtime
job constructors -> retain an explicit Runtime only

trusted host configure_runtime(Runtime(factories))     [no factory calls]
HTTP operation / Celery task body / explicit job run  -> Runtime.get(named slot)
Runtime.get -> original injected collaborator, created once on first use
register_jobs -> injected scheduler.add_job(deferred job.run), no job construction
start_scheduler -> injected scheduler/lock; explicit start only
```

Real Celery is used, not a substitute task framework. Its original local application,
Redis configuration and import of task declarations are retained byte-for-byte.
That declaration import is now safe: it does not create a recursive forecaster,
load a model, initialize database providers, open a broker connection or install
file handlers. The API no longer imports the Celery app merely to register routes.
APScheduler creation/start is a host-injected explicit operation, not an import.

## Supported host composition

`src.runtime.bootstrap.Runtime` accepts a copied, bounded allowlisted dictionary of
zero-argument factories. Configuration never invokes them. A factory must return a
non-None collaborator. Named construction is serialized/cached per Runtime; same-slot
reentrancy and missing/invalid configuration reject with RuntimeUnavailable. No raw
factory exception/cause/context is exposed by that error. Failed construction is
not cached; a later explicit attempt may retry. The host owns cleanup for resources
that its factory creates before raising; arbitrary factory side effects are not
transactionally rolled back. Other lifecycle/operation exceptions retain their
existing service/job semantics, except the API's generic handler logs a fixed message.

Use protected startup code, not JSON/task/request data, for example with test stubs:

```python
from src.runtime.bootstrap import Runtime
import api.main as api
import src.tasks as tasks

runtime = Runtime({
    'forecast': lambda: stub_single_forecast,
    'recursive_forecast': lambda: stub_recursive_forecast,
    'forecast_task': lambda: stub_task_dispatcher,  # .delay(store, dept)
    'task_backend': lambda: stub_task_backend,     # .AsyncResult(task_id)
})
api.configure_runtime(runtime)
tasks.configure_runtime(runtime)
```

API slots: forecast, recursive_forecast, dashboard, report, monitoring, job_health,
forecast_task, task_backend. Respective method signatures remain the legacy service
interfaces. The test dispatcher/backend avoid broker operations entirely. Default
API imports, startup and OpenAPI inspection need no operational composition.
Runtime-dependent endpoints return HTTP 503 with a fixed detail when a dependency
is absent or its factory fails. Health retains its schema and liveness status but
reports model_loaded=False: R7-01 never attests native model readiness. The existing
model_version="production" legacy response string is preserved; it is not an M9
approval or genuine model-output attestation. Injected test successes are only
legacy-interface tests, not canonical model-use evidence.

Celery keeps src.tasks.test_task and src.tasks.forecast_next_week_task, .run,
.delay and .apply_async conventions. Configure each worker process before accepting
forecast tasks; the default forecasting task fails closed. No cross-process transfer
or automatic restoration of Runtime/service/model ownership is implemented.

Job constructors keep their no-positional-argument interface and add keyword-only
runtime=None. Empty constructors perform no operational work; run fails closed.
Job collaborator slots are:

| Job | Slots used by its existing body |
|---|---|
| ForecastJob | recursive_forecast, job_status, job_execution, job_metrics |
| ForecastAccuracyJob | accuracy_service, job_tracker, job_execution, job_metrics |
| ReportGenerationJob | report_generator, pdf_writer, job_tracker, job_execution, job_metrics |
| ProductionModelSyncJob | production_sync |

Each body resolves its required slots before invoking its work/tracking methods.
Metrics is a namespace with the original counter/histogram names, and pdf_writer
is a callable. No default collaborator is supplied. Scheduling a sync job does not
resolve the sync object; its injected .sync() is called only on explicit dispatch.
Underlying production sync and resolver protocols are untouched and never exercised
against operational systems by these tests.

`build_job_registry(runtime)` retains the four names/triggers/intervals:
forecast_job=60, accuracy_job=10, report_job=120, production_model_sync_job=5 minutes.
Its `config['job'].run()` is deferred. `configure_runtime` preserves the public
JOB_REGISTRY dictionary identity for existing consumers. Scheduler configuration
also configures that registry. `register_jobs` accepts optional scheduler_instance
and registry arguments for bounded inspection. `start_scheduler()` keeps its public
entrypoint and adds install_signal_handlers=False for embedding/tests. Production
SIGINT/SIGTERM registration remains on explicit start, never import. Registration
failure/normal stop releases an acquired scheduler lock; lock-held reconfiguration
or reentrant start rejects. A denied lock does not register/start anything.

## Limits and deliberately retained defects

- Runtime is trusted-host factory wiring, not authentication, a capability issuer,
  a registry, M9 authority or proof that a returned model is authorized. Do not pass
  untrusted factories/objects into startup. It cannot turn TEST_ONLY packages into
  loadable artifacts. No factory is resolved by a caller-selected module name.
- Configure API/tasks/registry before serving, not concurrently with operations.
  There is no hot-reconfiguration protocol. Returned services retain their own
  concurrency/authorization requirements; construction locking is not an operation
  lease. Scheduler lock ownership is process-local orchestration, not distributed
  lifecycle authority. Persistent job stores and cross-process factory restoration
  are unsupported by this milestone.
- Default entrypoints are inspectable but operationally unavailable until explicitly
  composed. There is no implicit legacy fallback. A host intentionally supplying
  old ForecastService would still encounter its unchanged model/preprocessor guards.
- /batch_predict still reads items although BatchPredictRequest defines rows;
  the handler's float response also conflicts with BatchPredictResponse objects and
  required total_rows. Negative characterization tests preserve these failures.
- Duplicate earlier ForecastRequest/Item/Response declarations are not deleted.
  Existing response objects, clamp/round presentation, task/job names and legacy
  multiweek dispatch signatures remain; stub tests of weeks=4 are not H2+ execution.
- Raw history, enrichment/series/date leakage, mutable source snapshots, old DTO
  validation/authentication/CORS, serialization/native-loader policy, persistence,
  stale dashboard/DAG/Compose paths and deployment dependency gaps remain deferred.
- API/schema existing Pydantic/on_event deprecation warnings and TestClient warnings
  remain observable. No global warning suppression or Pydantic monkeypatch is used.
- Genuine fitting/loading, production registry/pointer/sync/deployment and new
  approvals are not performed. Native runtime authority, artifact compatibility,
  provider-backed as-of data and frozen-state execution remain Phase 7 prerequisites.

## Verification boundaries

Committed focused tests exercise actual FastAPI routing/TestClient, real Celery task
registration/discovery, actual APScheduler registration without starting it, original
job bodies with injected edges, and safe original-constructor traps. Fresh-process
imports trap operational module imports, filesystem writes/creation, file handlers,
socket and broker-connection construction and signal installation. No original
operational service constructor, DB, broker, registry, model loader or sync runs.
Bounded existing M1/canonical/M6-M9 regressions use estimator stubs. Genuine-native
fixtures are excluded; their previous acceptance is not claimed as new R7 evidence.

Optional Celery/APScheduler/Prometheus libraries were installed under /tmp solely
for verification; requirements/Docker/Compose and the original test environment were
not modified. No deployment dependency changes are bundled into bootstrap isolation.

Verification commands (repository project directory):

```
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=.:/tmp/r7-01-deps \
 /workspace/b1-test-env/bin/python -m pytest -q --tb=short -p no:cacheprovider \
 tests/runtime/test_bootstrap.py
```

Result: 42 passed. The optional test-only directory contained Celery 5.5.3,
APScheduler 3.11.2 and prometheus-client 0.22.1 plus their dependencies.

Existing regressions used the same flags with PYTHONPATH=. (the original environment):

| Targets / selection | Distinct passed |
|---|---:|
| tests/training/test_training_entrypoint_safety.py, test_training_entrypoint_integration.py, test_training_admission_integration.py; tests/monitoring/test_outlier_report.py | 79 |
| tests/data/contracts tests/features tests/data/processing; -k 'not test_genuine_candidates_are_label_free_and_remain_owned' | 1346 |
| tests/training/test_canonical_adapter_training.py::test_all_families_real_preparation_training_only_and_private_candidate; tests/training/test_candidate_packaging.py; -k 'original_owner_all_families or all_families_real_preparation' | 10 |
| tests/training/test_candidate_evaluation.py -k scoped | 12 |
| tests/training/test_candidate_lifecycle.py | 50 |

Total: 1,539 distinct tests, counting each repository case once. Initial characterization,
red import checks and repeated focused runs are not added to this total. Existing
schema/FastAPI/TestClient deprecations and Pandas date parsing warnings remain visible.
The excluded genuine candidate case performs native model work; no such work was
performed for R7-01. No unsafe repository-wide collection was used.
