# R7-05B5-4-1 F-04 — First CI evidence and actual redaction gate defect

**Evidence date:** 2026-11-17
**Observed workflow:** GitHub Actions run 44108955521, commit `14ef262aa9757d62b0010cb8fea348a12ef5f5e5`, Draft PR #1.
**Outcome:** F-04 candidate-only CPython 3.11 and 3.12 matrix **FAILED**, each **170 passed / 5 failed** among 175 collected executions. No Docker or PostgreSQL commands were run. Compilation and Python-only install passed in both jobs.

## Four test-expectation mistakes (corrected in candidate tests)

Four adversarial `test_r7_05b5_4_1_f04_run_plan_adversarial_audit.py` cases still asserted that candidate validation accepted a foreign ownership marker or an argument after the image. The tightened candidate correctly raises `ValueError`. The stale test assertions have now been fixed to require rejection. This is only a test correction; the accepted runtime has not been changed.

## Actual blocking source bug: secret-redaction fail-stop can be bypassed

**Failing case:** `test_r7_05b5_4_1_f04_cleanup_lifecycle.py::test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]`.

**Observed CI failure:** `RuntimeError: synthetic preflight stop: no Docker used` emerged instead of expected `CleanupUnconfirmed`; the test injects a disposable test-only secret in unexpected manifest metadata and correctly requires fail-closed cleanup.

**Source reason:** in accepted `tests/postgres/run_postgres.py`, `run().finally` resets `_LIFECYCLE` via `_LIFECYCLE.reset(token)` **before** invoking `redact_test_secrets(json.dumps(record,...))`. Although `redact_test_secrets` replaces matching text, it records `secret_redactions` only when `_LIFECYCLE.get()` returns an active record. Because the context has been reset already, the secret is redacted but `record['secret_redactions']` remains zero. Therefore `record['cleanup_passed']` may remain True and `CLEANUP_UNCONFIRMED` is not raised on *unexpected manifest secret occurrence* if all other cleanup signals report success.

**Threat characterization:** This is a real **failure-classification/integrity** bug in the approved runner, not evidence that a secret leaked into the redacted manifest; the redact operation may still replace it. It matters because the documented promise is *any detected redaction is a rejected run*. No PostgreSQL or Docker runtime data or production secret was involved in reproduction.

**Disposition:** **SECURITY BLOCKER / NO-GO.** Keep the regression red until a reviewed, narrowly scoped corrective implementation can be authorized; do NOT weaken it by replacing `CleanupUnconfirmed` expectation with `RuntimeError`, and do NOT treat four green fixes as clearance. A prospective fix should count redactions directly independently of ContextVar lifetime, preserve source material and credential cleanup, and keep the manifest redacted. Review ContextVar reset/error paths and test both unexpected secret and no-secret cases. The original `run_postgres.py` is **unchanged** pending explicit controlled-rebaseline/security authorization.

## Additional unfixed acceptance gates

- Original PostgreSQL and client image pins/build still need independently approved controlled rebaseline.
- All 16 candidate-v2 wheel bytes, lock, ABI, licensing and provenance require independent review.
- Actual Docker 29 no-pull/platform behavior, trusted-host boundary and prior intermittent `_HOST_HEALTH` remain unresolved.
- Real disposable PG16.15 foundation/restart/B5-4-1 acceptance test requires distinct opt-in and inspected cleanup before milestone signoff.

**No runner/Dockerfile/lock changes. No merge, deployment or B5-4-2.**
