# Phase 7 / R7-05B5-4-1 / F-04 — R1 Consolidated Independent Security Review Packet

**Prepared:** 2026-11-18 (UTC). **Verdict:** **HANDOFF READY FOR INDEPENDENT REVIEW; NOT APPROVED.**
**Author classification:** assistant-assembled source/evidence index, **not** a human independent security decision.
**Workflow status:** F-04 **OPEN / NO-GO**; B5-4-2 **BLOCKED**.

## 0. Decision in plain language

The proposed candidate repairs six groups of security failure modes in a **review-only**, deliberately non-operational Python runner. Those changes have encouraging mocked regression evidence but have NOT been independently signed off, approved for transfer into the protected runner, or exercised in an authorized disposable PostgreSQL integration. The accepted runner remains **frozen and vulnerable to the original RED regression**. Do not report the milestone complete.

The reviewer’s task is to assess whether the candidate’s security design and its exception/cleanup precedence are suitable for a *separately owner-authorized*, security-only controlled rebaseline. It is **not** a request to run the candidate against Docker or merge it.

## 1. Immutable snapshot and provenance

**Review snapshot:** repository `Ahmedbabiker94/AI_Sales_prediction_Platform`, candidate branch `fix/r7-05b5-4-1-bootstrap`, source/evidence commit `67d0284992ed425f69571792fb09cda61d6c0edd` (the last code-tested head, prior to this documentation-only handoff commit).

| Artifact | Git blob SHA | Restriction |
|---|---|---|
| [Frozen accepted runner](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/sales-forecast-platform/tests/postgres/run_postgres.py) | `0863e81b733381a80b2f83560d8bbc231513ba41` | **DO NOT EDIT** without distinct owner authorization |
| [Review-only candidate fork](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/sales-forecast-platform/tests/postgres/run_postgres_security_patch_candidate.py) | `dafd1822d58700663eaef378824c5c0e87d99428` | `OFFLINE_REVIEW_ONLY` unconditional command denial, no CLI |
| [Pure manifest redaction helper](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/sales-forecast-platform/tests/postgres/redaction_finalization_candidate.py) | `86f0b33ea460eec078991ba1f70d9c9c9ba67bae` | proposal, not imported by accepted runner |
| [Candidate offline CI workflow](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/.github/workflows/r7-b5-4-1-f04-candidate-offline.yml) | `293db3fc7b26bb8f9785e8b006d251a1e6831d65` | synthetic CI, not production acceptance |
| [Original independent reviewer handoff](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/sales-forecast-platform/docs/r7_05b5_4_1_f04_independent_security_reviewer_handoff.md) | `e4d8f6f322722545173af898e2bc15b61df0f6d8` | **superseded snapshot** (earlier candidate SHA), still useful checklist |

**Verification:** the frozen runner Git blob remains identical to `0863e81b733381a80b2f83560d8bbc231513ba41`. No protected-runner patch, rebaseline, real Docker/PG command, merge, or deployment is represented by this packet.

## 2. Six review tracks and test evidence

Each track describes **passing candidate-only mock regressions**, not a PASS on the frozen accepted runner or human approval.

