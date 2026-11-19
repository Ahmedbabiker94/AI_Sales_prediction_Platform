# R7-05B5-4-1 / F04-M2 — Interrupt-safe lifecycle result during resource reconciliation

**Date:** 2026-11-18. **Classification:** candidate-only mocked offline regressions; F-04 **OPEN / NO-GO**; B5-4-2 BLOCKED.

## Exact source identities

- Protected frozen accepted `tests/postgres/run_postgres.py` Git blob `0863e81b733381a80b2f83560d8bbc231513ba41`: **verified unchanged**.
- Review-only `tests/postgres/run_postgres_security_patch_candidate.py` Git blob `e80965a4988340b67a0ad50187f297f63cc5f962`.
- Regression test `tests/runtime/test_r7_05b5_4_1_f04_m2_reconciliation_interrupt.py` blob `3b4536c22f2ce46e2ec78e0b09a994e4761178aa`.
- Candidate-only workflow `.github/workflows/r7-b5-4-1-f04-candidate-offline.yml` blob `22690e236aa8955f00eef0dab488239a3e1f56d3`.
- Tested source head: `5f1032be19c9f9ed458a5d934d280e0574a76ba1`. No protected runner rebaseline, merge, deployment or real Docker/PG invocation.

## Confirmed flaw: pre-cleanup PASS retained on interruption

A fully mocked successful main body previously set `record['result'] = 'PASS'` **before** bounded container/volume reconciliation. An unhandled `KeyboardInterrupt` during `remove_owned('container', ...)`, `remove_owned('volume', ...)`, or ambiguous-create reconciliation `time.sleep` bypassed status correction. The outer owned envfile fallback removed `test-client.env`, yet the retained in-memory record incorrectly remained `PASS`. No accepted manifest was published on those interrupted paths.

**Test-first RED:** CI [run 44254669222](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44254669222), both Python 3.11 and 3.12: **265 passed / 4 failed (269 collected)**; three M2 failures specifically observed `'PASS' != 'FAILED'`; fourth failure was the unchanged original accepted-runner `unexpected_manifest_secret` regression. New M2 tests were explicitly added to the candidate CI test list; an earlier compilation-only run did NOT execute them and must not be counted as evidence.

## Candidate-only correction

The lifecycle record is created with `result=FAILED`, `cleanup_passed=False`, and `cleanup_status=CLEANUP_UNCONFIRMED`. Successful completion of the main body now sets only a **local** `body_passed=True` marker, never an observable terminal PASS. The cleanup `finally` keeps the record fail-closed until bounded owned-resource reconciliation, inventory queries, a single owned credential-file unlink, and the once-only restoration attempt of the previous SIGTERM handler complete. Only if body success, confirmed resource cleanup, an affirmative `REMOVED`/`NOT_PRESENT` credential status, and successful SIGTERM restoration all hold does the candidate promote `result=PASS` before manifest finalization.

An exceptional reconciliation exit—including `KeyboardInterrupt`—thus retains FAILED/CLEANUP_UNCONFIRMED even if fallback credential unlink returns REMOVED. No duplicated unlink or broad resource deletion is introduced. Existing candidate `OFFLINE_REVIEW_ONLY` unconditional command hard stop and lack of CLI entrypoint remain in place.

## After-fix offline tests

Three parametrized M2 tests run a synthetic full-body success with mocked command/image/client/owned-resource/signal interactions. Interrupts occur (1) during container cleanup, (2) during volume cleanup, and (3) at the ambiguous-mutation reconciliation sleep. Each checks:
- in-memory `FAILED` state **at the interruption surface and afterwards**, `cleanup_passed=False`, and `CLEANUP_UNCONFIRMED`;
- propagation of the injected original `KeyboardInterrupt`, no success manifest, and only one successful unlink of a test-created synthetic envfile;
- previous mocked handler restored exactly once and both ContextVars reset;
- all Docker-like argv intercepted by fakes, with no real process, Docker, PostgreSQL, network, or OS signal.

**Final CI:** [run 44255068587](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44255068587), Python **3.11: 268 passed, 1 failed (269 collected)**; Python **3.12: 268 passed, 1 failed (269 collected)**. All M2 (three), M1 (three), H2 (six) and H1 (five) synthetic regression tests passed. Only unchanged frozen-runner `unexpected_manifest_secret` remains RED (`RuntimeError` instead of expected `CleanupUnconfirmed`). A separate focused offline verification [run 44255068672](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44255068672) succeeded, while host prerequisite [run 44255068494](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44255068494) failed/unverified.

## Boundaries / remaining blockers

Mocked ordering is not proof against SIGKILL, arbitrary repeated asynchronous interrupts, non-cooperative process termination, actual daemon races, malicious filesystem races, partial evidence writes, or real host signal semantics. These results are not authorization for Docker/PG16.15, OCI or wheel changes, a frozen-runner edit, human review signoff, merge or deployment. Independent qualified human security review and separate owner authorization remain outstanding. Additional candidate review gates include strict unlink result typing and evidence-write failure classification; original protected redaction regression remains RED.

**Decision: F04-M2 synthetic candidate gate passed. F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**
