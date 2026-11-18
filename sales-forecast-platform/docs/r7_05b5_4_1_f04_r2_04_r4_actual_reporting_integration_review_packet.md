# Phase 7 / R7-05B5-4-1 / F-04 — R2-04-R4 Candidate Actual B5 Reporting Stage Integration

**Recorded:** 2026-11-18. **Owner authorization:** explicitly approved **R3 design** and authorized **R2-04-R4 candidate-only offline integration verification**. This is NOT owner self-review approval of protected changes, protected-runner/wrapper rebaseline, Docker/PG execution, source promotion, or F-04 closure.

**Decision:** **R4 targeted synthetic candidate integration tests PASS**, pending a **fresh read-only Codex AI source review** and separate owner security/risk decision. **F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**

## Pinned source identity

Repository: `Ahmedbabiker94/AI_Sales_prediction_Platform`; branch `fix/r7-05b5-4-1-bootstrap`; **final code-tested commit** `11e57a45820647bdaeb29a2ea6ae332122d2c1d7`.

| Artifact | Git blob SHA | Scope |
|---|---|---|
| Protected `tests/postgres/run_postgres.py` | `0863e81b733381a80b2f83560d8bbc231513ba41` | **UNCHANGED** accepted runner |
| Protected operational `tests/postgres/run_b5_4_1_postgres.py` | `040d0d30bb719d646eb350b229ccd9a4c5f2bf79` | **UNCHANGED** original reporting wrapper |
| `tests/postgres/run_postgres_security_patch_candidate.py` | `ccd1efac292d9504a59eeef3eaf8b21889879b59` | **UNCHANGED** review-only candidate, `OFFLINE_REVIEW_ONLY` guard |
| `tests/postgres/run_manifest_admission_controller_candidate.py` | `f8ffa51dbf072deaad0ab7e3701965a05b471548` | **UNCHANGED** trusted in-process synchronous controller |
| `tests/postgres/run_b5_4_1_reporting_stage_candidate.py` | `a9047bd6d23c0581ab0ad609aa595d02fc8acbde` | NEW candidate source-parity reporting stage |
| `tests/postgres/run_b5_4_1_postgres_admission_candidate.py` | `79c35ba50946dd3a54baee903f8ee1c23f3ed0c7` | NEW fixed actual-stage candidate consumer path |
| `tests/runtime/test_r7_05b5_4_1_f04_r2_04_r4_actual_reporting_stage.py` | `670ebdea2090b4fed5a2cb2d0172a709f5161f12` | NEW actual-stage synthetic integration tests |
| `tests/runtime/test_r7_05b5_4_1_f04_r2_04_controller_binding.py` | `ac60adabbbb50d0cac80022194418215f29c2bec` | Expanded same-length tamper/reader/callback/snapshot tests |
| `.github/workflows/r7-b5-4-1-f04-candidate-offline.yml` | `daa477ceba84bd7a6a85dcb4d40fa33fa0452aea` | Explicit pytest inventory, compile and path trigger |

All code/test paths except the workflow are relative to `sales-forecast-platform/`. The above baseline SHAs were re-read from GitHub after the final CI; no protected file was touched.

## Why R4 was needed

Codex R3 assessed the controller design as `TECHNICALLY ACCEPTABLE FOR OWNER REVIEW` under a trusted synchronous, in-process model but **blocked operational integration**. The actual `run_b5_4_1_postgres.py` imports `command`, `run`, `write_evidence`, `redact_test_secrets` from the frozen runner, obtains frozen `_LIFECYCLE` inside `reporting_stage`, and calls frozen `run` directly. Prior R3 consumer tests passed a **stub** callback, not the real reporting-stage command construction or resource ownership. A wrapper-only import swap would risk separating cleanup ownership.

R4 copies the actual **reporting_stage AST** from the pinned operational wrapper into a NEW review-only module. Its only function AST difference is changing `from run_postgres import _LIFECYCLE` to `from run_postgres_security_patch_candidate import _LIFECYCLE`. Fixed `TESTS` selectors, the Docker resource arguments, network-none flags, read-only mounts, envfile, stage stdout/stderr handling, redaction, registration of `extra_containers` **before** Docker invocation, and exception behavior remain unchanged. A negative source-parity regression fails if additional function-body differences appear.

The new `run_actual_reporting_stage_with_bound_admission_candidate()` in the candidate consumer has a **fixed**, in-process callback that sets `b5_4_1_evidence_dir` and invokes this actual parity stage, through `invoke_candidate_and_admit()`. The controller still directly owns the candidate runner invocation, the completion result, UUID, exact finalized record and SHA-256/byte binding. It returns immutable `manifest_bytes` only after successful, matched completion. It does **not** accept caller-supplied `run_completed=True`.

**IMPORTANT:** The operational wrapper is not rewired. This is a **candidate-only proven design path**, not an integrated production path. The review-only candidate command hard-stop remains; all external commands in tests are replaced by a mocked in-process function. No Docker, PostgreSQL, subprocesses, OS signals, dependency downloads or real credentials were used.

## Test-first evidence, corrections and final CI

