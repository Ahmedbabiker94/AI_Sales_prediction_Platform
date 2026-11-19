# Phase 7 / R7-05B5-4-1 / F-04 — R2-04: ambiguous publication and fail-closed evidence admission

**Date:** 2026-11-18.
**Finding:** Codex AI R2-04, MEDIUM.
**Owner directive:** Codex `CHANGES REQUIRED` accepted and candidate-only sequential test-first corrections permitted. **No protected-runner rebaseline, Docker/PG execution, owner security signoff or deployment authorization.**
**Decision:** **R2-04 TESTED CANDIDATE EVIDENCE-ADMISSION CONTRACT GREEN, with operational consumer-integration limitation**; F-04 **OPEN / NO-GO**, B5-4-2 **BLOCKED**.

## 1. Exact source identities

Repository `Ahmedbabiker94/AI_Sales_prediction_Platform`, branch `fix/r7-05b5-4-1-bootstrap`.

| Artifact | Git SHA |
|---|---|
| Frozen accepted `tests/postgres/run_postgres.py` | blob `0863e81b733381a80b2f83560d8bbc231513ba41` — **unchanged** |
| Review-only candidate `tests/postgres/run_postgres_security_patch_candidate.py` | blob `ccd1efac292d9504a59eeef3eaf8b21889879b59` |
| R2-04 tests `tests/runtime/test_r7_05b5_4_1_f04_r2_04_publication_acceptance.py` | blob `ea0e0e0b050854a96ba2bb084291f2c25268d527` |
| Pure finalizer `tests/postgres/redaction_finalization_candidate.py` | blob `86f0b33ea460eec078991ba1f70d9c9c9ba67bae` — **unchanged** |
| Candidate offline workflow `.github/workflows/r7-b5-4-1-f04-candidate-offline.yml` | blob `6fced607a0db482299a7184835a1e7c2d3a53bc1` |
| **Code-tested correction commit** | `335df73f707b47577bfe4a603c960bf7f1030a82` |

All Python/test paths are relative to `sales-forecast-platform/`; the workflow is repository-relative.

## 2. Precise failure and security boundary

Original candidate-only `publish_terminal_manifest_candidate()` writes the complete JSON bytes to a protected same-directory staging file and creates `run-manifest.json` via hard link. `os.link` can create the link and subsequently surface an exception in a fault model, while the rollback `path.unlink()` fails or identity verification becomes unavailable. The caller then reports `FAILED / EVIDENCE_UNAVAILABLE`, but the accepted-looking terminal filename may still contain a **full `PASS / AVAILABLE` manifest**.

Atomic visibility, protected stage permissions, and best-effort rollback are not a *transactional acknowledgement* of runner success. No portable local-filesystem-only protocol can guarantee the publisher can remove a file after arbitrary storage failures, nor prove crash durability without additional assumptions.

**Explicit admission contract:** A bare manifest is **never** sufficient proof of successful completion. A consumer must independently witness that the same `run()` invocation returned normally (a trusted controller result, not a field read from the manifest), and must then verify terminal-manifest status and cleanup fields. A failed/unknown invocation must be rejected even when a complete `PASS` filename remains. This guard is implemented as `terminal_manifest_accepted_candidate(path, *, run_completed)` in the **review-only candidate**.

**Trust limitation:** This helper expects `run_completed` to come from a trusted controlling caller; a caller merely inventing `True` can bypass it, and a raw-file consumer that does not use the helper remains exposed. No existing operational consumer integration was established by these candidate-only tests. This does **not** claim the filesystem rollback weakness disappeared. Operational deployment must integrate and audit every admission boundary, or a stronger explicitly designed attestation protocol must be reviewed. This limitation is submitted for fresh Codex/owner review rather than silently waived.

## 3. Test-first RED

Created seven candidate-only tests in `test_r7_05b5_4_1_f04_r2_04_publication_acceptance.py`, explicitly included in offline matrix:

