# R7-05B5-4-1 / F-04 — Interrupted reconciliation bypasses credential cleanup

**Date:** 2026-11-17. **State: REVIEW CANDIDATE BLOCKED / F-04 OPEN / NO-GO.**

## New source-level finding

The candidate `tests/postgres/run_postgres_security_patch_candidate.py` protects `_LIFECYCLE` and `_TEST_SECRETS` token restoration with an outer `finally`, and suspends ownership context before inventory queries. But **those improvements do not guarantee that the transient credential file is removed on a BaseException thrown inside reconciliation**.

Specifically the candidate's inner cleanup loop only catches `Exception` around `remove_owned`, and directly calls `time.sleep(.25)` when a Docker mutation is unresolved. A `KeyboardInterrupt` during this sleep exits the cleanup `try`; its nearest `finally` only removes `record['cleanup_deadline']`. It bypasses the later `remove_transient_credential_file(envfile)`, the two inventory listings and `write_evidence(run-manifest.json)`. The new outer `finally` still restores ContextVars, but **does not by itself remove the temporary file**.

This is a **mock-confirmed control-flow/cleanup-bypass gap**, NOT proof that a real credential file persisted in any live PostgreSQL run. If the temporary file already existed when such an interruption happened, it could remain until external cleanup. The original accepted runner also contains the same late cleanup structure, but it has not been changed or tested with a real database here.

## Exact offline evidence

New diagnostic `tests/runtime/test_r7_05b5_4_1_f04_cleanup_interrupt_counterexample.py` injects an unresolved owned-container creation attempt into the review-only fork, stubs `remove_owned` and the signal handler, and causes `time.sleep` to raise a synthetic `KeyboardInterrupt`. The test **passes by confirming a vulnerability**: the credential file removal callback was **never called**, no final manifest was published, and both ContextVars were restored by the fork's outer guard. All Docker subprocess entrypoints remain hard stopped.

GitHub Actions **run 44139900235**, commit `69edd3bedfe7c33d1a6aba6900d820e0ec90abb5`:
- CPython **3.11: 237 passed, 1 failed (238 collected)**.
- CPython **3.12: 237 passed, 1 failed (238 collected)**.
- The only red test is unchanged accepted runner `unexpected_manifest_secret`.
- Some green diagnostics intentionally *demonstrate defects*, including this newly added one. Therefore the green count is **not** security acceptance.

## Required owner/reviewer decision before protected rebaseline

The security-only patch candidate remains incomplete until it addresses cleanup interruption. A revised exception-safe finalizer should:
1. Attempt deletion of **only the known `envfile` generated within the new evidence directory**, regardless of whether reconciliation, manifest serialization or evidence writing raises. Preserve no-symlink/unlink safeguards and avoid broad filesystem or Docker deletion. Distinguish `NOT_PRESENT`, `REMOVED`, `UNCONFIRMED`; never record cleanup success when deletion is uncertain.
2. Ensure the credential file cleanup attempt cannot be silently skipped by an interrupted or failed reconciliation, while guaranteeing both ContextVars and the prior signal handler still restore; review repeated `BaseException` and second-signal interruption limitations.
3. Preserve the original primary failure, add bounded cleanup-error reporting and never replace an uncertain daemon mutation with PASS. No manifest if serialization/publication fails must be treated as evidence unavailable and unaccepted.
4. Add **negative repaired-runner acceptance tests** asserting credential deletion was attempted on reconciliation interruption and source ContextVars were cleared. Do not retain the vulnerability-affirming diagnostic as a green certification test.
5. Independently review interaction with `_LIFECYCLE` ownership scope, deadlines, SIGTERM restoration and no foreign resource deletion.

**No correction has been applied to `run_postgres.py`.** Frozen accepted Git blob verified: `0863e81b733381a80b2f83560d8bbc231513ba41` (expected `0863e81b733381a80b2f83560d8bbc231513ba41`).

**Authorization remains separate:** candidate review does not approve protected runner modification, OCI/wheel rebaseline, Docker/PostgreSQL 16.15 integration, merge, deployment or B5-4-2.

**F-04 OPEN / NO-GO.**
