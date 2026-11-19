# R7-05B5-4-1 / F-04 — Interrupted cleanup fallback corrected in review-only fork

**Date:** 2026-11-17. **Status: CANDIDATE MOCK-VERIFIED / ACCEPTED RUNNER STILL RED / NO-GO.**

## Candidate-only modification

`tests/postgres/run_postgres_security_patch_candidate.py` now retains a boolean `credential_cleanup_attempted` from the start of its guarded execution. The normal finalizer sets it **before** calling `remove_transient_credential_file(envfile)`. The outer `finally` attempts cleanup of **only** `evidence/test-client.env` when normal finalization did not reach that call (for example after `KeyboardInterrupt` during unresolved-daemon reconciliation).

- The fallback records the returned removal status; `UNCONFIRMED` or exception marks the candidate's record `FAILED` and `CLEANUP_UNCONFIRMED`. It does **not** unconditionally mark cleanup successful.
- The fallback attempts removal only once; the normal successful cleanup path does not invoke it a second time.
- Both ContextVars and the prior signal handler are restored in nested `finally` blocks, preserving the primary synthetic reconciliation interruption during the exercised cases.
- The candidate has a hard stop on unmocked command execution. No real Docker/PG or real credentials are used.

## Negative regression and targeted evidence

1. `tests/runtime/test_r7_05b5_4_1_f04_cleanup_interrupt_counterexample.py` was inverted: it now **requires** the exact owned envfile unlink attempt during a synthetic `KeyboardInterrupt` and no accepted manifest. It no longer passes by demonstrating missing cleanup.
2. `tests/runtime/test_r7_05b5_4_1_f04_credential_cleanup_fallback.py` adds **four** mock-only tests: interruption and `REMOVED`, interruption and `UNCONFIRMED`, interruption and a raised `KeyboardInterrupt` from unlink, and a normal preflight-error finalization that must call unlink exactly once.
3. `tests/runtime/test_r7_05b5_4_1_f04_fork_ast_operational_parity.py` explicitly documents the intentional change from one to **two source call sites**, and requires both to use the same `envfile` argument, the fallback being behind `not credential_cleanup_attempted`. This is a justified change in AST parity, **not** an ignored or disabled test.

GitHub Actions run **44141010943**, tested head commit `3b20a7fb3ce1302c62f72b47c6c59b0ca796075a`:
- CPython **3.11:** **241 passed, 1 failed (242 collected)**.
- CPython **3.12:** **241 passed, 1 failed (242 collected)**.
- The only failure on both remains the original protected-runner `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]`; original `RuntimeError` instead of `CleanupUnconfirmed`. Do **not** xfail, exclude, or weaken it.

## Residual review requirements

- A mocked unlink callback and synthetic interruption are not evidence that real disk credentials can be securely erased or that the real Docker daemon is quiescent.
- A second asynchronous interruption during the fallback itself cannot be ruled out with Python `finally`. The candidate catches a synthetic `BaseException` to record `UNCONFIRMED`, but hard process termination and OS-level signal behavior are not tested; the trusted host boundary remains.
- The fallback cannot retrospectively reconcile any ambiguous Docker creation request. Such outcomes remain unaccepted and must undergo independently authorized owned-resource audit.
- Test the final accepted runner under additional fault injection *after* separate permission for its protected security-only rebaseline, including credential unlink symlink refusal, evidence write failure, resource listing unavailable, and nested context restoration.
- Independent source/wheel/OCI/license/host-health review and separately authorized real disposable PostgreSQL 16.15 are still blockers.

**Protected accepted `tests/postgres/run_postgres.py` Git blob:** `0863e81b733381a80b2f83560d8bbc231513ba41` (expected `0863e81b733381a80b2f83560d8bbc231513ba41`). No protected edit, Docker/PG, merge, deployment, or B5-4-2.

**F-04 OPEN / NO-GO.**
