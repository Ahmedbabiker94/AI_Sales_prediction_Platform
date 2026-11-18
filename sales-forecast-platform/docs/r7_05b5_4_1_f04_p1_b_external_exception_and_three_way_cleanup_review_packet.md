# Phase 7 / R7-05B5-4-1 / F-04 — P1-B: Subprocess External Exception Sanitization and Three-way Cleanup Priority

**Date:** 2026-11-18  
**Direction:** Owner's earlier `Yes` approved controlled rebaseline **PLANNING ONLY**, and `Let's continue` authorized **P1 candidate-only offline negative tests**. Owner then supplied a Codex AI P1-A verdict `CHANGES REQUIRED` identifying two source-derived defects. This document reports P1-B candidate-only tests and corrections, **NOT** an owner protected-source rebaseline approval.  
**Technical decision for this step:** **10 new candidate-only negative tests PASS** on Python 3.11 and 3.12, **awaiting fresh read-only Codex AI review**. F-04 **OPEN / NO-GO**, B5-4-2 **BLOCKED**.

## 1. Exact code/test identities

Repository `Ahmedbabiker94/AI_Sales_prediction_Platform`; branch `fix/r7-05b5-4-1-bootstrap`.  
**Final P1-B code-tested commit:** `7ec6c313fbdc3cff3da7228806c52c3767e984c6`.

| Item (paths relative to `sales-forecast-platform/`) | Git blob SHA / disposition |
|---|---|
| **Protected accepted** `tests/postgres/run_postgres.py` | `0863e81b733381a80b2f83560d8bbc231513ba41` — **UNCHANGED** |
| **Protected operational** `tests/postgres/run_b5_4_1_postgres.py` | `040d0d30bb719d646eb350b229ccd9a4c5f2bf79` — **UNCHANGED** |
| **Review-only security candidate** `tests/postgres/run_postgres_security_patch_candidate.py` | `35ca0ef5fabe1e66f3bf8c65a74338f492c1c9e4` — **NEW, NOT YET CODEX REVIEWED** |
| `tests/runtime/test_r7_05b5_4_1_f04_h1_timeout_sanitization.py` | `2f36c4a760b737a87e05bf372b872f33c1461b1d` |
| `tests/runtime/test_r7_05b5_4_1_f04_r2_04_r4_actual_reporting_stage.py` | `ae72cfe457c590a78c7e76550e3267a1e01902bf` |
| Synchronous controller `tests/postgres/run_manifest_admission_controller_candidate.py` | `f8ffa51dbf072deaad0ab7e3701965a05b471548` — unchanged |
| R4 reporting stage `tests/postgres/run_b5_4_1_reporting_stage_candidate.py` | `5a82cb0219948c0b895f92b603252a547957bed7` — unchanged |
| Repository-root `.github/workflows/r7-b5-4-1-f04-candidate-offline.yml` | `daa477ceba84bd7a6a85dcb4d40fa33fa0452aea` — unchanged explicit test inventory |

`OFFLINE_REVIEW_ONLY` is still the **unconditional first executed statement** of the candidate's `command()`, the candidate **still has no CLI entrypoint**, and the protected baseline source was not changed. H1 test harness removes only that first AST guard **in memory** under fake Popen, fake `killpg`, fake environment and fake signals. The end-to-end B5 tests use a fake source/image/daemon and this AST helper only on the synthetic reporting command. **No real subprocess, Docker, PostgreSQL, source promotion, credentials, OCI operations, downloads, merges or deployment.**

## 2. P1-A-01 HIGH — unsafe primary `UnicodeDecodeError` propagation

**Codex source-derived finding:** `Popen(..., text=True)` / `communicate()` may raise a `UnicodeDecodeError` whose `.object` and `.args` contain password-bearing child output. Before this correction, the command handler rebuilt safe public exceptions only for `TimeoutExpired`; all other errors escaped raw. Terminal error handling might preserve a raw decoding error via a primary re-raise or implicit `CleanupUnconfirmed.__context__` chain.

**Test-first RED:** Added **4 H1 negative cases** crossing two modes (fake Popen creation error vs fake communicate decoding error) with two command types (mutating `docker run --name r7-05b3-...` vs read-only `docker exec`). The `UnicodeDecodeError.object` contains a generated **synthetic test-only credential** and genuinely invalid UTF-8 byte `0xff`. The tests inspect public `str`, `repr`, `.args`, `.object`, `.cmd`, `.output`, `.stderr`, recursively linked `__context__` and `__cause__`, formatted traceback, and mutating ledger `DAEMON_UNRESOLVED`.

[RED GitHub Actions run **44385153843**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44385153843) on initial test commit `e061b58b469618fd874d85569ddca17e39194d36`: **both Python 3.11 and 3.12: 361 passed / 5 failed (366 collected)** — four new decoding errors plus original frozen accepted-runner RED. The test's initial synthetic byte literal was then corrected to actual `0xff`, and its Popen-failure case correctly excludes `local_reaped` (no child existed to reap), without relaxing secret checks.

**Narrow candidate-only correction** (commit `1be1dd631342dbd6372bbd79a76b85f9a837223d`): in the same `command()` `except BaseException` block, **after** existing bounded local CLI reaping and creation-ledger state recording, add:

```python
elif isinstance(error, UnicodeError):
    sanitized_timeout = RuntimeError(
        'disposable PostgreSQL subprocess decoding failed; details suppressed')