| Track | Proposed correction and what mock tests probed | Mock tests | Case count added | Candidate CI at that track (Python 3.11 and 3.12 each) |
|---|---|---|---:|---|
| **H1 / F04-A** | Sanitize `TimeoutExpired` public exception/traceback attributes without leaking credential-bearing subprocess argv; review bounded reap and outstanding mutation state | [H1 tests](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/sales-forecast-platform/tests/runtime/test_r7_05b5_4_1_f04_h1_timeout_sanitization.py) `5bafc9ec60a66b8d3713d7df45a758bcb0a33f3c` | 5 | [44243018205](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44243018205): 256 passed / 1 failed |
| **H2 / F04-C,D** | Defer *installed Python SIGTERM callback* during one owned credential unlink, including flag→unlink seam and fallback; no duplicate attempt | [H2 tests](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/sales-forecast-platform/tests/runtime/test_r7_05b5_4_1_f04_h2_sigterm_unlink_seam.py) `c50284adc08c0390436ab3358128e6f0135dcc09` | 6 | [44244453322](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44244453322): 262 / 1 |
| **M1 / F04-B,C** | Attempt handler restoration once *before publishing a terminal PASS manifest*; restoration failure rejects success, primary fault precedence retained | [M1 tests](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/sales-forecast-platform/tests/runtime/test_r7_05b5_4_1_f04_m1_signal_manifest_gate.py) `eae221dbc6ecdf1062a09b4be5f6c2c37431ceae` | 3 | [44245540104](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44245540104): 265 / 1 |
| **M2 / F04-D** | No transient in-memory PASS between completed main body and resource reconciliation; interruption during container/volume/sleep stays FAILED | [M2 tests](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/sales-forecast-platform/tests/runtime/test_r7_05b5_4_1_f04_m2_reconciliation_interrupt.py) `3b4536c22f2ce46e2ec78e0b09a994e4761178aa` | 3 | [44255068587](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44255068587): 268 / 1 |
| **T1 / F04-E** | Exact `type(status) is str` before comparison; `REMOVED` and `NOT_PRESENT` affirmative, forged equality/other types normalized to `UNCONFIRMED` | [T1 tests](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/sales-forecast-platform/tests/runtime/test_r7_05b5_4_1_f04_t1_strict_unlink_result_type.py) `172fe3cfa2f77975ad5a816a06ef76f80b85ac0e` | 18 | [44259001678](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44259001678): 286 / 1 |
| **T2 / F04-B / Evidence** | `EVIDENCE_UNAVAILABLE` on serialization/write/publish error; stage terminal bytes before exclusive hard-link publication, refuse overwrite, retain primary error | [T2 tests](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/sales-forecast-platform/tests/runtime/test_r7_05b5_4_1_f04_t2_partial_evidence_write.py) `e439a9230cfae43ed31dfd64f84aaaf1ab1ffb12` | 9 | [44262801885](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44262801885): 295 / 1 |

Track reports (historical snapshots, not independent assessments):
[H1](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/sales-forecast-platform/docs/r7_05b5_4_1_f04_h1_timeout_sanitization_ci.md) ·
[H2](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/sales-forecast-platform/docs/r7_05b5_4_1_f04_h2_sigterm_unlink_seam_ci.md) ·
[M1](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/sales-forecast-platform/docs/r7_05b5_4_1_f04_m1_signal_restore_manifest_ci.md) ·
[M2](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/sales-forecast-platform/docs/r7_05b5_4_1_f04_m2_reconciliation_interrupt_ci.md) ·
[T1](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/sales-forecast-platform/docs/r7_05b5_4_1_f04_t1_strict_unlink_status_ci.md) ·
[T2](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/sales-forecast-platform/docs/r7_05b5_4_1_f04_t2_partial_evidence_publication_ci.md).

**Latest code-tested head:** `67d0284992ed425f69571792fb09cda61d6c0edd`. [Candidate CI run 44262801885](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44262801885) on both CPython 3.11/3.12: **295 passed, 1 failed, 296 collected**; the **sole failed regression is the original frozen accepted-runner `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]`**. It raises synthetic preflight `RuntimeError` rather than the required `CleanupUnconfirmed`. It was not weakened, skipped, or xfailed. Some passing counterexample tests expose original vulnerabilities and **must not be read as verified mitigation**. This matrix is not green.

The [focused offline run 44262801941](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44262801941) succeeded on both Python versions. The [read-only host prerequisite run 44262801818](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44262801818) failed: GitHub-hosted environment showed **Docker 28.0.4**, both exact pinned OCI image IDs **absent locally**, and a verified offline wheelhouse **not provisioned**. No Docker image pull/run or PostgreSQL integration was authorized/performed by these jobs.

## 3. Reviewer-guided code inspection sequence

Use these immutable source links and evaluate each branch under normal exception, `KeyboardInterrupt`, and second-fault conditions:

