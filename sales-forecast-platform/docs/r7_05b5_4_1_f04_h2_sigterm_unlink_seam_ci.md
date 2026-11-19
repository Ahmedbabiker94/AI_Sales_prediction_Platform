# R7-05B5-4-1 / F04-H2 — candidate-only SIGTERM deferral at owned credential unlink seams

**Date: 2026-11-18. Status: H2 SYNTHETIC TEST GATE PASSED; F-04 OPEN / NO-GO.**

## Exact sources

- Frozen accepted `tests/postgres/run_postgres.py` blob **`0863e81b733381a80b2f83560d8bbc231513ba41`**: **UNCHANGED**.
- Review-only `tests/postgres/run_postgres_security_patch_candidate.py` blob **`7d3673a4c5b08fe305e86b07a6212652d48f90de`**: changed only candidate source. The unconditional `OFFLINE_REVIEW_ONLY` hard stop remains the first statement in `command()`, and no CLI entrypoint was added.
- Test `tests/runtime/test_r7_05b5_4_1_f04_h2_sigterm_unlink_seam.py` blob `c50284adc08c0390436ab3358128e6f0135dcc09`.

## Finding and constrained fix

Codex's HIGH F04-C/D finding showed that the old `credential_cleanup_attempted=True` assignment was vulnerable to an installed SIGTERM handler raising `KeyboardInterrupt` immediately afterwards but before the owned `remove_transient_credential_file(envfile)` call. The outer fallback saw the `True` flag and could skip the actual attempt.

The **candidate only** now maintains a per-run mutable `deferred_term = {'active': False, 'pending': False}`. Its installed SIGTERM handler queues a pending interrupt while `active`, returning without raising. Both normal and fallback owned envfile cleanup set `active=True` **before** setting the one-attempt flag, call the pre-existing symlink-refusing unlink helper once, record `REMOVED`/`NOT_PRESENT` or reject `UNCONFIRMED`, then mark the record `FAILED / CLEANUP_UNCONFIRMED` if an interrupt was queued. They disable deferral and propagate the deferred `KeyboardInterrupt` only **after** the attempt and status evaluation. Existing nested finally restores handler and both ContextVars. This preserves the no-duplicate-unlink policy.

**This is cooperative deferral by the candidate's Python-installed SIGTERM callback, not OS-level atomic deletion or secure erase.** SIGKILL, process termination, arbitrary `KeyboardInterrupt` not from the installed handler, a second signal prior to entering the fallback guarded section, and filesystem races remain outside the proven guarantees. A true signal delivery order or OS-level race test was not run.

## Actual offline tests

Six cases introduced, all run without OS signals or external process execution:
- Parametric normal cleanup and interrupted-reconciliation fallback, each tested with **one** or **two** synthetic SIGTERM callback invocations injected on the `run()` source line **after** `credential_cleanup_attempted=True` and immediately **before** the call to the owned unlink helper. The Python tracing hook is temporary and restored after each test.
- Normal and fallback paths with SIGTERM callback invoked **inside** a wrapped local unlink helper.

Each case uses an actual fake-credential marker inside a newly created pytest temporary directory, not real passwords. Tests assert only one envfile removal, file absence, failure state, no accepted manifest, returned original handler, and no leaked `_TEST_SECRETS` or `_LIFECYCLE`. All Docker commands are mocked or blocked by the unchanged candidate command hard stop.

## CI results

**GitHub Actions run 44244453322**, source head `27a851638b9d09539a60b8e611de9717815ed24e`:
- **Python 3.11: 262 passed, 1 failed, 263 collected.**
- **Python 3.12: 262 passed, 1 failed, 263 collected.**
- The only failed test remains the original frozen accepted-runner `unexpected_manifest_secret` red regression, yielding synthetic preflight `RuntimeError` rather than `CleanupUnconfirmed`. It was not changed, skipped or xfailed.
- Some green diagnostic tests still document accepted-runner vulnerabilities. Therefore 262 PASS results are **not** independent acceptance.

## Remaining gates

The independent reviewer must inspect the deferral design and error precedence. The Codex MEDIUM findings remain: **M1 stale PASS manifest if SIGTERM handler restoration fails after publication; M2 resource-reconciliation interruption can leave retained record PASS even if envfile removal succeeds**. Further requirements also include strict string-type validation for unlink status, partial evidence write classification, original `unexpected_manifest_secret` RED, OCI/Docker29 no-pull checks, verified 16-wheel provenance, `_HOST_HEALTH`, and separately authorized disposable PG16.15 integration. Human independent approval and separate owner authorization for a protected-runner security-only rebaseline remain absent.

**F-04 OPEN / NO-GO. B5-4-2 BLOCKED. No protected edits, real Docker/PG, merge or deployment.**