```

The sanitized public exception is raised **outside the raw exception handler**, reusing the existing R2-01 pattern. The original `UnicodeError` with `.object` and `.args` never becomes the public error, `__context__` and `__cause__` are empty in direct command tests, and mutating creation stays `DAEMON_UNRESOLVED`. The review-only subprocess guard remains intact.

**End-to-end B5:** Two additional candidate-controller scenarios invoke the **actual B5 reporting stage** with AST-cloned `command()` raising the synthetic decoding error: (a) uncertain resource cleanup and successful failed-manifest publication, (b) same uncertain cleanup with an additional failed manifest publisher. They recursively inspect public exception chains and formatted tracebacks for synthetic credentials and verify no PASS admission, retained `FAILED/CLEANUP_UNCONFIRMED`, owned resource listing, credential unlink and ContextVar reset.

**Important scope limit:** This correction is limited to **known `UnicodeError` objects raised inside the subprocess command try block** and the existing `TimeoutExpired` sanitization. It is **NOT** proof of secrecy for arbitrary custom exceptions, traceback local variables, memory dumps, malicious callbacks, hostile environment, or all possible external components. `raise ... from None` alone is NOT treated as proof of empty `__context__` in cleanup/finalizer errors. These remaining risks must be reviewed prior to protected source transfer.

## 3. P1-A-02 MEDIUM — three simultaneous failures mask cleanup risk

**Codex source-derived finding:** partial B5 output `OSError` + reporting-container cleanup refusal + terminal manifest publication failure caused the publication-exception handler to unconditionally re-raise the pending original `OSError`. That bypassed the following `CleanupUnconfirmed` gate, while no valid terminal manifest existed for a consumer to inspect.

**Test-first RED:** A new mock test drives all **three simultaneous faults** through the real candidate B5 stage, runner, and admission controller. It requires public `CleanupUnconfirmed`, retained `FAILED / CLEANUP_UNCONFIRMED / EVIDENCE_UNAVAILABLE`, primary `error_type='OSError'`, attempted owned-container cleanup, transient envfile removal, previous SIGTERM handler restoration, no terminal accepted file, no receipt and ContextVar reset.

[Combined-fault RED run **44385770034**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44385770034), test commit `5134124946bd526129236bf747c94054b0c0a7ca`: Python 3.11 and 3.12 each **366 passed / 3 failed (369 collected)**; the two additional newly RED cases were the triple-fault assertion and the same decoding scenario with failed publication, plus original protected RED.

**Narrow candidate-only correction** (commit `478d75b0d1ebafd03acc35470ebd2ff3b532e338`): in the manifest finalization/publication `except BaseException` block, mark `result='FAILED'` and `evidence_status='EVIDENCE_UNAVAILABLE'` as before. Detect **actual unconfirmed owned cleanup** by requiring:
- `cleanup_passed is not True` and `cleanup_status=='CLEANUP_UNCONFIRMED'`;
- **plus** tracked `possible_owned_containers`, `possible_owned_volumes`, or `credential_file_cleanup=='UNCONFIRMED'`.

When those conditions hold, raise an explicit `CleanupUnconfirmed` instead of an ordinary pending stage/publication error. When cleanup is independently confirmed, continue to preserve the original in-flight failure **object** if publication later fails, as already required by earlier regression tests. Do **not** revert to the earlier rejected blanket preservation of any pending error.

**Precedence matrix (candidate-only):**

| Situation | Public failure classification |
|---|---|
| Stage failed; owned cleanup confirmed; publication succeeds | Original stage exception (existing rule) |
| Stage failed; owned cleanup unconfirmed; publication succeeds | `CleanupUnconfirmed` (existing rule) |
| Stage failed; owned cleanup confirmed; publication fails | Original stage exception (existing rule) |
| Stage failed; real owned cleanup unconfirmed; publication fails | **`CleanupUnconfirmed`** (P1-A-02 correction) |
| No actual owned resource recorded yet during failed setup | Do not treat the default unconfirmed status alone as observed daemon mutation; preserve existing preflight/error-policy tests |

**Risk to review:** Raising `CleanupUnconfirmed` from within an active publisher exception may retain implicit `__context__` references, even with `from None`; the test that injects a **sanitized** subprocess decode error checks no generated secret appears in such a public chain. This is **not a blanket guarantee for arbitrary credential-bearing secondary publisher exceptions**. Ask Codex to assess whether another narrower external-error sanitizer is required.

## 4. Stronger timeout/late-resource/inventory modelling

Three further candidate-only negative cases address Codex's modelling criticism without changing runtime source:
1. **Container inventory failure in isolation** (no ambiguous creation attempt; all fake resource cleanup succeeds) blocks acceptance via `remaining_containers='UNAVAILABLE'`.
2. **Volume inventory failure in isolation** similarly blocks acceptance via `remaining_volumes='UNAVAILABLE'`.
3. **Prior confirmed absence followed by late observation of the same labeled owned reporting container** in the final read-only inventory: despite fake `remove_owned=True`, late observed `remaining_containers` prevents cleanup success/admission.

These tests demonstrate **conservative sequencing of synthetic observations**. They do **not** model actual Docker daemon timing, namespace reuse, OS signals, or quiescence under concurrent mutations.

## 5. Final CI and governance

**Final candidate offline CI [run 44386347847](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44386347847)** at code-tested commit `7ec6c313fbdc3cff3da7228806c52c3767e984c6`, Python **3.11 and 3.12 each 371 passed / 1 failed (372 collected)**. **All 10 new tests PASS**, and prior candidate regressions continue to pass. Sole failure is the **unchanged original protected** `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]`, which has **not** been skipped/xfailed/weakened. **Overall F-04 candidate CI remains FAILURE, NOT a green acceptance.**

[Focused offline verification **44386348350**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44386348350) succeeded. [Read-only host prerequisites **44386348698**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44386348698) failed; no real Docker/PG was authorized.

**Gates remaining:** Independent *AI* (Codex) read-only review of this changed candidate and exact tests, P1 read-only operational entrypoint/admission inventory, owner F04-G01 personal risk assessment, **new explicit pinned authorization** before any protected runner/wrapper/controller security rebaseline. After authorization and source transfer, original frozen RED must become GREEN unchanged and full Python 3.11/3.12 security CI must pass before considering other external host/OCI/wheel/postgres gates. Archives and restart admission remain default-deny. Any subsequent actual Docker/PostgreSQL test still requires its own explicit permission and prerequisites.

**Current stage:** P1-B TARGETED CANDIDATE TESTS PASS / NEW CODEX REVIEW PENDING.  
**Global:** F-04 **OPEN / NO-GO**; B5-4-2 **BLOCKED**.
