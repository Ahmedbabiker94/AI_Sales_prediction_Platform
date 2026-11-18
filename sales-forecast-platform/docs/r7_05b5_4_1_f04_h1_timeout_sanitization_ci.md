# R7-05B5-4-1 / F-04-H1 — Candidate subprocess timeout disclosure remediation

**2026-11-18 — Candidate-only, source + synthetic subprocess review. F-04 remains OPEN / NO-GO.**

## Source identity and exact change

- Frozen accepted `tests/postgres/run_postgres.py` Git blob `0863e81b733381a80b2f83560d8bbc231513ba41` **unchanged**.
- Candidate `tests/postgres/run_postgres_security_patch_candidate.py` Git blob `e4e795c6522854a7afc2bc3f507e77f1d77dd1a6`.
- Review-only candidate's public `command()` still starts with `raise RuntimeError('OFFLINE_REVIEW_ONLY: ...')`, so it **cannot launch unmocked Docker or subprocess commands**. The retained source implementation below that hard stop is reviewed and tested through a copy of its AST, with the first guard removed **only in memory** and all subprocess/kernel actions replaced by injected fakes.

**Finding:** `subprocess.TimeoutExpired` carries the raw `cmd` (including disposable `PGPASSWORD` arguments), `output` and `stderr`. The accepted helper previously bare-reraised it after trying to kill and reap the local CLI. This could reveal synthetic disposable secrets via traceback independently of manifest redaction.

**Candidate correction:** When the original process or fake Popen raises `subprocess.TimeoutExpired`, the candidate maintains existing mutation state updates (mark `DAEMON_UNRESOLVED`) and bounded kill/reap attempt. A second `TimeoutExpired` during reap does not supersede the original timeout. A new public `RuntimeError` with a constant credential-free message is raised **after** leaving the unsafe `except` block, preventing an attached raw `__context__`, `cmd`, `output` or `stderr` from escaping in the tested path. Non-timeout exceptions follow the original path. The original `command()` implementation and frozen runner remain unmodified.

## Negative regression test results

New `tests/runtime/test_r7_05b5_4_1_f04_h1_timeout_sanitization.py` contains five synthetic tests:

1. Timeout from first process `communicate()` with mock argv, output and stderr containing fake secret.
2. An additional `TimeoutExpired` from the bounded reap attempt.
3. Timeout from fake `Popen` startup before process object creation.
4. Timeout after a synthetic owned Docker mutation has been registered, proving `DAEMON_UNRESOLVED` and `local_reaped=True` when fake kill/reap succeeds.
5. Successful fake process preserves `CompletedProcess` result, while the actual review fork `command()` still refuses execution before subprocess.

The tests inspect formatted traceback, public error text, `repr`, `args`, `vars`, `__context__`, `__cause__` and unsafe attribute absence. All Popen, process polling, killpg, timestamps and process communications are explicitly mocked; **no real process, Docker, PostgreSQL, network or credential is created or invoked**. This test does **not** inspect arbitrary debugger/traceback frame locals and cannot guarantee secret-free logging by unrelated wrappers.

## Verified CI

- **GitHub Actions 44243018205**, candidate suite head `af1402c1f2beff681d0724a37e0e1c3346afe3fe`: CPython **3.11: 256 passed, 1 failed (257 collected)**; **3.12: 256 passed, 1 failed (257 collected)**.
- The only failed case remains the unmodified accepted-runner `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]` (observed synthetic preflight `RuntimeError`, expected `CleanupUnconfirmed`). It was **not** skipped, xfailed, edited or softened.
- The focused offline verification run **44243018469** at the same SHA concluded **success**.
- The separate read-only host prerequisites workflow at the same SHA **44243018220** concluded **failure**. No environment acceptance inferred.
- Many green tests in the full suite are earlier *vulnerability-demonstrating* diagnostics. The green count is **not** a security signoff.

## Remaining security gates

This exercise proves the candidate's **specific sanitized timeout paths under fake subprocess conditions only**. It does not prove that every `BaseException` from `poll()`, `killpg()`, `communicate()`, a caller traceback with locals, or logging outside this helper is credential-free. Independent review must decide whether those deserve bounded additional fault injection. Remaining Codex findings: **H2 flag-before-unlink interruption**, **M1 success manifest before SIGTERM restoration**, **M2 interrupted resource reconciliation with residual PASS state**. The original F04-A regression remains blocking. Owner-controlled protected-runner security rebaseline remains a separate explicit authorization; no Docker/PG 16.15, OCI/16-wheel rebaseline, merge, deployment or B5-4-2 allowed.

**F04-H1 candidate test gate passed; independent signoff pending. F-04 OPEN / NO-GO.**