1. **Command and secret safety:** [candidate command hard stop / retained operational code](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/sales-forecast-platform/tests/postgres/run_postgres_security_patch_candidate.py#L106-L210). Assess sanitization of every public exception chain and attributes; no secret-bearing `TimeoutExpired.cmd` in observable tracebacks; preserve mutation/unreaped-process accounting. Candidate guards MUST NOT be confused with operational behavior.
2. **Context, setup, signal restoration:** [run and restoration guard](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/sales-forecast-platform/tests/postgres/run_postgres_security_patch_candidate.py#L276-L345), [terminal and outer finally](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/sales-forecast-platform/tests/postgres/run_postgres_security_patch_candidate.py#L500-L604). Confirm exactly-once restoration, possible install/restore failure, two ContextVar token resets, primary versus secondary error precedence and residual signal exposure.
3. **Resource state reconciliation:** [main-body and cleanup](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/sales-forecast-platform/tests/postgres/run_postgres_security_patch_candidate.py#L430-L556). Check that no lifecycle PASS survives an exceptional cleanup, unresolved daemon mutation cannot pass, bounded owned-only deletion occurs, and read-only inventory queries retain the accepted scope/timeout semantics.
4. **Transient credential file:** check both normal and fallback unlink; strict built-in string type; malformed/symlink/exception results rejected; exactly one owned attempt. Treat filesystem TOCTOU races and secure disk erasure as **outside current proof**.
5. **Redaction/evidence:** [pure redaction helper](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/sales-forecast-platform/tests/postgres/redaction_finalization_candidate.py) and [manifest staging + hard-link protocol](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/67d0284992ed425f69571792fb09cda61d6c0edd/sales-forecast-platform/tests/postgres/run_postgres_security_patch_candidate.py#L57-L105). Inspect unexpected secret in nested/key fields, redaction counters, serializer faults, partial writes, staging leftovers, existing destination collision and failure after link. Review absence of `fsync` for power-loss durability and failure handling under non-cooperative process termination.
6. **Source parity:** compare frozen accepted runner vs candidate; clearly separate security-only logic from review-only hard stop and no CLI. Verify no modifications to PG test selectors, OCI pins, wheel hashes, container isolation flags, local source mounts, production network/data or scope ownership.

## 4. Open blocking conditions — review must not waive them

- **Original RED in frozen runner** remains outstanding. After explicit approval of a narrow protected edit, the **unchanged original regression must become GREEN on the corrected accepted runner**. Do not silently suppress it, or claim a candidate-only test repaired the protected file.
- **No independent human reviewer has signed or been assigned** in [F-04 Issue #2](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/issues/2) as of this snapshot. Assistant/Codex analysis, code tests, and this packet are **not** that review.
- **No explicit owner-scoped security-only controlled rebaseline authorization** referencing the exact baseline/proposed SHA and allowed delta. A generic “go” to continue reviews does not grant it.
- **Runtime and supply chain gates remain blocked:** indexed OCI/Docker29 no-pull validation; 16-wheel v2 exact bytes+lock, OSV/license/provenance; intermittent `_HOST_HEALTH`; and separately authorized disposable PG16.15 with owned cleanup. The current CI host had Docker28, uncached exact images and no supplied wheelhouse.
- **Engineering risk limits:** synthetic callback tests are not OS signal scheduling; no SIGKILL/process-crash recovery guarantee; hard-link publication not `fsync`-durable; symlink/TOCTOU and malicious host risks remain; bounded reconciliation cannot prove an ambiguous Docker daemon operation will not finish later. Independently assess `BaseException` error precedence, nonterminal partial output, and stale evidence cases.
- **No code execution requested by this packet.** No Docker/PG, downloads, merge, deployment, or milestone promotion.

## 5. Required signed reviewer response

Independent reviewer should comment on [Issue #2](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/issues/2) using this template:

> **Reviewer GitHub handle and independence from author:**
>
> **UTC review date:**
>
> **Exact baseline SHA:** `0863e81b733381a80b2f83560d8bbc231513ba41`
>
> **Exact candidate SHA:** `dafd1822d58700663eaef378824c5c0e87d99428`
>
> **Reviewed code/test commit:** `67d0284992ed425f69571792fb09cda61d6c0edd`
>
> **Decision:** REQUEST_CHANGES / BLOCK / APPROVE_SCOPE_FOR_OWNER_REVIEW (not execution approval)
>
> **Findings with line references:** H1, H2, M1, M2, T1, T2, original RED and cross-cutting exception/signal/file/daemon cases
>
> **Must-fix defects and tests requested (especially against corrected accepted runner):**
>
> **Residual risks explicitly accepted/rejected:**
>
> **Exact proposed protected-runner security-only delta (if acceptable):**
>
> **What is NOT authorized:** Docker/PG, OCI/wheel changes, rebaseline without owner approval, merge, deployment, Phase 7 closure, B5-4-2.

If the reviewer finds a defect, keep the correction **candidate-only** and repeat targeted RED→GREEN before asking for a fresh review. Even an APPROVE_SCOPE_FOR_OWNER_REVIEW response cannot authorize touching the accepted runner.

**R1 packet is a documentation handoff, not a new security test or independent review. F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**
