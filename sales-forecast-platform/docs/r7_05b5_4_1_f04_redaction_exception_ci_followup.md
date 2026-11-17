# F-04 redaction candidate: exception paths verified in offline CI

**2026-11-17 — NO-GO / protected runner unchanged.**

Source revision `ed9c522a25c17f544dc761edc7c9cf96c2665710`, GitHub Actions run **44113405028**:
- CPython **3.11: 198 passed, 1 failed (199 collected)**.
- CPython **3.12: 198 passed, 1 failed (199 collected)**.
- The single failure on both matrices is the **original protected-runner security regression** `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]`, still raising the initial `RuntimeError` instead of `CleanupUnconfirmed`.
- The five additional candidate exception-path tests in `test_r7_05b5_4_1_f04_redaction_exception_review.py` are now part of the CI run and passed. They reject invalid JSON serialization and non-standard NaN values; `redaction_finalization_candidate.py` now uses `json.dumps(..., allow_nan=False)`.
- No Docker, real PostgreSQL, registry, wheel acquisition, Runner/Dockerfile/lock changes or signoff occurred.

## Remaining source-level exception risk

The accepted `run_postgres.py` resets `_LIFECYCLE` before manifest accounting and only resets `_TEST_SECRETS` in the final manifest-write `finally`. A serialization error before that point could bypass the secret ContextVar reset. This remains **not directly demonstrated** by the new candidate tests and must be covered with actual runner fault-injection after authorized controlled rebaseline design review.

**Proposed remediation review:** `docs/r7_05b5_4_1_f04_runner_redaction_controlled_patch_spec.md`. Do not move the lifecycle reset without reviewing all exception paths; design guaranteed exactly-once cleanup and secret ContextVar restoration, count manifest redactions without relying on the reset lifecycle, and preserve the red regression until the accepted runner is corrected.

**R7-05B5-4-1 / F-04 OPEN / NO-GO. No merge, deployment, B5-4-2 or real disposable PostgreSQL integration.**
