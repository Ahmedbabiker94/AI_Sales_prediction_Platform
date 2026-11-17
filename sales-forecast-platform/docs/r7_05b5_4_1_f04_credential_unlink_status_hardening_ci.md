# R7-05B5-4-1 F-04 — Strict credential unlink outcomes / exception guard

**Date:** 2026-11-17. **Status: candidate-only patch validated under mocks; accepted runner unchanged; F-04 OPEN / NO-GO.**

## Scope and finding

The prior `run_postgres_security_patch_candidate.py` set `credential_cleanup_attempted=True` just before invoking the normal unlink callback, but an injected `KeyboardInterrupt` inside the callback escaped without explicitly recording `credential_file_cleanup=UNCONFIRMED`. It also treated any returned status except the literal `UNCONFIRMED` as effectively successful, permitting malformed status values such as `UNKNOWN`, `None` or empty string to escape rejection.

This is a **review-fork status/exception contract gap**, not a confirmed live credential leak. No command, real disposable credentials, Docker or PostgreSQL was executed.

## Candidate-only correction

Both normal and interrupted-reconciliation fallback paths now accept only `REMOVED` and `NOT_PRESENT` as affirmative credential-file removal outcomes. Every other return value is normalized to `UNCONFIRMED`. If normal unlink raises `BaseException`, the record is explicitly marked `FAILED`, `cleanup_passed=False`, `cleanup_status=CLEANUP_UNCONFIRMED`, and the original exception re-raises; the outer finally restores secret/lifecycle ContextVars and SIGTERM handler without a second unlink attempt. The fallback also normalizes non-standard statuses to `UNCONFIRMED` and still attempts only the known `evidence/test-client.env`.

This distinction intentionally keeps the **existing failure cause** for an interrupted unlink while recording unsafe cleanup, and raises `CleanupUnconfirmed` for an unknown normal unlink result. Reviewer must assess exception precedence and the absence of a trustworthy manifest if the unlink itself is interrupted.

## Tests and CI

New `tests/runtime/test_r7_05b5_4_1_f04_credential_cleanup_status_contract.py` contributes **8 cases**:
- Unknown/empty/None/UNCONFIRMED result and interrupted normal unlink, expecting a one-time owned-path call, explicit rejected cleanup record, and context restoration.
- Unresolved reconciliation with fallback returning UNKNOWN, NOT_PRESENT or REMOVED, checking rejection/normalization and one unlink attempt.

GitHub Actions **run 44141916249** at head `3c982168878e4ddedde9131a87bcc2f927aca301`:
- Python 3.11: **249 passed / 1 failed (250 collected)**.
- Python 3.12: **249 passed / 1 failed (250 collected)**.
- Only failure remains the retained accepted-runner test `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]`; synthetic preflight RuntimeError instead of required CleanupUnconfirmed.
- The green count includes original vulnerability-demonstrating diagnostics, **not** independent security approval.
- Prior interim run 44141791433 had 5 failures due to a new test expecting original preflight RuntimeError for a now fail-closed `CleanupUnconfirmed` path. Test expectation was corrected to require the fail-closed exception, with assertions on record statuses intact.

## Boundaries

Protected accepted `tests/postgres/run_postgres.py` Git blob `0863e81b733381a80b2f83560d8bbc231513ba41` unchanged (expected `0863e81b733381a80b2f83560d8bbc231513ba41`). The candidate fork retains an unconditional no-subprocess guard and no CLI entrypoint.

Mocked interruption cannot guarantee recovery from hard kill, second SIGTERM, filesystem durability, or a malicious host. Further independent review is needed for real file lifecycle, finalizer accounting and controlled rebaseline, with separately authorized Docker/PG 16.15, OCI/16-wheel source review, and `_HOST_HEALTH` resolution. No merge or B5-4-2.

**F-04 OPEN / NO-GO.**
