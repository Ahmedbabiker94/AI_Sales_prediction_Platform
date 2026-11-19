# Phase 7 / R7-05B5-4-1 / F-04 — R2-04 R3: bound synchronous admission controller

**2026-11-18. Decision for this correction step:** **candidate-only controller and consumer-proposal synthetic test gate PASS; submit for fresh Codex AI review.** This is **NOT technical signoff**, owner self-review, permission to modify the frozen runner, or operational admission coverage. **F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**

**Reviewer prompting this stage:** Owner-supplied Codex AI second assessment `CHANGES REQUIRED`, with **R2-01/R2-02/R2-03 PASS (scoped)** and **R2-04 NOT ADEQUATELY RESOLVED**. Reviewer found that the prior `terminal_manifest_accepted_candidate(..., run_completed=True)` did not own the success observation, verify invocation identity/digest, or connect to a consumer. New files address that **candidate-only design gap** without editing operational sources.

## Exact reviewed / tested identities

Repository `Ahmedbabiker94/AI_Sales_prediction_Platform`, branch `fix/r7-05b5-4-1-bootstrap`.

| Artifact | Git blob |
|---|---|
| Frozen accepted runner `tests/postgres/run_postgres.py` | `0863e81b733381a80b2f83560d8bbc231513ba41` — **unchanged** |
| Review-only security candidate `tests/postgres/run_postgres_security_patch_candidate.py` | `ccd1efac292d9504a59eeef3eaf8b21889879b59` — **unchanged from second Codex review** |
| **New controller** `tests/postgres/run_manifest_admission_controller_candidate.py` | `f8ffa51dbf072deaad0ab7e3701965a05b471548` |
| **New proposed reporting consumer** `tests/postgres/run_b5_4_1_postgres_admission_candidate.py` | `618f22ee7655e6d21806960b80fa6a7d9dc3bba5` |
| **New R3 tests** `tests/runtime/test_r7_05b5_4_1_f04_r2_04_controller_binding.py` | `9a3949e5a2fc395a310a9b1caa71bd70fcdaeb97` |
| Candidate CI workflow `.github/workflows/r7-b5-4-1-f04-candidate-offline.yml` | `81a68f8cc98970d7f934e4aefbb3bc0e4df482e7` |
| Existing **operational** B5-4-1 wrapper `tests/postgres/run_b5_4_1_postgres.py` | `040d0d30bb719d646eb350b229ccd9a4c5f2bf79` — **unchanged, still imports the frozen runner** |
| **Last R3 code-tested commit** | `6253fb5603f647f9b34eeacfa7afc8788eb96234` |

Paths in rows above except workflow are relative to `sales-forecast-platform/`.

## Design contract and boundary

**Trusted, synchronous path:** Proposed `invoke_candidate_and_admit(allow, wheelhouse, evidence, _host_stage=trusted_stage)` owns the specific call to candidate `run(..., _host_stage=controller_callback)` and **does not accept any caller-supplied `run_completed=True`**.

The internal controller callback captures the fresh `uuid.UUID`, the **same mutable lifecycle record** finalized by R2-03, and its owned `test-client.env` path. It refuses missing/duplicate callbacks and wrong evidence scope. If `run()` raises **any** exception or control-flow interruption, no admitted result is created and the exception propagates. If `run()` returns normally, the controller requires the retained record's `run_id`, `run_hex`, PASS/AVAILABLE, cleanup/credential/handler restoration, zero redaction, non-production flags and source identity to be valid.

It then canonicalizes the **in-memory finalized redacted record** with `json.dumps(indent=2, sort_keys=True, allow_nan=False)` and hashes the expected UTF-8 bytes with SHA-256. It reads the terminal manifest **once**, using an OS `O_NOFOLLOW` read-only descriptor, `fstat` regular-file check and a 1 MiB cap; accepts only an **exact byte match** and digest match. Accepted consumers receive an immutable `VerifiedCandidateManifest(run_id, sha256, manifest_bytes, source_commit)` snapshot, rather than a filename to reopen. The native manifest remains unchanged on disk.

**Candidate-only B5-4-1 reporting consumer proposal:** `run_reporting_stage_with_bound_admission_candidate()` delegates the trusted reporting callback to that controller; its tests verify a successful reporting callback obtains a bound receipt and a failing reporting callback raises without creating an accepted result. This is a **proposed** migration path; it does **not** select modules from untrusted callers, import a frozen runner or expose a live CLI.

**Archive/import/restart:** `admit_archive_without_attestation_candidate()` is **default deny**. No persistent authenticated completion receipt, replay control, crash-recovery or durability protocol is introduced. An archived raw PASS file is never accepted under this candidate-only path. Reopening the terminal filename after an in-memory acceptance is also **not** an authorized consumption pattern; use the immutable snapshot returned by the controller.

