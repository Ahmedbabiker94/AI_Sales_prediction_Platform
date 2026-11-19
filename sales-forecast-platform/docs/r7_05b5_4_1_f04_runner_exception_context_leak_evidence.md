# R7-05B5-4-1 F-04 — Accepted-runner exception-path ContextVar retention demonstrated

**Evidence: 2026-11-17; source branch `fix/r7-05b5-4-1-bootstrap`; CI run 44116437425; commit `6f327d7d18097aab077b276b68d3dce4b3121c7b`.**
**State: TWO SOURCE-LEVEL SECURITY BUGS CONFIRMED BY MOCKED TESTS / NO-GO.**
No Docker or PostgreSQL executed. No change to accepted `run_postgres.py` blob `0863e81b733381a80b2f83560d8bbc231513ba41`.

## New observed test and evidence

`tests/runtime/test_r7_05b5_4_1_f04_runner_exception_counterexample.py` intentionally invokes the unmodified accepted runner `run()`, with Git preflight, Docker-image check, owned-resource cleanup and query commands all stubbed so **no daemon operation** occurs. It inserts an unserializable `object()` into lifecycle metadata immediately before a synthetic pre-Docker stop. This forces `json.dumps(record)` to raise during final manifest preparation.

The test PASSES **by demonstrating an unsafe condition**: after that exception, `_LIFECYCLE.get()` is `None`, but `_TEST_SECRETS.get()` still retains four generated disposable secret values. It cleans up that ContextVar explicitly in the test's own `finally`, and does not print or save the tokens. No real credentials were involved.

**Important:** A green counterexample is evidence of a defect, not evidence of security or authorization. This is a separate failure mechanism from the original redaction-count bug.

## GitHub CI result

Run **44116437425** verified both CPython **3.11** and **3.12**:
- **199 passed, 1 failed**, 200 tests collected on **each** Python version.
- The lone failing test remains `test_r7_05b5_4_1_f04_cleanup_lifecycle.py::test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]`: `RuntimeError` surfaced instead of mandatory `CleanupUnconfirmed`.
- The **new passing test is a deliberately vulnerability-confirming assertion**. Do not claim full 199 security tests passed or that F-04 has no other bugs.

## Invariant needed before any accepted-runner modification

1. Count generated-secret matches in serialized final manifest **without depending on a reset lifecycle ContextVar**; any detected occurrence must make `result=FAILED`, `cleanup_passed=False`, `cleanup_status=CLEANUP_UNCONFIRMED` before publication.
2. Restore `_TEST_SECRETS`, `_LIFECYCLE` and SIGTERM handler **exactly once on all errors**, including JSON encoding, user callback, credential-file cleanup, failed manifest publication and daemon listing. Avoid double reset or expansion of trusted resource ownership.
3. Preserve durable fail-stop classification and no PASS when manifest publication is missing/failed. Do not loosen filename symlink checks, bounded reconciliation, owned label checks, root-CHOWN-only exception, source provenance or no-network guarantees.
4. Replace the currently vulnerability-affirming counterexample with an explicit restoration/rejection regression **only after** a security-reviewed and expressly authorized limited protected-runner correction; don't turn it into a vacuous green test, xfail the original red test, or change protected files just to obtain a green CI indicator.
5. Rerun full F-04 suite and retained host security/unit suites after authorized diff. Real PostgreSQL 16.15 requires separate explicit opt-in and remains mandatory before B5-4-1 closure.

**Authorization boundary:** Candidate/proposal tests and documents may continue. No permission to edit protected runner, Dockerfile, requirements lock, run real Docker/PG, merge, deploy or start B5-4-2.

**Decision: F-04 OPEN / NO-GO.**
