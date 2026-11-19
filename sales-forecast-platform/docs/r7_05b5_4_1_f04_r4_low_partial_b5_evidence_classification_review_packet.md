# Phase 7 / R7-05B5-4-1 / F-04 — R4-LOW partial reporting evidence status correction

**Date:** 2026-11-18.
**Authority:** Owner authorized R2-04-R4 **candidate-only**, offline integration verification; the owner's permission did **not** authorize protected-source edits, a rebaseline, Docker/PG execution, merge, deployment or F-04 closure.
**Reviewer trigger:** The owner supplied a Codex AI R4 verdict `TECHNICALLY ACCEPTABLE FOR OWNER REVIEW` under a trusted synchronous single-process scope, with one **LOW** remaining evidence-classification mismatch: a partial `b5-4-1.stdout` or `b5-4-1.stderr` write could publish `FAILED / AVAILABLE`. The reviewer stated this does not bypass admission but should be fixed or documented before operational transfer.
**Decision:** **R4-LOW candidate-only targeted test gate PASSED**. A fresh Codex read-only review of this new source identity is still needed. **F-04 OPEN / NO-GO; B5-4-2 BLOCKED**.

## Pinned identities (repo / branch / code-tested commit)

Repository `Ahmedbabiker94/AI_Sales_prediction_Platform`; branch `fix/r7-05b5-4-1-bootstrap`; latest code-tested commit **`22b70f047b9260c83bc2ca4b2d2db8769a754f67`**.

| Artifact | Verified Git blob |
|---|---|
| Accepted frozen runner `tests/postgres/run_postgres.py` | `0863e81b733381a80b2f83560d8bbc231513ba41` — **UNCHANGED** |
| Operational B5 wrapper `tests/postgres/run_b5_4_1_postgres.py` | `040d0d30bb719d646eb350b229ccd9a4c5f2bf79` — **UNCHANGED** |
| Security runner candidate `tests/postgres/run_postgres_security_patch_candidate.py` | `ccd1efac292d9504a59eeef3eaf8b21889879b59` — unchanged `OFFLINE_REVIEW_ONLY` |
| Trusted synchronous controller `tests/postgres/run_manifest_admission_controller_candidate.py` | `f8ffa51dbf072deaad0ab7e3701965a05b471548` — unchanged |
| **R4 corrected reporting stage** `tests/postgres/run_b5_4_1_reporting_stage_candidate.py` | `5a82cb0219948c0b895f92b603252a547957bed7` |
| **R4 expanded tests** `tests/runtime/test_r7_05b5_4_1_f04_r2_04_r4_actual_reporting_stage.py` | `3c88abe69ccd19399506800a0fa69c98e3b2bc25` |
| Offline CI workflow `.github/workflows/r7-b5-4-1-f04-candidate-offline.yml` | `daa477ceba84bd7a6a85dcb4d40fa33fa0452aea` — unchanged |

All code/test paths excluding workflow relative to `sales-forecast-platform/`.

## Test-first RED reproduced

The existing partial-output test was explicitly parametrized for both `stdout` and `stderr`, and strengthened to assert:

- Original `OSError` is preserved, not masked by a generic cleanup error.
- The affected synthetic stage output is genuinely partially written.
- Original retained lifecycle record and published terminal manifest both say `result='FAILED'` **and** `evidence_status='EVIDENCE_UNAVAILABLE'`.
- The extra reporting container is included in reconciliation/owned removals; the transient credential file is removed; prior SIGTERM handler is restored; ContextVar tokens are reset.
- No trusted manifest admission occurs; archive admission remains default-deny.

[Pre-fix candidate offline CI **44362009764**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44362009764), test-only commit `32a96dffef149eb6e5a8a73e08a33cd985f9e9d9`: Python **3.11 and 3.12 each 350 passed / 3 failed (353 collected)**. Two new R4-LOW failures directly showed retained `'AVAILABLE'` vs required `'EVIDENCE_UNAVAILABLE'`; the third was the existing unchanged protected-runner redaction RED. This is *offline fault injection* only, not a real Docker/PG reproduction.

## Narrow stage-only security delta