**Trust assumptions:** The controller process and host-stage callback are trusted. An arbitrary external caller able to forge Python objects or replace the running code is outside this proof. SHA-256 here binds bytes and invocation identity within a trusted synchronous controller; **it is not a cryptographic signature or externally authenticated attestation**. The `O_NOFOLLOW` descriptor check does not protect against malicious ancestor replacement or hostile OS/filesystem. This does not prove `fsync` durability, cross-process consumer coverage or Docker daemon quiescence.

## Test-first requirement and results

Before implementing the controller, the 12 initially added new tests were in CI and failed with **`ModuleNotFoundError` for the absent controller module** (an implementation-presence RED, **not** a new empirical reproduction of the filesystem vulnerability). In the same stage, the previous R2-04 combined-fault tests already demonstrated that `PASS` files may survive failed rollback.

- [Initial missing-controller CI **44292013356**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44292013356), workflow commit `8dd4e9b1f71811a7ffd58df1347cd8ec7845fd68`: Python 3.11 **318 passed, 1 failed, 12 errors**; missing controller explains the errors. The unchanged accepted-runner redaction test explains the failure.
- [First implementation CI **44292221052**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44292221052), commit `9e0080765b36a04c911a0e9319fccf663f46e088`: Python 3.11/3.12 **330 passed / 1 failed (331 collected)**.
- [**Final CI 44292768783**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44292768783), code-tested commit `6253fb5603f647f9b34eeacfa7afc8788eb96234`: **Python 3.11 and 3.12 each 335 passed / 1 failed (336 collected)**. **All 17 new R3 controller/consumer tests PASS**, with prior R2-01/02/03/04 and H1/H2/M1/M2/T1/T2 synthetic tests still green. The sole failure is the unchanged protected-runner `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]` (observed `RuntimeError` rather than required `CleanupUnconfirmed`). The entire workflow remains **failure**, never label full CI GREEN.
- [Final focused offline verification **44292768722**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44292768722) **success**. [Read-only host prerequisite **44292768755**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44292768755) **failure**. No live Docker/PG integration was permitted or performed.

17 new tests cover: successful same-invocation ID+digest+bytes; ambiguous hard-link commit followed by failed unlink or inode verification (no completion receipt); cross-run substitution with B successful; cross-run substitution with **B failed while a PASS link survives** and A succeeds; same-run modified bytes and altered valid JSON; host-stage forged run ID; RuntimeError, TimeoutError, KeyboardInterrupt and SystemExit; default-deny archive/restart; successful/failed proposed reporting consumer; normal return without observed stage; unchanged operational-wrapper source. All use fake Docker command hooks and mock signal handlers; no real PostgreSQL, Docker, host signals, subprocesses, downloads or credentials.

## Required fresh Codex AI R3 review and owner gate

Ask Codex to review this exact source/CI and challenge:
1. Is obtaining the completion observation exclusively by returning from `_candidate_run`, with a controller-owned callback holding the same finalized record, sufficient under the stated **trusted synchronous** model?
2. Does the expected canonical digest truly bind the finalizer-published bytes, run ID and evidence path? Any unexpected serialization, mutation or callback exception that bypasses the boundary?
3. Are same-run/failed-B cross-run substitution and ambiguous rollback cases convincingly rejected? Can an operational caller still bypass the controller or directly read raw PASS?
4. Does the proposed B5-4-1 consumer wrapper actually enforce the candidate admission path? Is a later owner-authorized update to the existing operational `run_b5_4_1_postgres.py` essential before declaring F-04 fully closed?
5. Should archive/restart be explicitly **unsupported/fail closed** for this milestone, or do business requirements require a durable completion attestation? No standalone `.complete` file or unauthenticated receipt is accepted.
6. Are there other R2-01/02/03 regressions from the new code, new leak surfaces or unsafe source parity changes?

**Known open gates:** actual operational wrapper still imports protected frozen runner and does not enforce controller admission; current frozen runner still has original RED; Docker29+OCI offline pinned images, sixteen-wheel v2 provenance/OSV/license, `_HOST_HEALTH` and separately authorized disposable PostgreSQL16.15 remain blocked; no runtime integration, fsync power-loss, malicious host or recovery guarantee. Under F04-G01 there is no independent human reviewer requirement for this personal project; the owner must still **personally assess** a fresh AI report and **separately authorize** any narrow protected-runner/wrapper rebaseline if satisfied.

**Current: R2-04 R3 SYNTHETIC CANDIDATE TESTS PASSED; CODE AWAITING FRESH CODEX REVIEW. F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**
