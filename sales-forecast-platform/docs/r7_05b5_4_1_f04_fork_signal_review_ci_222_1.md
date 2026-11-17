# R7-05B5-4-1 F-04 — Candidate fork critical review follow-up

**2026-11-17; F-04 OPEN / NO-GO.** This is an internal adversarial source/mock review, **not** independent security signoff or an authorized rebaseline.

## Current evidence

At source commit `78ed552f357799fe3360f9b5368ef096faaab834`, GitHub Actions F-04 offline run **44131361732** collected **223 cases per CPython matrix**:
- Python 3.11: **222 passed / 1 failed**.
- Python 3.12: **222 passed / 1 failed**.
- The lone failure is unchanged accepted-runner `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]`: original synthetic preflight RuntimeError instead of expected CleanupUnconfirmed.
- Four new case executions in `tests/runtime/test_r7_05b5_4_1_f04_fork_signal_failure_review.py` **passed** on the isolated fork. Cases: `getsignal` error, failed/partially attempted SIGTERM install followed by restore attempt, SIGTERM restore failure after secrets were bound, and nested ContextVar value preservation.
- The four passing cases cover **mocked Python behavior**, not real OS signal races, Docker/PG, or all `BaseException` interleavings.

## Protected source integrity

The accepted `tests/postgres/run_postgres.py` retains Git blob `0863e81b733381a80b2f83560d8bbc231513ba41`. It has **not** been replaced or modified. The changed file is only a separate review fork `run_postgres_security_patch_candidate.py`, plus candidate test and CI files.

## Further review risks / stop conditions

1. The proposed fork **still defines a callable real `run()`** with Docker commands inherited from the original runner, despite removal of its CLI entrypoint. **Never call the fork outside fully mocked tests**; the absence of `__main__` is not an execution sandbox. Do not import its `run` into operational code.
2. Context restoration on a mocked signal handler error is not proof against SIGTERM/native signal races or sudden host exit. Actual process-level interrupt and credential-file unlink behavior need separate accepted safety tests.
3. Compare explicit secret-redaction accounting to the original `redact_test_secrets` side-effect semantics: ensure prior stdout/stderr counts and unexpected manifest values are reconciled without double count. The separate helper returns the correct fail-stop decision for its tested inputs, but does **not** integrate trusted manifest publication.
4. Confirm cleanup status/provenance if serialization or evidence write fails; no manifest is not an accepted PASS. No opportunistic delete/overwrite if evidence publication fails. Audit `BaseException` around cleanup ledger and original signal restoration.
5. The 222 green cases include *diagnostic tests demonstrating original vulnerabilities*; they are not a certified 222-case security clearance.
6. OCI indexed refs, 16 candidate-v2 wheel artifacts/hashes/licensing, original intermittent `_HOST_HEALTH`, full trusted-host review and separately authorized disposable PostgreSQL 16.15 are outstanding.

## Next gated step

Get independent review of the isolated security-only candidate diff, then **explicit approval** for any edit to `run_postgres.py`. Such approval **does not** grant Docker/PG execution, IOC/base rebaseline, merge or deployment. Once approved, transition the currently vulnerability-confirming diagnostic tests into negative no-leak acceptance assertions and rerun both Python matrices.

**No-Go for F-04 closure and B5-4-2.**
