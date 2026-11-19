# Phase 7 / R7-05B5-4-1 / F-04 — R2-01 (High): secondary-reaping exception sanitization

**Date:** 2026-11-18. **Scope:** Candidate-only, offline/synthetic security regression.
**Governance:** Owner explicitly accepted the Codex `CHANGES REQUIRED` verdict and authorized **candidate-only sequential remediation**; this is **not** owner authorization for a protected-runner rebaseline or real Docker/PostgreSQL.
**Status:** **R2-01 targeted synthetic tests PASSED; F-04 OPEN / NO-GO; B5-4-2 BLOCKED**.

## Pin exact source and CI evidence

| Item | Git identity |
|---|---|
| Frozen accepted runner `tests/postgres/run_postgres.py` | `0863e81b733381a80b2f83560d8bbc231513ba41` — verified UNCHANGED |
| Review-only candidate `tests/postgres/run_postgres_security_patch_candidate.py` | `e4634526291cc713586ab07125de49cc032226f5` |
| New R2-01 negative tests `tests/runtime/test_r7_05b5_4_1_f04_r2_01_secondary_reap_sanitization.py` | `c4095004e96e521a4bfc8679de5bfa82f79380a6` |
| Candidate-only CI workflow `.github/workflows/r7-b5-4-1-f04-candidate-offline.yml` | `a8381c8673041a97c20727ff85ffbace256c3938` |
| **Final tested source commit** | `fa308bd1e8d1335a0296b1c3850b906d6a26e67f` |
| AI reviewer original R2 input | Codex `CHANGES REQUIRED` report received via owner; inspected baseline candidate blob `dafd1822d58700663eaef378824c5c0e87d99428` |

All repository paths below are relative to `sales-forecast-platform/`. The new test is *explicitly* in the hand-maintained candidate CI pytest list (not merely compiled by file glob).

## Problem and test-first reproduction

The prior candidate handles `subprocess.TimeoutExpired` from a potentially password-bearing `docker exec -e PGPASSWORD=...` argv inside a broad exception handler. Before replacing it with a safe, public `RuntimeError`, it called `process.poll()`, `os.killpg()`, and a second `process.communicate(timeout=3)`. If one of these secondary operations throws an unexpected error (`OSError`, `UnicodeDecodeError`, or control-flow `KeyboardInterrupt`/`SystemExit`), the secondary exception can escape *inside the raw timeout's active exception scope*. The Python implicit `__context__` can then expose original `TimeoutExpired.cmd`, `stdout` or `stderr` with synthetic test credentials.

**Before correction:** [offline candidate CI 44281482147](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44281482147) on both Python 3.11 and 3.12: **296 passed, 9 failed, 305 collected**. Eight new R2-01 failures were deliberate negative reproductions; the ninth was the unchanged protected-runner `unexpected_manifest_secret` RED.

The new regression **copies the candidate's `command()` AST in memory and removes only its unconditional offline guard from that copy**; fake `Popen`, `poll`, `killpg`, `communicate`, `time` and `signal` then exercise the otherwise unreachable command implementation. The on-disk review fork's guard is not removed. No actual subprocess, OS signal, Docker, PostgreSQL, network access or real credentials were used.

Eight scenarios: poll OSError, poll KeyboardInterrupt, killpg OSError, killpg KeyboardInterrupt, reap UnicodeDecodeError, reap OSError, reap KeyboardInterrupt, reap SystemExit. Every case injects only a synthetic password and a raw `TimeoutExpired` whose public attributes would contain it if exposed. The tests assert exception class, constant sanitized public message, `__context__ is None`, `__cause__ is None`, absence of raw public `cmd/output/stderr`, no secret in `traceback.format_exception`, conservative daemon mutation state, safe issue classification, bounded synthetic second `communicate`, and ContextVar reset.

## Narrow candidate-only correction

- Contains `BaseException` separately around poll, killpg, and the second bounded communicate; only *fixed, non-sensitive stage codes* (e.g. `POLL_EXCEPTION`, `KILLPG_EXCEPTION`, `WAIT_EXCEPTION`, `WAIT_TIMEOUT`) are recorded, never raw exception instances, command argv or their messages.
- If polling itself cannot reliably establish that the process is still running, **does not risk signaling a potentially reused PID/process group**. Attempts one bounded local `communicate(timeout=3)`, with unknown/reconciliation outcome left unconfirmed if needed.
- Marks `local_reaped` only on positively confirmed `poll()` or successful bounded `communicate()`; a failed second wait cannot be counted as a successful local reap merely because `returncode` looks populated.
- Maintains `DAEMON_UNRESOLVED` when a daemon mutation command was interrupted; reaping the CLI never proves the Docker mutation was canceled.
- For raw `TimeoutExpired`, builds a new safe public exception and **raises it after leaving the raw exception-handler scope**. An interrupted secondary cleanup preserves `KeyboardInterrupt` or `SystemExit` class via a newly built safe exception with no sensitive original context. The chosen policy may delay delivery of an interrupt until the bounded local cleanup attempt completes; this does not guarantee atomic OS behavior.
- Preserves the candidate's first-statement `OFFLINE_REVIEW_ONLY` hard stop, lack of CLI entrypoint, original fixed PG selectors/OCI parameters, and original frozen runner.

## After-correction CI and interpretation

[Candidate CI 44281670745](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44281670745) on Python 3.11 and 3.12 **each 304 passed / 1 failed (305 collected)**. All eight new R2-01 cases and the prior H1/H2/M1/M2/T1/T2 synthetic cases passed. **Only the original frozen-runner `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]` is still RED** and was not skipped, xfailed, or weakened. Overall CI conclusion remains `failure` by design. [Focused offline verification 44281670258](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44281670258) completed successfully. [Read-only host prerequisite 44281670253](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44281670253) failed; it is not proof of Docker/PG readiness.

## Open security and governance gates

Codex's other unresolved findings **R2-02 (unrepresentable prior SIGTERM), R2-03 (retained PASS vs redacted rejected manifest), and R2-04 (ambiguous manifest publication and rollback failure)** are **NOT fixed or accepted** by this step. They require separate candidate-only test-first remediation in that order, followed by fresh Codex AI re-review of the exact updated source identity and the owner's personal source/risk assessment under [F04-G01](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/fix/r7-05b5-4-1-bootstrap/sales-forecast-platform/docs/r7_05b5_4_1_f04_g01_owner_self_review_policy.md). This is not independent human signoff; it does not automatically authorize rebaseline.

Additional untested risks: secret-bearing non-timeout exception chains, partial output encoding at truncation boundary, third-party log collectors or traceback-locals capture, actual OS interrupt timing, unreaped process behavior, malicious host, crash durability, and Docker daemon quiescence. No real Docker/PG test, image pull, package installation, protected runner edit, merge, deployment or B5-4-2 advancement occurred. The original redaction RED, Docker29/OCI no-pull and 16-wheel supply-chain provenance, `_HOST_HEALTH`, and separately authorized disposable PostgreSQL 16.15 remain blocked.

**Decision:** R2-01 **TARGETED SYNTHETIC TEST GATE PASSED**. F-04 **OPEN / NO-GO**. B5-4-2 **BLOCKED**.
