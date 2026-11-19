# Phase 7 / R7-05B5-4-1 / F-04 — Controlled Security Rebaseline Plan (PLAN ONLY)

**Date:** 2026-11-18  
**Owner decision A:** **APPROVED for planning only.** The owner explicitly accepted the narrow AI-reviewed, trusted synchronous candidate design as a basis for preparing this plan. **The owner's "Yes" does not authorize implementing this plan**, protected-source edits, integration execution, deployment or release.  
**Governance:** F04-G01 owner self-review with Codex AI technical advice; **no independent human security signoff claim**. Full personal residual-risk and protected-source authorization are separate future owner decisions.
**Status:** **PLAN PREPARED / EXECUTION NOT AUTHORIZED — F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**

## 1. Scope, version pins and actual baseline

Repository: `Ahmedbabiker94/AI_Sales_prediction_Platform`  
Planning branch: `fix/r7-05b5-4-1-bootstrap`  
Last **code-tested** candidate commit: `22b70f047b9260c83bc2ca4b2d2db8769a754f67`. Administrative plans/issue comments created later are not new test runs.

| File (all paths relative to `sales-forecast-platform/` unless noted) | Current Git blob | Planned handling (NOT executed) |
|---|---|---|
| `tests/postgres/run_postgres.py` | `0863e81b733381a80b2f83560d8bbc231513ba41` | **Protected baseline**; narrowly patch only reviewed security deltas under separate authorization |
| `tests/postgres/run_b5_4_1_postgres.py` | `040d0d30bb719d646eb350b229ccd9a4c5f2bf79` | **Protected operational wrapper**; wire same-runtime controller + actual reporting stage, no raw PASS fallback |
| `tests/postgres/run_postgres_security_patch_candidate.py` | `ccd1efac292d9504a59eeef3eaf8b21889879b59` | **Reference only**; preserve `OFFLINE_REVIEW_ONLY` denial and no CLI, **do not copy/execute it operationally** |
| `tests/postgres/redaction_finalization_candidate.py` | `86f0b33ea460eec078991ba1f70d9c9c9ba67bae` | Pure redaction finalizer to be reviewed for import/transfer with original regression |
| `tests/postgres/run_manifest_admission_controller_candidate.py` | `f8ffa51dbf072deaad0ab7e3701965a05b471548` | Synchronous bound controller design; future operational import must point at **authorized corrected runner** |
| `tests/postgres/run_b5_4_1_reporting_stage_candidate.py` | `5a82cb0219948c0b895f92b603252a547957bed7` | Actual reporting-stage parity reference: lifecycle-import redirect + narrowly guarded output writes |
| `tests/postgres/run_b5_4_1_postgres_admission_candidate.py` | `79c35ba50946dd3a54baee903f8ee1c23f3ed0c7` | Fixed in-process callback/consumer design; no caller-supplied success flag |
| `tests/runtime/test_r7_05b5_4_1_f04_r2_04_r4_actual_reporting_stage.py` | `3c88abe69ccd19399506800a0fa69c98e3b2bc25` | R4/LOW test reference; additional operational tests must be non-destructive and separately reviewed |

**Observed CI:** [44362940928](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44362940928), CPython 3.11 and 3.12 each **353 passed, 1 failed (354 collected)**. The sole RED is **unchanged protected-runner** `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]`. Entire CI stays **FAILURE**. Focused offline run 44362940882 passed; host prerequisite 44362940969 failed. No live Docker/PG.

**Review history:** Codex R2 reported R2-01/02/03 PASS (scoped); R2-04 R3 and R4 reported `TECHNICALLY ACCEPTABLE FOR OWNER REVIEW` for **trusted synchronous single-process** admission; the subsequent R4-LOW report similarly accepted the conservative `FAILED / EVIDENCE_UNAVAILABLE` correction. All reports are AI opinions, not operational evidence.

## 2. Non-negotiable invariants

1. No production data, database, credentials, Docker mounts/socket in workers, TCP published ports, arbitrary test selectors or foreign resource deletion. Preserve fixed OCI image digests, wheel hashes, `TESTS`, envfile restrictions, `--network=none`, read-only mounts, scoped Docker labels, CPU/memory/PID limits and verified source snapshot requirements.
2. One **single operational** `run_postgres` module must own `run()`, `command()`, `_LIFECYCLE`, `_TEST_SECRETS`, evidence writing/redaction, creation ledger and resource reconciliation. Controller must invoke **that same corrected module**. B5 stage must import its helper aliases and lifecycle from it; no mixed candidate/frozen imports or duplicate `ContextVar` instances.
3. Only a trusted synchronous controller is allowed to admit a result after the **same** invocation returns normally and its UUID/record, exact terminal bytes and SHA-256 agree. The operational caller must treat both exception and `None` as rejection and use returned immutable `manifest_bytes`, **never** a raw `run-manifest.json` PASS or external `run_completed=True`.
4. Archives, process restarts and asynchronous/cross-process import are **default-deny** without a separately authorized authenticated/durable attestation design; no `.complete` or manifest field substitute.
5. Original negative regression must pass **unchanged**, without `skip`, `xfail`, deleting the test, or modifying its expectations; targeted negative test matrix also must pass. No release claim from isolated synthetic tests.
6. Candidate default-deny is permanent: **never remove its guard or turn it into a CLI entrypoint**. Operational security changes are **reviewed deltas against frozen source**, not copying an unexecutable fork wholesale.

