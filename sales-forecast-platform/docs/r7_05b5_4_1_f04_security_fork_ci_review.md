# R7-05B5-4-1 / F-04 — Isolated corrected-runner fork tested; protected runner still RED

**Date:** 2026-11-17. **Status: CANDIDATE PATCH / REVIEW REQUIRED / NO-GO.**

## Bounded changes

A separate file, `tests/postgres/run_postgres_security_patch_candidate.py`, was derived from original `tests/postgres/run_postgres.py`. **The accepted file is UNCHANGED at Git blob `0863e81b733381a80b2f83560d8bbc231513ba41`**.

The candidate fork:
- Removes the CLI `__main__` entrypoint but retains the original runner functions for **mock-only review**; it must **never** be used as an actual Docker/PostgreSQL runner.
- Guards setup after lifecycle binding under a single outer `try/finally`, including errors during handler retrieval/installation and password generation; tracks attempted signal handler replacement.
- Defer-restores the prior SIGTERM handler and both ContextVar tokens exactly once in layered `finally` blocks, including cleanup/serialization/writing exceptions.
- Uses `redaction_finalization_candidate.finalize_manifest_candidate(record, tuple(passwords.values()))` for final manifest status and exact disposable-secret redaction counts independent of ambient lifecycle ContextVar.
- Keeps original Docker/source guard and cleanup command text intact; **does not update OCI references, Dockerfile, requirements.lock, allowed selectors or installation artifacts**.
- Produces an explicit `CleanupUnconfirmed` when the pure manifest finalizer flags an unexpected secret despite otherwise successful mocked ownership cleanup.

## GitHub Actions result

Workflow `R7 B5-4-1 F-04 candidate offline tests`, run **44130680979**, tested commit `69baff2315a86fed256df4eb212f262d6559dbf5`:
- **CPython 3.11:** 218 passed, 1 failed (219 collected).
- **CPython 3.12:** 218 passed, 1 failed (219 collected).
- **All five new mock-only fork tests passed**, exercising unexpected manifest secret, invalid serialized metadata, evidence-write error, signal registration error and password generation error, with ContextVars cleaned and no Docker calls.
- The **only failed test** continues to be `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]` from the **unchanged accepted** `run_postgres.py`. It must not be skipped, xfailed or rewritten to hide the defect.
- Note: Additional passing *diagnostic* tests deliberately assert presence of original runner vulnerabilities. **218 passes are not security signoff**.

## Important remaining candidate review concerns

1. Mocked handler installation/restoration is not OS signal concurrency validation. It does not exercise an abrupt native process termination or all possible `BaseException` timing windows.
2. The fork still contains original code with real Docker commands if a caller invokes `run()` outside mocked tests, despite lacking a CLI entrypoint. Do not invoke it with `allow=True` on any host. Only mocked tests are authorized.
3. A full post-rebaseline review must test **original runner's own tests against the changed accepted file**, including clean success, daemon reconciliation, write failures and ContextVar reset under signals.
4. The pure finalizer's snapshot of a lifecycle record is shallow; nested objects must be JSON-serializable. Invalid metadata must remain terminal and must not result in false PASS or publish partial secrets.
5. The tested corrected fork is an independent candidate, **not a patch applied or permission to replace** `run_postgres.py`.
6. Original OCI pins, Python base and hashed 16-wheel source-to-build provenance, independent license/OSV review, Docker29 runtime flags and earlier `_HOST_HEALTH` discrepancy are still acceptance blockers.

## Explicit authorization gate

Only a separately approved controlled-rebaseline security-diff may modify `tests/postgres/run_postgres.py`. The candidate fork and related test helpers do **not** authorize the broader CR-2/CR-3 OCI/Docker rebaseline, real disposable PostgreSQL 16.15, production, merge or B5-4-2.

**R7-05B5-4-1 / F-04 OPEN / NO-GO.**
