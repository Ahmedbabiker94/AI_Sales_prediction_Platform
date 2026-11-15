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


## 2026-11-15 host-only contract correction and CI
- Focused GitHub Actions run `43707789179` executed the new host-boundary audit, the B5-4-1 unit tests and retained B5-3 regression suite. **44 passed on CPython 3.11 and 44 passed on CPython 3.12**, as confirmed from job logs.
- Updated `host_reporting_request_service.py` documentation to require BOTH the issuer and dispatcher remain within the trusted host. Neither Python object nor its original grants/request handles should be exposed to external or untrusted Python consumers. This is a contract correction only, **not a newly enforced process boundary**.
- The audit test deliberately proves the raw dispatcher exposes its original Reader and pending grant; therefore the threat-model finding is still **OPEN** whenever an untrusted Python component can hold a dispatcher reference.
- Existing operational API entrypoints are not currently bound to B5-4-1 in the inspected files. No complete caller/import/deployment graph is proven and no external transport or independent reviewer has been added.
- PostgreSQL 16.15 disposable integration remains **UNEXECUTED**. Acceptance remains **NO-GO**, and the isolated Draft PR must not be merged.


## Adversarial in-process bypass reproduction — 2026-11-15

Added `test_exposed_dispatcher_reference_allows_bypass_of_request_one_use` to the isolated host-boundary audit. Under the existing synthetic original forecast grant, it consumes an approval through `dispatcher.execute` once (second call DENIED), then accesses `dispatcher._reader` and the captured `_Approved.forecast_grant` to invoke the original read source a second time. This deliberately demonstrates why *exposing the dispatcher to untrusted Python is unsafe*. It does **not** show a bypass of the correctly deployed host-only boundary: original grants are designed to allow original source reads while valid, and the hostile caller only gets them if the host leaks a Python reference.

Expected test behavior: succeed as a *negative security finding* while two original source lookups occur; do not interpret a green test as isolation. The code and test avoid creating new SQL, schema, migration, transport, or API. No fix to the process-isolation blocker is claimed.

A compliant controlled-offline composition must retain issuer, dispatcher, original source references and request handle within trusted code. Untrusted clients may receive only reviewed result DTOs through a distinct transport owned by host; implementation and actual adversarial boundary verification are not present. If no untrusted clients are in scope for B5-4-1, narrow the formal acceptance statement to trusted-host accidental misuse/replay prevention and obtain an independent review explicitly accepting that threat model.

PostgreSQL integration is still unexecuted. The execution environment inspected here had no `postgres`, `psql` or `docker` executable, and its Python is 3.13.5. It is not an admissible substitute for the preapproved PG16.15 disposable harness.


## 2026-11-15 operational Runtime direct-injection guard (partial mitigation)
- Inspected `src/runtime/bootstrap.py`: it allows named trusted startup factories whose return values were previously unchecked beyond `None`. Added a check rejecting a *direct* `ReportingRequestIssuer` or `ReportingRequestDispatcher` instance from a `Runtime.get` factory. The check does not import the protected service and leaves unrelated factory objects untouched.
- Added focused test cases for direct rejection of each exact protected type and unchanged ordinary factory behavior. A focused CI attempt (run `43708368942`) reported **47 passed, 1 failed** on CPython 3.11: the previous import audit conflated a literal module-name comparison with importing the module. Corrected that test to inspect actual imports in `src/runtime/bootstrap.py` while preserving the stricter string check on `api/main.py`. Await refreshed CI evidence before claiming success.
- This is **NOT** an isolation fix. Wrappers or closures may retain a protected object and a malicious same-process caller can inspect Python attributes. The direct-injection guard reduces inadvertent registration of the exact protected objects only. Neither provenance nor original grant remains secure against hostile in-process execution. Treat this issue as BLOCKER unless independent review explicitly accepts the strictly trusted-host-only threat model.
- PostgreSQL 16.15 disposable integration remains unexecuted. No merge and no B5-4-2.


## 2026-11-15 scope rollback (current branch head)
The temporary B5-4-1 module-name/typename rejection in `src/runtime/bootstrap.py` was **reverted** in commit `d35ad902`, restoring its original content SHA `afba0003be6fe0a76306ef17795115a4f718e141`. Related direct-injection tests and workflow references were removed. Reason: it was an unrelated general Runtime edit, could be bypassed by ordinary wrappers, and was not an enforceable security boundary. It must not be described as accepted hardening. B5-4-1 source code, negative in-process exposure demonstrations and the host-only deployment restriction remain.

**Security decision:** Treat B5-4-1 as controlled-offline **trusted-host-only** code. Only already-trusted host composition may hold the issuer, dispatcher, original request handle, and source grants. Any claim against untrusted Python in the same interpreter, or a public API/client, requires a separately implemented and reviewed process/transport boundary. No such boundary has been implemented. Current absence of operational caller wiring is not a substitute for proof of deployed isolation.

**Acceptance still blocked:** independent security review of the scoped threat model and interruption behavior; complete approved disposable PostgreSQL 16.15 integration logs; explicit review of any host composition caller before future activation. Do not merge or advance to B5-4-2.


## 2026-11-15 PostgreSQL optional host-stage runner safety checkpoint

- Added `tests/postgres/run_b5_4_1_postgres.py` as an **opt-in** extension of the original `tests/postgres/run_postgres.py` lifecycle. Pinned B5-2, B5-3 and B5-4-1 PostgreSQL test modules execute only after original foundation migration and server restart stages. No arbitrary test selector, DSN, network port or production connection was added.
- `run_postgres.py` cleanup ledger was minimally expanded to include `record['extra_containers']`; the extra container is registered before invocation and is run-scoped and label-checked by existing cleanup. This must still receive independent review because it touches accepted harness code.
- Added six no-Docker lifecycle tests for success path, nonzero pytest exit, simulated KeyboardInterrupt/SystemExit/TimeoutError and rejection when original lifecycle is absent.
- Focused GitHub Actions **run 43727145923**, commit `eb5c94f2`: **51 passed on CPython 3.11** and **51 passed on CPython 3.12**. The new tests use a mocked Docker command and do NOT prove a database actually ran, cleanup of daemon-side ambiguous mutations, or PostgreSQL 16.15 behavior.
- The disposable PostgreSQL stage remains **UNEXECUTED**. It requires the exact pinned images and verified wheelhouse on a trusted compatible Docker host. Do not claim completion based on py_compile or mocked lifecycle tests.
- Trust-boundary finding remains OPEN; independent code/security review and actual isolated PostgreSQL evidence remain mandatory. Acceptance **NO-GO**, no merge.
