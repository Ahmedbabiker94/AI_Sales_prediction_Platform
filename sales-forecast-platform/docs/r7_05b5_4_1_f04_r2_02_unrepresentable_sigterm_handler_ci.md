# Phase 7 / R7-05B5-4-1 / F-04 — R2-02: unrepresentable prior SIGTERM handler

**Recorded:** 2026-11-18. **Security review finding:** Codex AI R2-02, MEDIUM.
**Owner direction:** `CHANGES REQUIRED` accepted; candidate-only, test-first remediation authorized.
**Decision:** **R2-02 TARGETED SYNTHETIC OFFLINE TEST GATE PASSED**, not an independent human security audit, approval to patch frozen runner, or operational readiness. **F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**

## Exact source identity

- **Frozen accepted runner**: `tests/postgres/run_postgres.py`, Git blob `0863e81b733381a80b2f83560d8bbc231513ba41` — verified **unchanged**.
- **Review-only candidate**: `tests/postgres/run_postgres_security_patch_candidate.py`, Git blob `e9e43c4ce4da9e21cb0c46d9766f8221cf4cad8c`.
- **Three new tests**: `tests/runtime/test_r7_05b5_4_1_f04_r2_02_unrepresentable_sigterm.py`, Git blob `941082f9f6e5df3383e755bd13859e92934327a7`.
- **Candidate CI workflow**: `.github/workflows/r7-b5-4-1-f04-candidate-offline.yml`, Git blob `691486ae922b6882c7d3ff986ca70b3ce57dab95`.
- **Final code-tested commit**: `6c9949d751f6ec3ce8df18f1ea0744897d303b3a`.

All test source paths above are relative to `sales-forecast-platform/`, but the CI workflow path is relative to the repository root.

## Root cause

Python `signal.getsignal(SIGTERM)` may return `None` when the current handler is not representable by the Python signal interface, for example if installed by native code. In the R2-01 candidate, `run()` still replaced the SIGTERM handler, while `restore_handler_once()` required `previous_term is not None` and silently skipped restoration on this value. A superficially successful mocked main body could therefore publish PASS despite leaving the runner callback installed.

A safe operational implementation cannot restore a handler it cannot represent. **R2-02 chooses to reject BEFORE installation**, rather than trying an unreviewed native-signal restoration mechanism.

## Test-first RED

The R2-02 tests were authored and explicitly added to the candidate-only pytest workflow before the candidate implementation was patched.

[RED CI run **44285456058**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44285456058), code commit `2ae68499006b04cda39859152c1e0bd640ec9f00`: Python 3.11 **305 passed / 3 failed (308 collected)** and Python 3.12 **305 passed / 3 failed (308 collected)**.

- New RED case 1: `getsignal(SIGTERM) -> None` in an otherwise empty outer context: pre-install rejection did not happen and fake `inspect_image` reached the forbidden preflight.
- New RED case 2: same `None` but with nested `_LIFECYCLE` and `_TEST_SECRETS` values: same missing rejection.
- New GREEN baseline control: valid **falsey** prior handler `SIG_DFL == 0` is installable/restorable; tests must not mistakenly reject all falsey values.
- Persistent original frozen accepted-runner `unexpected_manifest_secret` RED remains unrelated and untouched.

Tests use fake `getsignal`, `signal.signal`, guarded fake Docker command functions, and controlled `Path` temp directory. Neither tests nor candidate invoke a real OS signal, Docker, PostgreSQL, network, production data or subprocess.

## Minimal candidate-only correction

After `previous_term = signal.getsignal(signal.SIGTERM)`, but **before** installing a new callback or generating transient passwords:

- If `previous_term is None`: set `handler_restoration='UNCONFIRMED'`, `result='FAILED'`, `cleanup_passed=False`, `cleanup_status='CLEANUP_UNCONFIRMED'`, and `evidence_status='EVIDENCE_UNAVAILABLE'`.
- Raise `CleanupUnconfirmed('CLEANUP_UNCONFIRMED: SIGTERM prior handler unrepresentable')` without replacing the existing native handler. The outer `finally` still attempts bounded owned credential-file removal if applicable and resets both ContextVar tokens.
- An existing valid `SIG_DFL` representation (`0`) is not blocked, preserving the normal exactly-once restoration path.
- The review fork's unconditional `OFFLINE_REVIEW_ONLY` command denial and lack of CLI entrypoint are preserved. **The protected accepted runner is untouched.**

**Early-evidence limitation:** after `evidence.mkdir`, an early setup refusal can leave an empty evidence directory and no accepted manifest. The retained record is marked EVIDENCE_UNAVAILABLE; lack of a manifest is **never** PASS. No fsync/power-loss or native-signal control guarantees are claimed.

## After-correction tests and decision

[Post-fix CI run **44285545207**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44285545207), code commit `6c9949d751f6ec3ce8df18f1ea0744897d303b3a`: Python 3.11 **307 passed / 1 failed (308 collected)**, Python 3.12 **307 passed / 1 failed (308 collected)**. Both new formerly RED cases passed, and the `SIG_DFL == 0` guard passed. Prior R2-01 + H1/H2/M1/M2/T1/T2 candidate-only tests remain green.

The sole failing test is the **unchanged original frozen-runner redaction regression** `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]` (synthetic preflight `RuntimeError`, expected `CleanupUnconfirmed`). It has not been weakened, skipped or xfailed. Therefore the **overall CI remains FAILURE**, not a green release gate.

[Focused offline verification 44285545119](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44285545119) succeeded. [Read-only host prerequisites 44285545186](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44285545186) still failed; exact OCI/Docker29 local/no-pull environment, sixteen-wheel candidate-v2 provenance, `_HOST_HEALTH` and separately authorized disposable PostgreSQL 16.15 are unverified/blocking.

## Scope and next sequential stage

- **R2-02 is closed only as a scoped, synthetic candidate test gate**, not as human security signoff or protected-source acceptance.
- Codex's remaining **R2-03** (a rejected redacted finalizer snapshot while retained lifecycle record still shows PASS) and **R2-04** (ambiguous manifest publication followed by failed rollback) are unresolved and not waived.
- Next strictly sequential stage: R2-03 candidate-only RED test first, minimal correction, Python 3.11/3.12 CI and an evidence record. Then R2-04 separately. After all findings, request a **fresh Codex AI technical review** of exact source IDs, followed by owner's documented personal risk assessment and **separate explicit scoped protected-runner authorization**, if appropriate, under F04-G01.

**Final:** **R2-02 SYNTHETIC TEST GATE PASSED; F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**
