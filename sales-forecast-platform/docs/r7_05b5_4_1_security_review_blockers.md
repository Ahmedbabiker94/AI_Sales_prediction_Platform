# R7-05B5-4-1 security boundary review — BLOCKER

**Date:** 2026-11-15
**Reviewed branch:** `fix/r7-05b5-4-1-bootstrap`
**Last observed focused CI:** run `43706510051`, 41 passed on CPython 3.11 and 41 passed on CPython 3.12 (focused unit + retained B5-3 contracts).
**Classification:** INTERNAL CODE REVIEW, **not independent external review**.
**Disposition:** OPEN / NO-GO. Do not merge, deploy, expose to request handlers, or proceed to B5-4-2.

## CRITICAL: dispatcher object exposes original access capabilities

**Location:** `src/services/host_reporting_request_service.py`, `ReportingRequestDispatcher.__init__`, `execute`, `_Approved`, and `build_host_reporting_requests`.

The supposedly consumer-facing dispatcher stores `_reader`, `_evaluator`, `_records`, `_issuer_identity` and `_registry_lock` as accessible Python attributes. Pending registry records retain `_Approved.forecast_grant`, `_Approved.label_grant`, exact selection JSON and actor/visibility metadata. The reader retains protected state and operator reference. A Python consumer that can inspect this dispatcher can retrieve original grant and source references; it can avoid the one-use dispatcher path and call the original B5-1/B5-2 sources instead. Python `__slots__`, underscore names and type checks are *not* isolation.

**Effect:** One-use protections are verified for `dispatcher.execute(handle)` under the documented trusted host assumptions, but **NOT a security boundary against any untrusted Python component that receives the dispatcher**. The previous statement "only dispatcher + opaque request go to consumer" cannot be interpreted as secure capability minimization in a shared interpreter.

**Remediation options, requiring explicit architecture review:**
- Keep both issuer and dispatcher *inside the trusted host process*. Expose ONLY a narrow host-owned request/response transport to externally untrusted components, with the trusted host authenticating and authorizing each request. Never send Python objects, grants, readers, registry, operator or raw result capabilities across the boundary.
- If all callers are trusted internal host code (the current controlled-offline assumption), state clearly that this provides accidental misuse/replay prevention, not security against hostile code running in the same interpreter. Ensure no operational integration exposes these objects.

**Mandatory evidence:** an inspected deployment/composition call graph proving the non-host caller cannot reference the Python dispatcher, issuer, original authority, reader or registry; threat-model decision; adversarial test at the chosen actual boundary. No fixed-size refactor or one more ordinary unit test alone resolves the isolation issue.

## IMPORTANT: interruption, lifetime and registry

- `take_once` uses the original `_locked_call` protection and removes a handle before dispatch. Injected `KeyboardInterrupt`/`SystemExit` tests check selected interruption points; they are not exhaustive proof for all native signal timings or crash-restart ordering.
- Pending entries are capped at 4096, consumed entries removed. Expired *unconsumed* approvals are retained until execution or process restart; the registry may permanently fail closed at capacity. No unsolicited eviction or reset is permitted without new review.
- Original grants are revalidated before dispatch and by original B5-1/B5-2. This is not atomic distributed authorization or an external-report delivery fence.
- The contract assumes CPython 3.11/3.12 private internals and trusted-process health. No free-threaded, alternate-runtime, or malicious in-process code guarantees.

## PostgreSQL 16.15 verification — NOT EXECUTED

Added `tests/postgres/test_r7_05b5_4_1_host_reporting_requests.py` with 4 parameterized cases: original SELECT-only forecast/accuracy evidence and original preconnection revocation denial.

Do not run that module against production, a shared cluster, or with a generic DSN. Its original `tests/postgres/conftest.py` enforces disposable `R7_PG_INSTANCE` + exact `R7_PG_DATABASE`, `/run/r7pg` Unix socket, and explicit `R7_PG_{ADMIN,MIGRATOR,WRITER,READER}_PASSWORD` credentials. Use the existing reviewed `tests/postgres/bootstrap.sql` in a new isolated PG16.15 database and **explicit** `apply_forecast_evidence_migration(target('migrator'))` before this module. The reporting schema precondition is version 1 and is intentionally incompatible with running the pre-migration `migrated` fixture in an already migrated database. Application readers remain SELECT-only; only test setup/migration write.

The GitHub-hosted focused workflow does **not** provision this PostgreSQL environment. No PG integration success may be claimed until an actual isolated harness and logs are supplied. The present assistant execution environment has no `psql`, `postgres`, or `docker` executable and uses Python 3.13, so it cannot serve as the accepted CPython/PG16.15 proof.

## Next acceptance gates

1. Decide whether the B5-4-1 security requirement is only trusted-host accidental misuse protection or an actual untrusted request boundary. The latter requires architectural isolation and an adversarial test. **BLOCKER.**
2. Obtain an actual, separately performed security review and address all BLOCKER/IMPORTANT issues.
3. Execute all retained read-only PG16.15 integration tests using the approved disposable harness, collect concrete logs.
4. Only then consider milestone acceptance. **No merge without explicit authorization.**


## 2026-11-15 boundary proof checkpoint
- Added `tests/runtime/test_r7_05b5_4_1_host_boundary_audit.py`. The first test intentionally demonstrates that a same-interpreter caller holding the dispatcher can inspect `dispatcher._reader` and obtain an original pending `forecast_grant` from `dispatcher._records`. This is evidence of the BLOCKER, not a successful boundary defense.
- The second test statically checks current `api/main.py` and `src/runtime/bootstrap.py` have no direct B5-4-1 service imports. It does **not** prove a complete import/deployment graph, absence of dynamic indirect references, or future callers.
- Both issuer and dispatcher must remain host-private; request consumers must not receive either object. A future untrusted transport boundary is **NOT IMPLEMENTED** and remains out of current operational integration. If separate-process authentication/transport is desired, review the scope as an explicit architecture decision first; do not bolt an unaudited API onto this milestone.
- A CI update adds this module to the focused verification command. Do not claim it passed until job logs verify the new module actually ran.
