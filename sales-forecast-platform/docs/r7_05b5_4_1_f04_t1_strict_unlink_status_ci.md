# R7-05B5-4-1 / F04-T1 — strict exact-string credential-unlink status

**Date:** 2026-11-18. **Scope:** offline, review-only candidate. **F-04 OPEN / NO-GO; B5-4-2 BLOCKED.** This is not a human independent security approval or a protected-runner rebaseline.

## Pinned identities

- Frozen accepted `tests/postgres/run_postgres.py` Git blob `0863e81b733381a80b2f83560d8bbc231513ba41`: **UNCHANGED**.
- Review-only `tests/postgres/run_postgres_security_patch_candidate.py` Git blob `c2e963222328baa40677b8b7e74d180e30491361`: **candidate only**, unconditional `OFFLINE_REVIEW_ONLY` command denial and no CLI entrypoint retained.
- `tests/runtime/test_r7_05b5_4_1_f04_t1_strict_unlink_result_type.py` Git blob `172fe3cfa2f77975ad5a816a06ef76f80b85ac0e` (18 cases: 10 normal-success-path and eight interrupted-reconciliation fallback).
- Candidate-only workflow `.github/workflows/r7-b5-4-1-f04-candidate-offline.yml` blob `c331ef6e21e6b71c247a2f6ed30e977a613b109b`, explicitly includes all new T1 cases.
- Final tested source commit `a36158b1ebac7204d04e95fb5efdea895c40ae11`.

## The contract and test-first RED

Previously both normal and fallback credential-file cleanup used `status in ('REMOVED','NOT_PRESENT')` on an untrusted return object. This implicitly invokes user-defined equality, and accepts an exact-looking `str` subclass. Forged `__eq__` methods can claim success, or raise to mask a primary `KeyboardInterrupt`; an arbitrary object stored in `record` can also cause JSON serialization failure rather than a classified cleanup rejection. Validity of removal cannot be inferred from Python duck-typed comparison.

**Before fix:** [candidate CI 44258852023](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44258852023), **both Python 3.11 and 3.12: 278 passed / 9 failed (287 collected)**. Eight failed cases are the new T1 negative tests: forged REMOVED / NOT_PRESENT and raising comparison in normal and fallback paths, plus `str` subclass in both. The ninth is the **unchanged original** frozen-runner `unexpected_manifest_secret` RED. The tests intentionally showed non-string status objects being accepted, comparison exceptions escaping, and fake statuses reaching manifest serialization. Ten other new T1 cases were already green, including ordinary strings, unknown strings and basic rejected non-strings.

## Narrow candidate-only correction

A pure `normalize_transient_credential_cleanup_status(status)` helper checks **`type(status) is str` before any membership or equality test**. Only literal built-in strings `'REMOVED'` and `'NOT_PRESENT'` remain affirmative. Every other type/value is canonicalized to the built-in literal `'UNCONFIRMED'` without invoking object `__eq__` or storing a non-string in the lifecycle record. Both normal and interrupted-reconciliation fallback invoke the same helper; terminal PASS promotion also requires exact `str` cleanup state. Successful owned envfile removal does not override an untrusted status or uncertain daemon/resource reconciliation.

## Tests and observable outcomes

All T1 cases intercept Docker-like commands via mock functions or block them with the candidate hard stop, mock signal installation/restoration, and (in full-body tests) use an actual locally created `test-client.env` with synthetic bytes, removed exactly once using the existing symlink-refusing helper. Test variants: equality-spoofed REMOVED, equality-spoofed NOT_PRESENT, equality-throwing objects, `str` subclass, bytes, int, None, unknown string, plus literal valid REMOVED and NOT_PRESENT controls. The fallback variant injects a synthetic `KeyboardInterrupt` during bounded reconciliation. Tests confirm original interruption precedence, canonical FAILED/CLEANUP_UNCONFIRMED, zero invocations of adversarial equality methods, no accepted PASS for malformed results, one owned unlink, previous mock handler restoration, and ContextVar cleanup.

**After fix:** [candidate CI 44259001678](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44259001678), **Python 3.11 and 3.12: 286 passed / 1 failed (287 collected)**. All **18 new T1** and previously added H1/H2/M1/M2 offline regressions passed. Sole RED remains the unchanged original `unexpected_manifest_secret` scenario, which expects `CleanupUnconfirmed` but raises `RuntimeError`. The overall matrix is **not green**. [Focused offline verification 44259001641](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44259001641) succeeded; [host prerequisites 44259001648](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44259001648) failed/unverified.

## Explicit limitations / gates

Mock tests do not prove real OS SIGTERM timing, secure erase, malicious-host filesystem race safety, Docker reconciliation, Postgres 16.15 behavior or production deployment. There was **no actual Docker, PG, subprocess, OS signal, network, or protected-runner invocation** as part of T1. Independent qualified human security review and separate owner authorization for any controlled frozen-runner edit are still absent. The next distinct F-04 candidate review is **partial evidence/manifest-write failures and EVIDENCE_UNAVAILABLE classification**, keeping the original redaction RED, OCI/Docker29 no-pull checks, complete 16-wheel provenance, `_HOST_HEALTH`, and separately approved disposable PG16.15 integration blocked.

**F04-T1 synthetic candidate tests PASS. F-04 OPEN / NO-GO. B5-4-2 BLOCKED.**
