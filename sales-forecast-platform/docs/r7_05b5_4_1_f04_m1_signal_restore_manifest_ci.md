# R7-05B5-4-1 / F04-M1 — Restore SIGTERM before terminal success evidence

**Date:** 2026-11-18. **Scope:** Candidate-only, synthetic offline verification; F-04 **OPEN / NO-GO**.

## Frozen source and corrective candidate

- Accepted protected `tests/postgres/run_postgres.py` Git blob **`0863e81b733381a80b2f83560d8bbc231513ba41`**, unchanged.
- Review-only `tests/postgres/run_postgres_security_patch_candidate.py` Git blob **`5bc8dcc22ae9d09577e15a1519c3c473e03554d3`**. Candidate's unmocked `command()` still stops before `subprocess.Popen`; no operational code was executed.
- New mock-only tests `tests/runtime/test_r7_05b5_4_1_f04_m1_signal_manifest_gate.py` Git blob `0ccb1528d32366667b7f5782f84b602452a92be5`.

Codex-assisted MEDIUM finding M1: the prior candidate could publish `PASS / CLEANUP_SUCCESS` before trying to restore the previous SIGTERM handler. If that restoration raised, the run would exit with an exceptional outcome while already publishing accepted-looking success evidence.

The candidate now calls a guarded **one-time SIGTERM restoration helper** before serializing and publishing the terminal manifest. It records `handler_restoration=RESTORED` when the signal setter succeeds. On restoration error, it marks `handler_restoration=UNCONFIRMED`, `result=FAILED`, `cleanup_passed=False`, and `cleanup_status=CLEANUP_UNCONFIRMED`. If the body had otherwise succeeded, this leads to a `CleanupUnconfirmed` rejection **before a PASS manifest can be written**. When the body already has an exception, a restoration error does not mask the primary error; the candidate may publish a failed manifest, then propagate the original exception. Outer `finally` still handles early-setup exceptions, attempts the restoration only when not attempted earlier, and resets both ContextVars even if the signal handler could not be restored.

The helper catches `BaseException` from the *mocked* setter and records failure instead of retrying. This is an explicit limited error-precedence policy; repeated OS signal interruption, hard kills and unknown side effects of a real signal handler remain unverified. Handler restoration failure is not evidence of resource cleanup success.

## Three new offline regressions

The test file drives a **synthetic complete main-body success path** using fake `command()`, `inspect_image()`, `build_client()`, and `remove_owned()` implementations. All real file writes are under an isolated pytest `tmp_path`, with fake disposable passwords; no Docker, PG, network, or real subprocess runs. It checks:

1. **Mocked full-body success with SIGTERM restoration success:** old signal handler is restored before `write_evidence(run-manifest.json)`; one manifest is published with PASS, CLEANUP_SUCCESS, and RESTORED.
2. **Mocked full-body success with SIGTERM restoration failure:** signal setter attempted exactly once, candidate rejects via `CleanupUnconfirmed`, no success manifest exists, record is FAILED/CLEANUP_UNCONFIRMED, envfile is removed and ContextVars cleared.
3. **Simultaneous synthetic primary preflight failure and restoration failure:** original preflight exception survives; any manifest published is FAILED/CLEANUP_UNCONFIRMED with handler restoration UNCONFIRMED; ContextVars cleared.

## GitHub Actions evidence

Candidate-only CI [run 44245540104](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44245540104), head commit `63833aedcde98ea481f098e44170135601e25796`:
- Python **3.11: 265 passed, 1 failed (266 collected)**.
- Python **3.12: 265 passed, 1 failed (266 collected)**.
- Three new M1 test cases all pass.
- Sole remaining failure in both versions: the **unchanged accepted-runner** `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]`, observed `RuntimeError` instead of required `CleanupUnconfirmed`.
- Separate focused offline verification at the same head, [run 44245540199](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44245540199), succeeded.
- The **host prerequisite** check [run 44245540120](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44245540120) remains failed. None of these jobs authorize real PostgreSQL integration or supply-chain acceptance.

A number of original green tests are **counterexamples proving vulnerabilities**, not repaired-runner acceptance. A candidate PASS count does **not** close F-04.

## Remaining blockers / next milestone

Next sequential candidate-only issue **F04-M2**: if a body sets `record['result']='PASS'`, later reconciliation interrupted by `KeyboardInterrupt` must invalidate the lifecycle record regardless of a successful envfile unlink; add a fully mocked successful-main-body + interrupted reconciliation test. The independent human review, original red regression, strict malformed unlink result contract, partial evidence-write classification, OCI/16-wheel provenance, Docker29, `_HOST_HEALTH` and explicit disposable PG16.15 permissions are still unresolved. No protected-runner edit, real Docker/PG, controlled rebaseline, merge, deployment or B5-4-2 is authorized.

**F04-M1 mock test gate passed. F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**
