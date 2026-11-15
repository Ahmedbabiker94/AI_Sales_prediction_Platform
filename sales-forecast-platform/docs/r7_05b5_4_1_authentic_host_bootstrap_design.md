# R7-05B5-4-1 — Authentic Host Bootstrap (design gate)

Status: DRAFT / NOT ACCEPTED. Base commit: 57e44881af82c73ef3b1724d9d2587d48427aa00.
Branch: fix/r7-05b5-4-1-bootstrap.

## Verified starting point
B5-3 CanonicalReportingReadService.__post_init__ checks exact source types and evaluator._pinned_reader identity. It can be constructed through a public constructor or build_canonical_reporting_reader. Those checks alone do not authenticate trusted-host origin. The B5-1 reader's public constructor accepts a state, and B5-2 evaluator's constructor accepts reader/labels; a construction-only type check is insufficient.

The original _forecast_read_order module retains _register_origin, _activate_original and _issued_original, with process-wide original event ordering and terminal quarantine. Those mechanisms belong to the B5-1/B5-2 source boundary; B5-4-1 must not rewrite their S/P/Q ordering or claim that type checks substitute for issuance.

## Proposed host-only contract (not yet implemented)
1. Trusted host retains source authorities, authenticated actor, fixed approved action, visibility scope and exact B5-1/B5-2 dependencies. No selection, source, verifier, clock or dispatcher supplied by untrusted request data.
2. Authenticate origin of the exact reader and evaluator instances against an original host-controlled issuance/registry, not merely their classes or mutable attributes. A synthetic evaluator must be bound to this specific original reader. Distinguish host-owned trusted code from untrusted request code: no claim of protection from arbitrary code running with Python host internals.
3. Host-reviewed request approval binds exact canonical forecast selection; for synthetic accuracy also exact label selection; underlying original grant/principal identities; host identity, actor, action and visibility. A public data class, JSON, hash, copied view, caller-constructed wrapper or similarly shaped object is not an approval.
4. Once-only opaque request authority must be consumed at a well-defined, concurrency-safe point BEFORE exactly one original B5-3 dispatch. Subsequent or concurrent reuse denies without additional protected source lookup. Issuance/consumption interruption handling must be reviewed for fail-closed cleanup and process-wide quarantine.
5. B5-1/B5-2 source decisions remain independently authoritative for original grant expiry/revocation, mismatch, publication and cancellation. Host bootstrap may not renew authority, create a detached source outcome, add a second read or bypass terminal Q.
6. Preserve B5-3 output status mapping, immutable evidence, original float32/f64 semantics and synthetic-only limitations. No stronger external delivery or post-publication authorization implied.

## Mandatory design review before implementation
- Locate actual trusted host composition caller and test bootstrap paths; review public factory signatures and module import graph.
- Specify exact original provenance proof, registry ownership/lifetime, id collision resistance, actor binding, visibility checks, fixed dispatch, consumption linearization and lock order.
- Review how original _ORDER / _HOST_HEALTH signal and shutdown semantics interact; avoid public reset/recovery.
- State threat assumptions: CPython 3.11/3.12 process-private internals under trusted host ownership; do not advertise Python-level malicious-code isolation.

## Required tests after implementation
Authentic host invocation; forged public reader/evaluator/wrapper/request; cross-host, cross-actor, action, scope, selection and grant mismatch; copied/serialized grants and handles; revoked and expired underlying authorities; replay and concurrent same-handle use; cancellation and interruption at issuance/consumption; Q terminal; zero lookup on predispatch denial; unchanged B5-3 source evidence and negative statuses.

Retain original accepted unit and read-only PostgreSQL integration tests on disposable PG 16.15, following existing bootstrap.sql and migration procedure. Exclude the historically unsafe test_controlled_h1_execution.py suite. No ML fitting, workers or unrelated service activation.

## Explicitly deferred
No API/dashboard/PDF/export/jobs/scheduler/Celery/MLflow binding, operational actual labels, SQL/schema/migration changes, separate report P, external delivery fence, B5-4-2 or merge.

## Acceptance
Only after implementation, bounded verification, independent security/diff review and end-of-milestone local PostgreSQL verification. The document itself is not proof of running tests or of secure deployment.


## Implementation checkpoint — 2026-11-15 (unverified)
On isolated branch, a provenance registry now tracks the original B5-1 reader instance and original B5-2 evaluator instance; B5-3 constructor checks this host registry in addition to original identity bindings. The new `host_reporting_request_service.py` defines a host-retained `ReportingRequestIssuer` and consumer-facing `ReportingRequestDispatcher`, with an original non-serializable request object and one-use `dict.setdefault(0, marker)` before source dispatch. Fixed action/visibility modes: `FORECAST/full-evidence`, `SYNTHETIC_ACCURACY/synthetic-full-evidence`. Original grant/subject/selection and independent synthetic label subject are checked at issuance; underlying authority is rechecked before dispatch and again inside original services. This does not alter original source publication events.

**Separation requirement:** A request handler must NEVER receive `ReportingRequestIssuer`, original `ReadAuthority`/`ActualLabelAuthority`, operator object, protected original B5-3 reporting service, or raw original grants except through the protected host runtime. This is a trusted-host composition contract, not a Python security sandbox. The module's public constructors and mutable private attributes are not themselves a production authentication boundary.

**Remaining review risk:** Host-origin/provenance maps are process-private Python dictionaries. They do not resist malicious code executing inside the trusted process. The once-use setdefault linearization is CPython 3.11/3.12 and GIL dependent; CPython free-threaded or alternative interpreters must fail closed. No event-fence or distributed authorization guarantee is claimed. Validate that all multi-step issuance interruption paths, principal/clock callbacks, lock ordering and source identity checks fail closed. A request-handle expiry is based on original authority clocks; the exact semantics of host action and actor authentication must be established by a real integration caller before any operational use.

**Test state:** Added provenance, replay, concurrent same-handle, actor, action/visibility, expiry and revocation test cases, but none have been executed by this GitHub connector. Real disposable PostgreSQL 16.15 verification, focused CPython 3.11/3.12 tests, static/diff review, and independent security review are **PENDING**.

**Acceptance state:** OPEN / NO-GO. No merge, no production use, no B5-4-2. Prior accepted milestones are not automatically reaccepted following these changes.