- Hard link successfully creates `run-manifest.json`, then raises, while **destination rollback unlink** raises. The file remains `PASS`; caller record is `FAILED / EVIDENCE_UNAVAILABLE`.
- Hard link creates terminal file and raises, while **samefile inode verification** raises. The file remains `PASS` and caller reports failure.
- Successful full mock run: positive trusted run completion accepts, lack of positive confirmation rejects.
- Four invalid success claims: integer `run_completed=1`, nonzero redaction count, `cleanup_passed=False`, absent terminal file; all must be rejected.

The test harness uses only a fake Docker command, fake signals, synthetic identities, controlled temporary files, and simulated `os.link`/`unlink`/`samefile` I/O errors. No real Docker, PostgreSQL, subprocesses, OS signals, production secrets or network operations.

**RED:** [candidate offline CI 44288269648](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44288269648) at test/workflow commit `b1b1c2034d73e5fc399a99fce16c2808754d67bf` — Python 3.11 **311 passed / 8 failed (319 collected)**; Python 3.12 **311 passed / 8 failed**. All seven new evidence-admission cases were RED against an intentionally unsafe bare-file consumer fallback, plus the unchanged original frozen-runner redaction RED.

## 4. Candidate-only correction

Added `terminal_manifest_accepted_candidate(path, *, run_completed)` to the **review-only** candidate, not the protected runner. It:

- Requires `run_completed is True` exactly, not truthy integers or custom equality.
- Requires the exact `run-manifest.json` name, non-symlink path, resolved canonical path and existing regular file.
- Parses well-formed JSON with a plain dictionary root and rejects missing/malformed data.
- Requires terminal `result='PASS'`, `evidence_status='AVAILABLE'`, `cleanup_passed is True`, `cleanup_status='CLEANUP_SUCCESS'`, `handler_restoration='RESTORED'`, exact legitimate credential-unlink status, and `secret_redactions=0` of builtin `int` type.
- Returns False for missing/unreadable/invalid input; the helper neither performs cleanup nor mutates evidence.

**Important:** This is an **admission validation contract**, not an atomic filesystem rollback fix. The helper by itself does not automatically enforce usage by arbitrary external readers. The publisher's existing best-effort owned-inode rollback remains as before; after a combined fault, the raw terminal PASS may still exist. A fresh AI review must evaluate the admission policy and proposed operational consumer integration, and may request a stronger completion-attestation mechanism.

The candidate's unconditional `OFFLINE_REVIEW_ONLY` command guard and absent CLI entrypoint remain intact; its original fixed OCI pins, wheel hashes, PG selectors, owned-resource scope and source provenance checks are not relaxed.

## 5. GREEN and remaining failures

[Post-fix candidate offline CI 44288582457](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44288582457), code-tested commit `335df73f707b47577bfe4a603c960bf7f1030a82`: **Python 3.11 and 3.12 each 318 passed / 1 failed (319 collected)**. All seven new R2-04 tests and the previous R2-01/R2-02/R2-03/H1/H2/M1/M2/T1/T2 candidate-only tests passed. The **single unchanged protected accepted-runner** `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]` remains RED, so the full CI conclusion is still **FAILURE**.

[Focused offline verification 44288582354](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44288582354) succeeded. [Read-only host prerequisites 44288582303](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44288582303) failed. Docker29/pinned OCI local availability/no-pull, verified sixteen-wheel v2 bytes/OSV/license/provenance, `_HOST_HEALTH`, and separately authorized disposable PostgreSQL16.15 remain blocked.

## 6. Decision and next sequential gate

**R2-04 seven synthetic tests are GREEN, but the raw-file acceptance hazard and need for consumer-wide integration are not claimed closed for operations.** Next: **fresh Codex AI security re-review** of the combined R2-01–R2-04 candidate and this remaining admission trust boundary, using exact SHAs above. Codex may decide `CHANGES REQUIRED`; the owner then personally evaluates findings under F04-G01. A **separate, explicitly scoped owner authorization** would still be needed before any protected `run_postgres.py` edit.

**NOT AUTHORIZED:** protected-runner rebaseline, weakening original RED test, independent human signoff claim, OCI/wheel mutation, real Docker/PG invocation, merge, deployment, F-04 closure, or B5-4-2 advancement.

**Current status: F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**
