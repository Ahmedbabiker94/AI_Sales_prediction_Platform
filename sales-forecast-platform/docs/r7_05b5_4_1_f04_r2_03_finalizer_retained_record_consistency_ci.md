# Phase 7 / R7-05B5-4-1 / F-04 — R2-03: finalizer/retained-record terminal-state consistency

**Date:** 2026-11-18. **Finding:** Codex AI R2-03 (MEDIUM).
**Direction:** The project owner accepted `CHANGES REQUIRED` and authorized sequential **candidate-only** test-first security corrections. This is an AI-reviewed concern, not independent human authorization, nor permission to edit the frozen runner.
**Decision:** **R2-03 TARGETED SYNTHETIC CANDIDATE TEST GATE PASSED**. F-04 **OPEN / NO-GO**; B5-4-2 **BLOCKED**.

## Pinned source identities

Repository `Ahmedbabiker94/AI_Sales_prediction_Platform`, branch `fix/r7-05b5-4-1-bootstrap`.

| Artifact | Git blob SHA |
|---|---|
| Frozen accepted `tests/postgres/run_postgres.py` | `0863e81b733381a80b2f83560d8bbc231513ba41` (**unchanged**) |
| Review-only candidate `tests/postgres/run_postgres_security_patch_candidate.py` | `4f0b81c170427370c343503e9582874ae1014c3a` |
| Pure finalizer `tests/postgres/redaction_finalization_candidate.py` | `86f0b33ea460eec078991ba1f70d9c9c9ba67bae` (**unchanged**) |
| Four new tests `tests/runtime/test_r7_05b5_4_1_f04_r2_03_rejected_finalizer_retained_record.py` | `983f3816f03ee8a87c86ba2bba0bafdd6e2e5d65` |
| Candidate-only workflow `.github/workflows/r7-b5-4-1-f04-candidate-offline.yml` | `bf4f34c91a04455b3f6e103b1b457d257e766254` |
| **Last code-tested source commit** | `842a690f0d1bdbfda3097716f55cd31a3c11b6a8` |

Paths above except for workflow are relative to `sales-forecast-platform/`. The workflow is at the repository root.

## Why it mattered

In the uncorrected candidate, a mock **fully successful** main body, resource reconciliation, owned credential deletion and SIGTERM restoration could cause `record['result']='PASS'` before manifest finalization. A synthetic disposable secret injected into unexpected metadata caused the **pure**, correctly redacting finalizer to produce a `FAILED / CLEANUP_UNCONFIRMED` JSON manifest and a `CleanupUnconfirmed` exception. But the original dict retained by `_host_stage` was not reclassified and could still be **PASS**, with an unredacted secret in a nested value/key.

The pure finalizer is intentionally non-mutating. The owner of the lifecycle record must explicitly synchronize its authoritative terminal classification with the **same exact sanitized bytes** about to be published.

## RED first

Added and explicitly included the new test file in the candidate-only CI pytest command, with four scenarios using a fully mocked successful main body:

1. Unexpected disposable secret inside a nested metadata **value**.
2. Unexpected disposable secret in a nested metadata **key**.
3. Preexisting `secret_redactions=2` despite no new secret in unexpected metadata.
4. Successful non-rejected control case: retained record and manifest must match, including explicit `secret_redactions=0`.

The tests capture the **original** record reference through `_host_stage`, generate secrets only from test-only `_TEST_SECRETS`, verify manifest rejection, preserved failed classification, matching redaction counts, absence of generated secrets in both manifest and retained dict, exact dict equality, prior SIGTERM restoration, owned credential deletion and ContextVar reset. The control asserts a matching PASS when no rejection occurs.

**Before fix**: [GitHub candidate CI run 44286648069](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44286648069), test/workflow commit `ddc04607c166dba359553d9e8b47c6a9ee8716f4`, Python 3.11 and Python 3.12 **each 307 passed / 5 failed (312 collected)**. Four new R2-03 tests were RED; the fifth was the existing unchanged frozen runner's `unexpected_manifest_secret` RED.

The three rejection cases failed because retained record stayed `PASS`; the ordinary success control revealed that the pure finalizer published `secret_redactions=0` but the original retained dict had no such key. These failures are not Docker/PG integration evidence.

## Narrow correction in review-only candidate

After `finalize_manifest_candidate(record, tuple(passwords.values()))` returns `(manifest_text, rejected)`, but **before** attempting `publish_terminal_manifest_candidate`, the candidate:

```python
finalized_record = json.loads(manifest_text)
record.clear()
record.update(finalized_record)
```

This keeps the **original dict identity** while replacing its contents with exactly the validated, redacted final snapshot that will be submitted to the manifest publisher. It restores consistency for nested metadata keys as well as values; merely updating three status fields would leave plaintext credentials or obsolete key names in the retained dict. The finalizer remains pure and unchanged. Failed publication still demotes in-memory `result='FAILED'` and `evidence_status='EVIDENCE_UNAVAILABLE'`, subject to the **separate unresolved R2-04 ambiguous commit/rollback issue**.

The candidate's unconditional `OFFLINE_REVIEW_ONLY` default-deny command guard and missing CLI entrypoint are unchanged. **No protected-runner edits, real Docker or PostgreSQL commands, real signal installations or subprocesses, or external package/image operations occurred.**

## GREEN results and remaining failures

**After fix**: [GitHub candidate CI run 44286857506](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44286857506), code-tested commit `842a690f0d1bdbfda3097716f55cd31a3c11b6a8`, Python **3.11 and 3.12 each 311 passed / 1 failed (312 collected)**. All **four new R2-03 cases passed** and all prior targeted candidate-only cases continued to pass.

The sole failed test remains the **original frozen accepted-runner** `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]` (observed synthetic `RuntimeError` rather than required `CleanupUnconfirmed`). It was not weakened, skipped, or xfailed, so the **whole candidate matrix conclusion is still `failure`, NOT full acceptance**.

[Focused offline run 44286857433](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44286857433) succeeded. [Read-only host prerequisites run 44286857438](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44286857438) failed; OCI/Docker29 no-pull, complete 16-wheel v2 provenance/OSV/license, `_HOST_HEALTH`, and separately authorized disposable PostgreSQL16.15 remain blocked.

## Residual risks and next gate

- **R2-04 remains open:** a hard link can become visible, publication may raise and rollback may fail; a complete accepted-looking manifest can then coexist with a caller's FAILED/EVIDENCE_UNAVAILABLE. This correction does **not** fix ambiguous publication or crash durability.
- This test demonstrates ordinary in-process state agreement after `finalize_manifest_candidate`; it does not prove race-free observations across arbitrary asynchronous host callbacks, SIGKILL, malicious filesystems, or unexpected serializer failures before a finalized snapshot exists.
- Raw secrets may exist transiently in Python locals/frames while finalization runs; the new tests assert the **retained** record reference and accepted manifest don't contain them after normal rejection, not memory zeroization.
- No owner risk-acceptance or protected `run_postgres.py` authorization has been given by this step, and an AI review is **not** independent human review under F04-G01.

**Next in strict sequence:** **R2-04**, first a combined fault **RED** proving atomic publication creates a destination and subsequent rollback/verification fails; define and test a single authoritative accepted-evidence contract, then candidate-only fix and Python3.11/3.12 CI. Afterwards obtain a **fresh Codex AI review of the updated exact candidate Git blob**, and the owner's documented personal security assessment/authorization decision.

**Final: R2-03 SYNTHETIC TARGETED GATE PASSED; F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**
