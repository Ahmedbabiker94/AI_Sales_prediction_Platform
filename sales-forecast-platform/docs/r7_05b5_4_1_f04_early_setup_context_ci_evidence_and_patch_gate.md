# R7-05B5-4-1 / F-04 — Early setup ContextVar leak confirmation & protected patch gate

**2026-11-17. OPEN / NO-GO. Not authorization to modify accepted harness, Dockerfile, or lock.**

## Evidence acquired

GitHub Actions **run 44119484563**, head SHA `1223d7eea0837b5e7b7e9b8089faadfdc6bd0bcc`, compiled and collected the new `test_r7_05b5_4_1_f04_early_setup_counterexamples.py` with the F-04 candidate suite. **CPython 3.11: 201 passed / 1 failed (202 collected); CPython 3.12: 201 passed / 1 failed (202 collected).** The lone failure remains the retained real-runner secret redaction classification regression `unexpected_manifest_secret`: original synthetic RuntimeError emerges rather than required CleanupUnconfirmed.

Two extra *passing vulnerability demonstrations* now prove that accepted `run_postgres.run()` binds `_LIFECYCLE` before setting SIGTERM handler and generating test-only passwords, and that errors in either setup operation can escape **before its `try/finally`**. In both fully mocked cases, the lifecycle ContextVar remains bound on exit. The tests safely stub the runner's `signal` namespace to avoid changing actual CI process signal handlers and reset the test-only ContextVar afterward. No Docker, PostgreSQL, registry or real credentials used.

Previous confirmation still applies: a JSON-serialization exception after the inner lifecycle reset can retain four generated disposable credentials in `_TEST_SECRETS`. The new tests are separate early setup variants, not redundant safety validations.

## Proven defect inventory in unchanged accepted file

1. Manifest redaction is text-effective but its **counting** relies on `_LIFECYCLE` that was already reset; resulting failure classification can remain incorrectly successful.
2. Exception before manifest write can leave `_TEST_SECRETS` still bound after `_LIFECYCLE` has reset.
3. SIGTERM registration failure / password generation failure can leave `_LIFECYCLE` still bound because they occur before `try`.

**All three are source/mock evidence, not proof of a real host secret disclosure.**

## Narrow, separate protected-runner patch review prerequisites

Before an approved protected edit, reviewer must accept a *single cohesive exception-safe ownership design*:
- Establish an outer try/finally that starts **immediately after** lifecycle token binding, guarding all subsequent setup. Do not add a cleanup call that affects volumes before there are registered owned volumes; respect existing preflight and resource ledger semantics.
- Track whether SIGTERM handler was actually installed and whether secret token was actually bound; restore only installed/bound state **exactly once**, including early setup failures. Preserve original exception causality where practical.
- Ensure final manifest serialization detects/counts unexpected disposable secrets **independently of reset ContextVar**, with fail-stop status and redacted output. Never silently accept an unexpected manifest field or an unconfirmed credential file unlink.
- On finalizer serialization/evidence write errors, never report PASS, leak a context token, perform an unowned delete, or assert that cleanup was confirmed. Record unavailable evidence where feasible without fabricating a manifest.
- Preserve original source cleanliness, fixed test selectors, server socket isolation, timeout/ambiguity controls, and all approved protected file digests until independently reviewed controlled rebaseline authorization.

Reviewer must demand *negative acceptance tests* that assert the desired **absence** of leaked ContextVars. Existing passing counterexample tests are diagnostic and should not remain as safety assertions after correction; the red case must stay unmodified until the accepted runner actually changes.

## Remaining gates

Controlled security/rebaseline review, 16-wheel v2 bytes+lock verification, OSV/license/provenance assessment, real Docker 29 no-pull indexed image behavior, prior intermittent HostHealth disposition, and separately permitted real disposable PostgreSQL 16.15 integration with owned cleanup.

**F-04 OPEN / NO-GO; B5-4-2 blocked.** No runner edit, Docker, PostgreSQL, merge, or deployment in this checkpoint.