R4 stage tests and expanded controller-negative tests were added *before* implementing the new reporting-stage candidate and fixed adapter. An intermediate candidate CI failed because the not-yet-created reporting module could not be compiled/imported; this is a test-first **implementation-presence RED**, not a live exploit. After the parity stage and wrapper were added, new integration cases uncovered an overly broad **test fixture mistake**: the fake command identified **foundation/restart** pytest commands as B5 reporting commands. That fixture was narrowly corrected to classify only container names ending `-b5-4-1`; no production implementation was changed for this test correction.

**Final CI:** [Candidate offline GitHub Actions run **44351301396**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44351301396) at **code-tested commit `11e57a45820647bdaeb29a2ea6ae332122d2c1d7`**, on Python **3.11** and **3.12**, **each 351 passed / 1 failed (352 total)**. New R4 and earlier R2-01/02/03/04 candidate-only tests passed. The **one unchanged frozen accepted-runner** regression `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]` remains RED (synthetic preflight `RuntimeError` rather than expected `CleanupUnconfirmed`). **Overall CI conclusion remains FAILURE — never describe the full job as GREEN.**

[Focused offline verification **44351301737**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44351301737) succeeded. [Read-only host prerequisite **44351301419**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44351301419) failed. These are not supply-chain certification or permission for Docker/PG.

### New reporting-stage integration cases

- AST parity of actual `reporting_stage` and fixed three-module `TESTS` selectors; only the expected import path is redirected.
- Fully mocked successful reporting stage calls the fixed candidate consumer, tests resulting manifest's `operations['b5-4-1']`/test selectors, fixed Docker isolation argv, generated stage stdout/stderr, exact receipt UUID/sha256, shared candidate `_LIFECYCLE`, prior SIGTERM restoration, and ContextVar resets.
- Registering `extra_containers` **before** the stage command, and showing the same name inside the candidate runner's owned-resource `remove_owned` reconciliation.
- Returning nonzero reporting status, interruption by `KeyboardInterrupt`, and synthetic partial `b5-4-1.stdout` write; each propagates as failure, preserves owned-resource reconciliation, and does **not** issue a successful admitted manifest.
- Direct use of the stage with a foreign lifecycle is rejected before command invocation.

### Added controller and evidence tests

- **Same-length content substitution:** change a valid JSON `source_commit` byte while preserving file length and PASS; the bound controller rejects it by digest/byte disagreement, not only by size.
- **Reader boundaries:** reject symlink, directory/nonregular, oversized, unreadable via injected PermissionError, absent `O_NOFOLLOW`, and missing file. **FIFO/open blocking is not separately tested**; hostile filesystem behavior remains out of scope.
- **Callback protocol:** duplicate callback and wrong credential/evidence path fail closed before admission.
- **Post-admission mutation:** delete and replace the on-disk terminal file, while consumer retains the immutable verified `manifest_bytes` and sha256 from the earlier synchronous admission; archive default-deny is retained.

## Remaining security boundaries and explicitly withheld authorization

1. **Protected-source integration is NOT implemented:** actual operational runner and B5 wrapper still import/use frozen code. The candidate stage's `command` is a module alias to the review-only runtime; the offline test patches the same fake command into the runner and stage. During a later authorized migration, production imports must resolve to exactly one corrected operational runtime module, with a single `_LIFECYCLE` and effective creation/reconciliation ownership. Merely removing the candidate's hard stop and running it is forbidden.
2. **Real execution not proven:** no Docker 29, pinned OCI images/no-pull, PostgreSQL 16.15, actual process group signal scheduling, daemon quiescence, wheel-bytes/provenance/OSV/licenses or operational reporting-stage run has been verified. The original frozen-runner RED must turn green **without modifying the regression** if a security-only rebaseline is later authorized.
3. **Security model:** trusted synchronous single process/controller and stable host paths; no malicious host or concurrent mutation claims. File descriptor `O_NOFOLLOW` blocks final-component symlinks but does not solve hostile ancestor replacement or fsync durability. Archival/restart/cross-process acceptance remains **default deny** pending a separate authenticated completion attestation, should business needs require it.
4. **Admission coverage:** candidate wrapper fixed path has no raw PASS fallback, but this does NOT prove the real operational wrappers or external/archive consumers all adopt the new path. Future protected changes must be narrow, pinned, reviewed and separately owner-authorized; tests must verify no operational file-only acceptance.
5. **Residual review:** ask Codex to inspect exact new stage source parity, binding of helper imports and resource cleanup, injected fault correctness, same-length tamper/reader tests and any remaining security or integration gaps.

## Next decision gate

Obtain **fresh, read-only Codex AI R4 review** of the exact latest source/testing identity and R4 report. Under the F04-G01 personal-project governance amendment, the project owner can personally review/accept bounded risks based on the AI technical review, but **the owner has not yet approved protected-source rebaseline**. Any later **separate** authorization must cover the accepted runner, operational reporting wrapper, admission controller operational binding, original RED regression and real integration checks by pinned SHA. Do not skip Phase 7 order.

**R4: TARGETED SYNTHETIC INTEGRATION TESTS PASS / AWAITING CODEX REVIEW. F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**
