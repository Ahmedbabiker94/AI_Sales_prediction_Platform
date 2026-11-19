# Phase 7 / R7-05B5-4-1 / F-04 — R3 Owner review and operational integration scope

**Date:** 2026-11-18  
**Purpose:** Record the latest Codex **AI** technical finding and propose a narrowly constrained, sequential **candidate-only integration validation** phase for owner consideration.  
**Status:** **REVIEW PROPOSAL ONLY — NO OWNER APPROVAL OR CONTROLLED REBASELINE AUTHORIZATION**. F-04 **OPEN / NO-GO**, B5-4-2 **BLOCKED**.

## 1. Independent review status and owner's decision authority

Under the owner's previously approved personal-project **F04-G01** governance amendment, a separate human security reviewer is **not mandatory**. The owner personally evaluates the Codex AI technical report and makes documented decisions. Codex's report is **not** independent human review or owner signoff.

**2026-11-18 Codex R3 review verdict, as supplied by owner:** `TECHNICALLY ACCEPTABLE FOR OWNER REVIEW` **only for the trusted, synchronous, in-process Admission Controller design**. R2-01, R2-02 and R2-03 remain **PASS scoped**. R2-04's controller-bound UUID, finalized-record and exact-byte digest checks are judged adequate for that bounded model. **Operational integration remains a blocking requirement**; no new blocker was identified within the stated model.

The owner has **not yet issued a personal risk-assessment decision for this R3 source** and has **not** granted any protected runner/wrapper rebaseline authorization. This document is prepared for that future decision, and does not presume assent.

## 2. Exact source identity and observed testing

Repository: `Ahmedbabiker94/AI_Sales_prediction_Platform`  
Branch: `fix/r7-05b5-4-1-bootstrap`  
Codex-reviewed & code-tested commit: `6253fb5603f647f9b34eeacfa7afc8788eb96234`

| Item | Git blob SHA | Status |
|---|---|---|
| Frozen protected `tests/postgres/run_postgres.py` | `0863e81b733381a80b2f83560d8bbc231513ba41` | Protected; unchanged |
| `tests/postgres/run_postgres_security_patch_candidate.py` | `ccd1efac292d9504a59eeef3eaf8b21889879b59` | Review-only; unconditional `OFFLINE_REVIEW_ONLY` hard stop; no CLI |
| `tests/postgres/redaction_finalization_candidate.py` | `86f0b33ea460eec078991ba1f70d9c9c9ba67bae` | Pure finalization design |
| `tests/postgres/run_manifest_admission_controller_candidate.py` | `f8ffa51dbf072deaad0ab7e3701965a05b471548` | Trusted synchronous candidate admission; file-only archives rejected |
| `tests/postgres/run_b5_4_1_postgres_admission_candidate.py` | `618f22ee7655e6d21806960b80fa6a7d9dc3bba5` | Proposed callback-based consumer only, **not** actual reporting stage |
| `tests/runtime/test_r7_05b5_4_1_f04_r2_04_controller_binding.py` | `9a3949e5a2fc395a310a9b1caa71bd70fcdaeb97` | 17 candidate-only tests at reviewed commit |
| Operational `tests/postgres/run_b5_4_1_postgres.py` | `040d0d30bb719d646eb350b229ccd9a4c5f2bf79` | **Still imports frozen runner**; not integrated |

GitHub Actions [run 44292768783](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44292768783): Python 3.11 and 3.12 each **335 passed / 1 failed (336 collected)**. The one failure is the **unchanged protected-runner** `unexpected_manifest_secret` regression (needs `CleanupUnconfirmed`, sees synthetic `RuntimeError`). It must **not** be skipped/xfail/modified. The overall matrix is **RED**, not accepted as a green release. Focused offline succeeded; read-only host prerequisites failed. No actual Docker/PG integration has been demonstrated.

## 3. Essential integration dependency confirmed by source

The actual operational wrapper `tests/postgres/run_b5_4_1_postgres.py`:

- At **lines 12–15**, statically imports `ROOT, PROXIES, POSTGRES, BASE, command, run, write_evidence, redact_test_secrets` from the existing **protected frozen `run_postgres`** module.
- At **lines 55–58**, imports `_LIFECYCLE` from **the same frozen module** and requires the current run's `run_hex` to match `run_id.hex`. It registers the additional `b5-4-1` container name with that lifecycle at **lines 59–64**, before command execution, for cleanup/reconciliation ownership.
- At **lines 78–90**, its `main()` calls frozen `run()` directly; no trusted Admission Controller decides evidence acceptance.
- The proposed candidate wrapper **only delegates a passed-in stub callback to the candidate Admission Controller**. Its existing synthetic tests do not exercise the actual reporting-stage `command`, `_LIFECYCLE`, `write_evidence`, cleanup, argv, network-isolation or module ownership behavior.