## 3. Sequential, hold-point plan (NOT EXECUTED)

### P0 — Scope inventory, source freeze and risk register (planning; no protected edit)

- Reverify the exact blob SHAs above at the moment a new authorization is requested, including original wrapper, finalizer, controller and tests. Divergence => STOP and re-review.
- Inventory every operational manifest-admission/read path, CLI wrapper, archive importer, evidence uploader and job consumer in the repository; record call-site, whether it accepts a raw file, and policy needed. Do not presume the reviewed single wrapper exhausts all consumers.
- Produce exact file-level security patch diff proposal, excluding unrelated refactors, image/PG/wheel upgrades, test selector changes and production feature work.
- Pin a clear **rollback base** (commit/branch plus tree/blob IDs), preserve existing CI and Issue #2 evidence, and require clean source/branch protections appropriate to permissions.
- **P0 hold:** owner to separately approve the full transfer scope and exact SHAs **before any protected write**.

### P1 — Candidate-only required negative tests BEFORE protected transfer

Write/review targeted tests to expose the operational transfer risks without real Docker or PostgreSQL:
- B5 reporting command `TimeoutExpired` / uncertain daemon mutation; creation ledger remains unresolved; late named container appearance, inaccessible inventory, mixed Docker failures and bounded reconciliation; no PASS or receipt.
- Test interrupted reporting output writes (`KeyboardInterrupt`, `SystemExit`), exact exception-object/traceback preservation, plus partial stdout/stderr write combined with cleanup failure or terminal manifest publication fault. Status stays conservative; secrets do not escape attributes or exception chains.
- Assert the **actual operational module imports** and bound controller runtime are the same module and identical `ContextVar` objects, with registration before Docker invocation and same runtime owner for cleanup.
- Operational-entry-point negative tests: no raw PASS-file fallback, `None` or exception => nonzero/failed admission, wrong UUID/digest, wrong file path, duplicate callback and same-length valid JSON substitution => rejection; immutable verified bytes retained after file changes.
- Check one active accepted run cannot cross-admit a failed other run's surviving PASS; no archive/restart admission. Concurrency remains explicitly unsupported unless a separate mutation-safe snapshot design is tested.
- **P1 hold:** a read-only Codex AI review of negative tests/transfer diff and owner evaluation. Candidate-only gate PASS is **necessary but insufficient** for protected authorization.

### P2 — Authorized security-only protected-source change set (FUTURE, REQUIRES NEW OWNER CONSENT)

Only after a **new explicit owner message** authorizes these exact sources and deltas:
- **Runner:** apply R2-01 reaping/exception sanitization, R2-02 signal handler unrepresentability rejection, R2-03 original retained-record/finalizer synchronization, established cleanup/manifest exception and redaction protections, while **preserving operational command semantics and legitimate CLI**. Review finalizer dependency and provenance separately; never transplant `OFFLINE_REVIEW_ONLY` guard.
- **Controller:** implement/bind one operational controller to the corrected accepted runner, with same-invocation `run_id`+digest+exact bytes. No supplied completion boolean and no filesystem-only acceptance. Default deny imports/restarts.
- **B5 wrapper/reporting stage:** make operational `main()` call the trusted admission controller; ensure the stage uses only corrected runner's `command/write_evidence/redact_test_secrets/_LIFECYCLE`; explicitly transfer the R4-LOW two-write failure guard. Preserve run-scoped container registration and original B5 `TESTS`; never rely on an injected untrusted callback to choose the stage. Clearly fail if no verified receipt.
- Put these dependent edits in **one reviewable protected integration change set** so no partially rewired stage is treated as runnable/merged. No automatic merges or branch promotion. Keep the candidate and its protected-origin SHAs for forensic parity.

**P2 hold:** changed protected-source code must remain unpromoted pending P3 independent CI/review and owner decision; if source identity drifts, stop and do not improvise a patch.

### P3 — Read-only/offline CI verification of actual transferred source

- Verify frozen and candidate baseline SHA before patch, and **new exact blobs/commit** after patch. Review a narrow diff for pins, fixed argv, mounts, OCI checks, passwords, labels, cleanup and production avoidance.
- Run Python 3.11 and 3.12 candidate + **corrected accepted runner** regression tests with test-only fake commands, including original `unexpected_manifest_secret` test unchanged. **Full required security test matrix 0 failing, 0 skipped/xfail** (the specific original failure must be GREEN). Totals may increase with new tests; do not infer acceptance from prior 353/1.
- Test actual operational wrapper with stubbed CLI/protected runner and same-runtime binding, `None`/exceptions fail closed, correct admission receipt use and no raw file-only success path.
- Confirm test code never bypasses safety gates on a real host and cannot launch Docker/PG from CI; explicitly separate test simulations from real integration.
- Fresh **read-only Codex AI review** of new exact transferred Git blobs, diff, tests, source parity and remaining security boundaries. Codex advice is **not human-independent approval**.
- **P3 hold:** any failed security regression, unexpected diff, partial file wiring, unreviewed dependency or admission bypass => NO-GO; rollback plan prepared, no merge.

