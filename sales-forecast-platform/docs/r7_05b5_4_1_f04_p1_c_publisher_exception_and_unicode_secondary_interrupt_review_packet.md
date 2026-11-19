# Phase 7 / R7-05B5-4-1 / F-04 — P1-C Publisher Exception Boundary and Unicode Secondary Interrupt

**Date:** 2026-11-19  
**Scope authorized:** P1 candidate-only offline fault-injection / negative testing following owner-approved PLAN ONLY. **NOT AUTHORIZED:** edits to protected runner or operational B5 wrapper, protected security rebaseline, removal of candidate execution guard, Docker/PostgreSQL execution, dependencies/image downloads, merge or deployment.  
**New Codex AI review status:** **PENDING**, because previous Codex `CHANGES REQUIRED` reviewed old P1-B candidate blob `35ca0ef5fabe1e66f3bf8c65a74338f492c1c9e4`.  
**Gate:** P1-C synthetic target cases **PASS**, full candidate CI **FAILURE** due original immutable protected-runner redaction RED. **F-04 OPEN / NO-GO, B5-4-2 BLOCKED.**

## 1. Verified immutable and changed source identities

Repository `Ahmedbabiker94/AI_Sales_prediction_Platform`, branch `fix/r7-05b5-4-1-bootstrap`.
**Final code-tested commit:** `8e22e03701b317b99cc8583cea8e7bc44f2776c6`.

| Artifact | Git blob SHA and status |
|---|---|
| `tests/postgres/run_postgres.py` (protected) | `0863e81b733381a80b2f83560d8bbc231513ba41` **UNCHANGED** |
| `tests/postgres/run_b5_4_1_postgres.py` (operational wrapper) | `040d0d30bb719d646eb350b229ccd9a4c5f2bf79` **UNCHANGED** |
| `tests/postgres/run_postgres_security_patch_candidate.py` | `2cc9a34eee3c3a3a3fe5cd2c023fd2fc81965be5` **NEW: Codex review required** |
| `tests/postgres/run_manifest_admission_controller_candidate.py` | `f8ffa51dbf072deaad0ab7e3701965a05b471548` unchanged |
| `tests/runtime/test_r7_05b5_4_1_f04_r2_04_r4_actual_reporting_stage.py` | `4b3e08ddee16969a76f64cbcf43101ef959990f7` |
| `tests/runtime/test_r7_05b5_4_1_f04_r2_01_secondary_reap_sanitization.py` | `e25b0ad881ba07572aa9365d24c48a7f29b82c9a` |
| `tests/runtime/test_r7_05b5_4_1_f04_t2_partial_evidence_write.py` | `b7b250d17049b956b18c8b3452edecba176a1344` |
| `tests/runtime/test_r7_05b5_4_1_f04_r2_04_publication_acceptance.py` | `1509f95d10d02a1477baae2c6341ade04d6b1685` |
| `tests/runtime/test_r7_05b5_4_1_f04_r2_04_controller_binding.py` | `92a75489619e75db0cf6f3167c6288220f86cbbb` |
| Repository-root `.github/workflows/r7-b5-4-1-f04-candidate-offline.yml` | `daa477ceba84bd7a6a85dcb4d40fa33fa0452aea` **UNCHANGED; tests already explicitly included** |

All code paths above other than workflow are relative to `sales-forecast-platform/`.

**Hard stop verified:** The review-only `command()` still starts with unconditional `raise RuntimeError('OFFLINE_REVIEW_ONLY: candidate command execution forbidden')` and the candidate still has **no CLI entrypoint**. Only an **in-memory AST clone**, behind a completely fake Popen/killpg/daemon, removes this guard for isolated unit tests. The actual accepted/operational runner was not edited.

## 2. Reviewer findings and test-first RED

Codex P1-B returned `CHANGES REQUIRED` with:

**P1-C-01 HIGH:** the previous manifest serialization/publisher `except BaseException` raised new cleanup errors or re-raised the pending original exception **inside** the raw publication-exception handler. Even `raise ... from None` leaves `__context__` reachable; a maliciously credential-bearing `UnicodeError.object/args` or `OSError.args/filename/custom attributes` could leak. With no pending primary, the raw exception escaped directly.

**P1-C-02 MEDIUM:** `UnicodeError` primary subprocess failure followed by secondary `KeyboardInterrupt` or `SystemExit` from process poll, killpg or bounded reap was incorrectly transformed into `RuntimeError`. Timeout primary already preserved the secondary control-flow class.

**Twelve RED cases added before fixes:**
- **6 publisher combinations:** 2 unsafe publisher exception classes (`UnicodeDecodeError` with a generated synthetic credential in `.object`/`.args`, `OSError` with it in message, `.args`, `filename`, `object`, `stderr`) × 3 outcomes (uncertain owned cleanup, confirmed cleanup with pending safe primary, successfully completed body but publisher failed). Each requires no admitted receipt, safe public exception graph (recursive `__cause__`/`__context__`, attributes, formatted traceback), retained `FAILED / EVIDENCE_UNAVAILABLE`, and correct priority.
- **6 Unicode secondary interrupt combinations:** fake `poll`, fake `killpg`, fake bounded second `communicate` × `KeyboardInterrupt` or `SystemExit`. Verify correct public control-flow type, no disclosure of generated test credentials in exception attributes/chains, synthetic `DAEMON_UNRESOLVED` ledger, bounded secondary cleanup and ContextVar reset.