Therefore a runner-only transfer or replacing the wrapper's call while leaving `reporting_stage` bound to frozen helpers can create **split lifecycle ownership**, remove cleanup guarantees, or invoke an incompatible `command()`. A future operational change must treat **runner, controller, reporting stage and its imported helpers as one module-context integration**. The review fork must remain non-executable; its command guard must not be casually removed to make integration tests work.

## 4. Proposed strictly sequential pre-authorization work (candidate-only)

**Next stage R2-04-R4: actual reporting-stage integration prototype and negative tests** — *still offline, no protected edits*. This stage is proposed, **not yet approved as owner signoff or rebaseline**.

1. **First write failing tests** for an adapter that exercises the exact B5-4-1 reporting-stage argument/test selectors under a fake command with a **single shared candidate `_LIFECYCLE` ContextVar**. Do not accept stub callbacks alone. Record exact source parity against `run_b5_4_1_postgres.py` `040d0d30...`.
2. Implement the **smallest candidate-only reporting-stage adapter** that explicitly binds `command`, `_LIFECYCLE`, `write_evidence` and `redact_test_secrets` to **one and the same candidate runtime module**, preserving fixed `TESTS`, safe image/network/volume arguments, and the `extra_containers` registration-before-daemon-invocation rule. Avoid broad restructuring, circular imports, and per-source business logic.
3. Test success and resource cleanup; failed/interrupted container registration, failed command, partial stdout/stderr evidence, retained lifecycle ownership and no PASS admission. Assert accepted consumers use controller-returned `manifest_bytes`, not file-only PASS; record every planned admission entrypoint.
4. Add Codex-requested candidate-only adversarial tests: **same-length byte substitution** (to isolate digest checks from length), symlink/nonregular/oversized/unreadable files and missing `O_NOFOLLOW`, duplicate and wrong-evidence-path callbacks, mutated record across finalization/receipt boundary, and **post-admission file replacement/deletion** while verified `manifest_bytes` remain immutable.
5. Run Python 3.11 + 3.12 candidate offline CI, preserving original RED, and publish exact code/test/workflow Git blob SHAs plus direct logs; obtain **fresh read-only Codex review** of the candidate integration **and** of all transfer-required deltas.

## 5. Proposed future owner-controlled rebaseline scope — NOT AUTHORIZED HERE

Only after the R4 review and explicit owner assessment should a **separate and narrowly worded authorization** be considered for the exact reviewed identities and deltas. That later scope must identify:

- **Protected runner** `tests/postgres/run_postgres.py`: apply only independently reviewed security corrections, including secret finalizer and original RED behavior; retain pinned OCI identities, source validation, command restrictions, ownership-scoped cleanup, selectors, and no-production-data boundaries. Do **not** copy the `OFFLINE_REVIEW_ONLY` fork blindly as the operational runner.
- **Operational B5-4-1 wrapper** `tests/postgres/run_b5_4_1_postgres.py`: integrate the trusted controller for the **actual** reporting stage, using the same corrected runner/lifecycle/module context and only its verified immutable admitted evidence bytes. Ensure no alternative file-only admission path or unreviewed re-entry.
- **Controller operationalization**: create or adapt the necessary module with imports **bound to the corrected, authorized accepted runner**, not the fork's unexecutable `command()`; enforce exactly one trusted completion signal. Preserve archive/restart **default deny** until a separately designed and owner-approved durable attestation becomes necessary.
- **Tests/workflows/docs**: preserve all original negative tests, require original frozen runner `unexpected_manifest_secret` to go **GREEN without test weakening**, verify same-invocation digest identity and genuine reporting-stage integration, and produce a new exact source/CI/test-evidence packet.

**Rebaseline authorization must explicitly state the pinned baseline runner SHA, reviewed candidate SHA(s), proposed wrapper and controller paths, approved changes and negative tests, exclusions, residual risks, and owner's personal signoff.** This F04-G01 process replaces the earlier mandatory independent human reviewer with Codex AI advice plus **owner self-assessment**, not an assertion of independent human approval.

## 6. Unchanged operational gates

Even after any future security-only protected rebaseline, **F-04 and B5-4-2 cannot be declared ready** until their separate prerequisites are met: original accepted-runner RED becomes green, proper Python CI, Docker29 host compatibility, pinned OCI image availability without pull, sixteen-wheel candidate-v2 bytes/provenance/license/OSV review, `_HOST_HEALTH`, and separately authorized isolated disposable PostgreSQL 16.15 tests. Network, filesystem durability, asynchronous signals, memory/traceback secrets, malicious host, and replay/archive remains appropriately limited or separately assessed.

**Current decision record: Codex R3 `TECHNICALLY ACCEPTABLE FOR OWNER REVIEW` (scoped). Owner R3 security decision: PENDING. R2-04-R4 candidate integration proposal: READY FOR OWNER REVIEW, NOT AN AUTHORIZATION. Frozen transfer: NOT AUTHORIZED. F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**