The candidate reporting stage now wraps **only** the two existing calls:

```python
try:
    write_evidence(evidence_dir/'b5-4-1.stdout', result.stdout)
    write_evidence(evidence_dir/'b5-4-1.stderr', result.stderr)
except BaseException:
    record['evidence_status'] = 'EVIDENCE_UNAVAILABLE'
    record['result'] = 'FAILED'
    raise
```

The bare `raise` preserves the original exception; runner-owned reconciliation, transient credential cleanup, handler restoration and terminal failed-manifest publication remain intact. The `EVIDENCE_UNAVAILABLE` indicator is retained through finalization and copied into the original lifecycle record by R2-03. This is **deliberately conservative**: a failed write does not prove a complete trustworthy stage evidence set.

**Review parity accounting:** The original frozen operational `reporting_stage` function is unchanged. The candidate-stage AST parity test now permits **exactly two reviewed deltas**, and no others:
1. Redirect local `_LIFECYCLE` import from protected module to the candidate module, ensuring same runtime.
2. One `try/except BaseException` around both stdout/stderr writes, with *exact* assignments to `record['evidence_status']` and `record['result']`, and bare re-raise. The test flattens this node back into the two original write statements and independently compares the entire remaining AST with operational source.

No other test selectors, execution isolation, resource registration, Docker argv, mounts, output redaction, process invocation or other operational source behavior was changed.

## Additional synthetic secret regression

Added one B5 stage control-flow test where the same fake reporting-stage command returns **test-only disposable credential strings** in **both** stdout and stderr. It asserts:
- On-disk `b5-4-1.stdout` and `b5-4-1.stderr` contain `<redacted-disposable-secret>` and never either test credential.
- Captured console stdout/stderr and terminal manifest contain no generated credentials.
- Redaction is nonzero, the finalizer rejects the run as `FAILED / CLEANUP_UNCONFIRMED`, and the Admission Controller issues **no** accepted snapshot.
- The named reporting container reaches owned reconciliation, prior SIGTERM handler restoration and ContextVar reset.

No real password, subprocess, external command, signal, Docker or PostgreSQL was used.

## Final CI and residual blocking conditions

[**Final offline candidate CI 44362940928**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44362940928) on code-tested commit `22b70f047b9260c83bc2ca4b2d2db8769a754f67`: **Python 3.11 and Python 3.12 each 353 passed / 1 failed (354 collected)**. Both prior R4-LOW RED cases, new synthetic test-secret case, updated strict AST source-parity and all prior candidate tests are **GREEN**. The sole remaining failure is the *unchanged protected accepted-runner* `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]`. It has **not** been skipped, weakened, xfailed, or patched; entire CI remains **FAILURE**, not a green release gate.

[Focused offline run **44362940882**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44362940882) succeeded. [Read-only host prerequisite **44362940969**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44362940969) failed.

**This candidate correction needs a fresh AI technical assessment of its updated stage and test identities.** The previous Codex verdict `TECHNICALLY ACCEPTABLE FOR OWNER REVIEW` was issued against the **prior** reporting-stage blob, not this corrected version, and is not automatically extended to it.

Still **BLOCKED** for operational transfer:
- The actual operational wrapper and frozen runner are unchanged, not connected to the candidate controller; a later owner-authorized *runner + controller + wrapper* rebaseline would be necessary.
- Original frozen-runner redaction test RED must turn GREEN **without weakening the test** in a separately authorized source change.
- Remaining R4 negative tests to consider before any protected-source authorization: reporting command timeout with daemon-mutation uncertainty, late resource appearance or inventory failure; check exact shared runtime imports after migration; block file-only operational entrypoints; explicitly reject concurrency if unsupported.
- Docker29 and pinned OCI no-pull image availability, all sixteen wheel candidate-v2 provenance/hash/license/OSV checks, host prerequisites, `_HOST_HEALTH` and a separately authorized *disposable PostgreSQL16.15* integration run remain unverified.

**Status: R4-LOW TARGETED SYNTHETIC GATE PASSED; NEW SOURCE AWAITS CODEX RE-REVIEW; OWNER PROTECTED REBASELINE NOT AUTHORIZED; F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**