### P4 — Owner post-change decision / controlled rollback

- Owner records a **separate post-patch** personal risk assessment and decision, with source IDs, Codex review, negative-test evidence, residual limits, and exact disposition. Do not claim independent human signoff under G01.
- **If verification fails:** stop promotion; restore authorized working branch to pinned baseline source versions via a new auditable **revert** (not force push or history rewrite), preserve failing logs/evidence, and verify original SHAs restored where possible. Do not erase or classify external resources as cleaned without observation. Since real Docker/PG is not permitted by this plan, there should be no intentionally created daemon resources to clean.
- **If verification passes:** technical security-only rebaseline may be considered for owner acceptance **but F-04 remains OPEN/NO-GO** pending separately gated host/OCI/wheel/PG checks and operational acceptance.

### P5 — External prerequisites (NOT authorized by this plan)

Separately verify Docker 29 host/socket and no-pull indexed OCI pinned image availability, complete sixteen-wheel candidate-v2 hash/byte/provenance/license/OSV checks, `_HOST_HEALTH`, and obtain specific authorization for isolated disposable PostgreSQL 16.15 actual integration. Do not run real Docker, download wheels/images, install dependencies, invoke cloud services or access any production data as a result of this planning document.

## 4. Explicit stop/go decision criteria

| Gate | Required result | If missing |
|---|---|---|
| A — Scoped design acceptance | **OWNER ACCEPTED FOR PLANNING ONLY** (2026-11-18) | No execution |
| B — Protected-write permission | NEW exact `AUTHORIZE SECURITY-ONLY CONTROLLED REBASELINE` owner decision with pinned files/SHAs and change scope | **NO protected modification** |
| C — Negative source and tests | Updated reviewed diff; original frozen redaction regression passes unchanged; full 3.11/3.12 security matrix green | Revert/NO-GO |
| D — Runtime ownership/admission | Same corrected runner `ContextVar`; reporting registration/cleanup; no raw-PASS fallback; controller only admits returned verified bytes | NO-GO |
| E — Operational infrastructure | Docker29/OCI no-pull, wheel provenance, host health and explicitly authorized disposable PG16.15 integration | F-04 remains OPEN/NO-GO |

## 5. Required wording of a **future, separate** authorization

Any authorization must be personally issued by the owner **after** reviewing this plan and any additional P1 tests; it cannot be inferred from `Yes`, `Go`, this document or a Codex verdict.

> **AUTHORIZE SECURITY-ONLY CONTROLLED REBASELINE** of protected `sales-forecast-platform/tests/postgres/run_postgres.py` from baseline blob `0863e81b733381a80b2f83560d8bbc231513ba41` and operational `run_b5_4_1_postgres.py` from `040d0d30bb719d646eb350b229ccd9a4c5f2bf79`, using the separately reviewed candidate runner `ccd1efac292d9504a59eeef3eaf8b21889879b59`, finalizer `86f0b33ea460eec078991ba1f70d9c9c9ba67bae`, controller `f8ffa51dbf072deaad0ab7e3701965a05b471548`, reporting-stage candidate `5a82cb0219948c0b895f92b603252a547957bed7`, and B5 consumer candidate `79c35ba50946dd3a54baee903f8ee1c23f3ed0c7`. Allowed edits are ONLY the reviewed security corrections and mandatory same-runtime bound admission integration, associated exact tests/workflows/docs. Preserve original regression unchanged, fixed pins/flags/ownership, the review-only candidate hard stop and no CLI. **No Docker/PG execution, downloads, merge, deployment or F-04 closure**. Require fresh Python 3.11/3.12 test evidence and Codex review, with an auditable revert on any gate failure.

**THIS IS A TEMPLATE ONLY; IT IS NOT AN ISSUED OWNER AUTHORIZATION.** Its scope must be refreshed against the actual reviewed P1 source and updated SHAs before use.

## 6. Final governance record

F04-G01 says no separate human specialist is required for this *personal project*; owner personally owns acceptance risk and evaluates Codex AI advice. That flexibility **does not waive** original regressions, protected-source permission, supply-chain checks, host readiness or real isolated integration gates.

**Current authorized activity completed:** A — planning.  
**Current prohibited:** any protected write or rebaseline execution; candidate hard-stop removal; real Docker/PG; dependency/image downloads; merged deployment; F-04 closure; B5-4-2 advancement.  
**Final: F-04 OPEN / NO-GO. B5-4-2 BLOCKED.**