[**Test-first RED CI 44458923208**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44458923208) at `e8499ac6b3f1dd68276310bc6092777c627a61dc`: Python 3.11 & 3.12 **each 371 passed / 13 failed (384 collected)**. Twelve new cases were RED, plus the original protected-runner RED.

## 3. P1-C-01 candidate-only security correction — untrusted publisher boundary

Publisher/finalizer faults are caught only long enough to record a fixed `publication_failed` boolean and `publisher_interrupt` classification. The actual exception object is **not** stored. After exiting the `except BaseException as publisher_error` handler, a separate `if publication_failed:` branch selects the public exception:

- **Unconfirmed actual owned-resource cleanup** — fixed-message `CleanupUnconfirmed`, **even if publication also failed**; do not conflate default fail-closed setup status alone with observed ambiguous daemon mutation.
- **Confirmed cleanup, with safe primary exception pending** — re-raise the exact primary exception object with traceback **outside** the publisher handler. Its original diagnostic identity is retained while the raw publisher exception is no longer part of the active context.
- **No primary, publisher `KeyboardInterrupt` / `SystemExit`** — new safe exception of the corresponding control-flow class with fixed suppressed-details message.
- **No primary and ordinary serialization/publisher fault** — **new** fixed-message `OSError('terminal manifest publication failed; details suppressed')`, with neither the original unsafe exception nor its attributes reachable in the tested public exception graph.

The public outcome change for ordinary standalone publisher errors is **intentional and security-relevant**: the prior raw `ValueError`, `FileExistsError`, or original publisher `OSError` and arbitrary text are **no longer propagated** through this boundary. Three existing negative test files were updated to require the **fixed safe public error**, **without dropping any fail-closed assertions**. T2 still checks rejected manifests, existing destination never overwritten, owned-link rollback, original pending-primary preservation, cleanup, signal restoration; R2-04 still checks surviving bare PASS is **not admitted**, and cross-run substitution cannot borrow another invocation's completion.

No cryptographic archive attestation or filesystem rollback guarantee was introduced. The synchronous in-process controller and default-deny archive policy remain unchanged.

## 4. P1-C-02 candidate-only correction — preserve secondary interruption class

Inside the `command()` `except BaseException`, after existing bounded reap and stage-code recording, the `UnicodeError` branch now follows the existing timeout branch's explicit secondary-interruption policy: return a freshly constructed `KeyboardInterrupt` or `SystemExit` with constant safe message when the secondary reaping operation observed that type; otherwise retain a fixed safe `RuntimeError` for primary subprocess decode failure. New exception is raised **only outside** the raw error handler, keeping public `__context__`/`__cause__` clear in direct AST tests.

The mutating daemon ledger remains `DAEMON_UNRESOLVED` regardless of bounded local CLI reaping. No raw Unicode `.object`, args, argv, outputs or secondary interrupt objects are exported.

## 5. Extra guard tests and final CI

Two additional candidate-only negative tests exercise **publisher KeyboardInterrupt and SystemExit with synthetic credential-bearing messages/objects**. They require preservation of public control-flow **class**, a clean exception graph, failed/unavailable evidence, cleanup and context reset, and no admission.

[**FINAL CI 44459451460**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44459451460), code-tested commit `8e22e03701b317b99cc8583cea8e7bc44f2776c6`: **Python 3.11 and 3.12 each 385 passed / 1 failed (386 collected)**. **All 14 P1-C new cases GREEN**, previous P1-B/R4/H1/H2/M1/M2/T1/T2/R2-01 through R2-04 candidate negatives continue passing. The **one original, unchanged** protected runner regression `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]` remains RED; **entire workflow conclusion is FAILURE**. It has not been skipped, xfailed, deleted, weakened or patched.

[Focused offline 44459451522](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44459451522) **success**. [Read-only host prerequisites 44459451507](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44459451507) **failure**; Docker/PG not invoked.

## 6. Known limits and next decision gate

**Important exception-safety boundary:** The new test protocol inspects the *public exception object graph* (`str`, `repr`, args, known attributes, recursive context/cause, formatted tracebacks). It does **not** prove safety against introspection of traceback **frame locals** or arbitrary sensitive primary exceptions from unreviewed callbacks; those may require a separately reviewed boundary. `from None` is not misrepresented as clearing `__context__`; the publisher handler is exited before public raise.

**Question for fresh Codex:** Is the new safe generic standalone `OSError` contract acceptable for original `FileExistsError`/ValueError scenarios? Any new policy mismatch, accidental exception chain, or broadened external-data boundary requiring tests? Is the prior primary object always known safe? Does `publisher_interrupt` correctly preserve termination semantics under every tested outcome? Do not infer full operational approval from synthetic tests.

**Still BLOCKED:** protected source original RED; actual operational B5 wrapper/runner/controller module binding and all admission consumers not yet verified, need P1 read-only consumer inventory; OCI no-pull/Docker29, sixteen-wheel provenance/license/OSV, `_HOST_HEALTH`, and separately authorized disposable PostgreSQL 16.15 tests. Archive/restart default deny, no real signal scheduling/durability/daemon quiescence guarantee.

**Next strictly sequential:** fresh **read-only Codex P1-C** technical re-review of exact post-fix blobs and CI, then remaining P1 read-only operational entrypoint/consumer inventory and owner F04-G01 personal security decision. **No protected-source write without NEW explicit owner authorization.**

**Status: P1-C TARGETED CANDIDATE OFFLINE TESTS PASS, CODEX RE-REVIEW PENDING. F-04 OPEN/NO-GO; B5-4-2 BLOCKED.**
