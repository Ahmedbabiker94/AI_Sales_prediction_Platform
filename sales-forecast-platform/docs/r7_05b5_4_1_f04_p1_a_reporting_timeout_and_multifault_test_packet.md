# Phase 7 / R7-05B5-4-1 / F-04 — P1-A Candidate-Only Timeout, Daemon Uncertainty and Multi-Fault Negative Tests

**Date:** 2026-11-18.  
**Authority:** The owner accepted the controlled-rebaseline design **FOR PLANNING ONLY**, then requested to continue the next step. This packet covers only **P1 candidate-only offline negative testing**, not any protected edit authorization.  
**Decision:** **P1-A targeted test additions PASS; P1 overall and protected rebaseline remain PENDING.** F-04 **OPEN / NO-GO**, B5-4-2 **BLOCKED**. No source has been promoted, merged, deployed or executed against Docker/PostgreSQL.

## 1. Exact code and test identities

Repository `Ahmedbabiker94/AI_Sales_prediction_Platform`; branch `fix/r7-05b5-4-1-bootstrap`.

| Source | Git blob SHA at completed P1-A stage |
|---|---|
| Frozen accepted runner `tests/postgres/run_postgres.py` | `0863e81b733381a80b2f83560d8bbc231513ba41` — **unchanged** |
| Frozen operational B5 wrapper `tests/postgres/run_b5_4_1_postgres.py` | `040d0d30bb719d646eb350b229ccd9a4c5f2bf79` — **unchanged** |
| Review-only runner `tests/postgres/run_postgres_security_patch_candidate.py` | `ccd1efac292d9504a59eeef3eaf8b21889879b59` — **restored to previous Codex-reviewed blob; default deny unchanged** |
| Candidate real B5 reporting stage `tests/postgres/run_b5_4_1_reporting_stage_candidate.py` | `5a82cb0219948c0b895f92b603252a547957bed7` — **unchanged** |
| Expanded P1-A test file `tests/runtime/test_r7_05b5_4_1_f04_r2_04_r4_actual_reporting_stage.py` | `6cabac1709ddc6e22d5d63a183fc0032b67cd1cc` |
| Candidate offline workflow `.github/workflows/r7-b5-4-1-f04-candidate-offline.yml` | `daa477ceba84bd7a6a85dcb4d40fa33fa0452aea` — **unchanged, explicitly includes expanded test file** |
| **Final code-tested commit** | `364add85268a575bbed51b2193a78599c8899867` |

Python code paths here are relative to `sales-forecast-platform/`; the GitHub Actions workflow path is repository-relative.

## 2. Added P1-A cases — 8 parameterized tests

All use the prior R4 fake command, fake signal handlers, synthetic disposable passwords and a **real in-process candidate reporting-stage/controller path**. Not one real subprocess, operating-system signal, Docker daemon request, PostgreSQL connection, host filesystem privilege change, wheel download or image operation is performed. The runner's unconditional `OFFLINE_REVIEW_ONLY` hard stop stays in source.

1. **Timeout + late container appearance:** fake B5 command records its run-scoped container in `creation_attempts` as `DAEMON_UNRESOLVED` before throwing a synthetic **sanitized** timeout. Fake ownership removal fails once, then returns success after simulated late arrival; the cleanup status remains `CLEANUP_UNCONFIRMED`, original cause type is retained in `error_type`, no trusted receipt is admitted.
2. **Timeout + read-only inventory unavailable:** same uncertain mutation; synthetic `ps -aq` and `volume ls` queries fail and are reported `UNAVAILABLE`; cleanup cannot be inferred successful, manifest remains `FAILED / CLEANUP_UNCONFIRMED`.
3–6. **B5 output-write KeyboardInterrupt and SystemExit** for **both stdout and stderr**: synthetic partial data written before throwing the exact original interruption object. Verify original Python exception object identity escapes, `FAILED / EVIDENCE_UNAVAILABLE` in retained and published manifests, owned reporting-container cleanup, owned transient credential removal, SIGTERM restoration, context reset, archive admission denied.
7. **Partial B5 output write + secondary reporting-container cleanup refusal:** original B5 `OSError` is recorded in `error_type`; the public exception is deliberately `CleanupUnconfirmed` because owned cleanup could not be verified. Result stays `FAILED / CLEANUP_UNCONFIRMED / EVIDENCE_UNAVAILABLE`. This is the **pre-existing fail-closed exception precedence**, not silent masking of an operational PASS.
8. **Partial B5 output write + secondary terminal publication error:** when owned cleanup is confirmed, a synthetic failed publisher cannot replace the **exact original primary OSError object**. No manifest filename is admitted, retained evidence is `EVIDENCE_UNAVAILABLE`, resources are reconciled and contexts reset.

The new tests do not assert that all possible public exception contexts or credential-bearing third-party exceptions are sanitized. That remains a **separate potential exception-chain review scope**.

## 3. Test-first observations and explicit rollback of a bad candidate change

**First expanded suite:** [CI `44371736219`](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44371736219), initial test commit `d879f37ce5257fcaa1e735341c2a97bd3342e1a8`; Python 3.11/3.12 **each 358 passed / 4 failed (362 collected)**. Three new cases failed: the existing candidate raises **`CleanupUnconfirmed`** instead of the P1 test's initial expectation that the primary `RuntimeError`/`OSError` must always escape. The fourth is the long-standing, **unchanged frozen-runner** redaction RED.

An attempted **candidate-only** exception-precedence modification at `455d9e74f4ade7a206fa383b4b3c6e7287345691` was intentionally narrow, but [CI `44371967731`](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44371967731) showed **6 failed / 356 passed (362 collected)** per Python: it made FIVE existing fail-closed preflight/credential-cleanup tests RED in addition to the unchanged original. **This attempted correction was rejected and reverted** at commit `a52369f398d9733c6ee21b8abd1dd80254ade5fc`; the candidate blob returned **exactly** to `ccd1efac292d9504a59eeef3eaf8b21889879b59`, with all previous security semantics intact.

The P1 assertions were then corrected to distinguish **two established precedence contracts**, rather than weakening existing tests:
- **Unconfirmed owned cleanup** (including unresolved daemon creation or failed B5 container removal): `CleanupUnconfirmed` has priority, and the retained record still records the primary `error_type`.
- **Confirmed cleanup but failed manifest publication:** the in-flight primary exception object survives the secondary publisher fault, and no PASS evidence is admitted.

**FINAL [CI `44372336273`](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44372336273)**, last code-tested commit `364add85268a575bbed51b2193a78599c8899867`: **Python 3.11 and 3.12 each 361 passed / 1 failed (362 collected)**. All **8 P1-A cases passed** and existing targeted candidate-only security tests remained green. The sole failed test remains original frozen-runner `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]`, not skipped/xfail/weakened. **Full CI conclusion remains FAILURE.**

[Focused offline verification `44372336235`](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44372336235) passed. [Read-only host prerequisites `44372336356`](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44372336356) failed. These reports do not authorize actual integration.

## 4. Remaining P1 scope and risks — NO protected transfer

- **Source-transfer and runtime identity:** only an owner-authorized future correction to frozen runner plus actual B5 wrapper can prove operational `command`, `_LIFECYCLE`, `_TEST_SECRETS` and controller bind to **one** operational module. No protected module was edited or integration performed here; existing fake-callback tests are not operational evidence.
- **Operational entrypoint audit:** systematically enumerate raw manifest readers and consumers, disallow file-only `PASS` acceptance, and test `None`/exceptions as rejection after a future authorized transfer. Existing R3/R4 candidate tests cover bound receipts and substitution only under the **trusted synchronous** model.
- **Exception diagnostics:** a cleanup-unconfirmed error can take precedence over a primary failed stage. The terminal record retains `error_type`, but test coverage does **not** prove all nested `__context__`, `__cause__`, local traces or arbitrary credential-bearing errors are safe. Review this risk before protected authorization and add bounded tests if advised by Codex.
- **Mock-model limits:** late appearance and inventory failure were injected directly into the candidate runner's in-process ledger and fake Docker listing; subprocess timeout sanitization is separately covered by H1/R2-01 AST-in-memory tests. These tests do **not** establish actual Docker daemon behavior, no-pull image provenance, process quiescence, host filesystem crash durability or real PG cleanup.
- **Cross-system/archive acceptance:** still **default deny** without a separately designed authenticated/durable completion attestation. Concurrent retained-record mutation and hostile filesystem behavior remain unsupported.
- **Original frozen runner RED and all external gates:** still blocked. Docker29/OCI no-pull pins, 16-wheel v2 hashes/provenance/license/OSV, `_HOST_HEALTH`, and isolated disposable PostgreSQL16.15 require separate approved evidence.

## 5. Required next decision sequence

1. Obtain a **fresh read-only Codex AI P1-A review** of the revised test file, unmodified/reverted candidate Git blob, fault-model assumptions, cleanup/error precedence and synthetic CI. **Do not conflate green tests with a human security audit**.
2. Finish remaining **P1 source-transfer/consumer inventory** as planning/read-only work. Any additional candidate negative tests must remain offline and preserve original protections.
3. Owner personally assesses the pinned plan, findings and remaining risks under **F04-G01**.
4. Only a **new, separate, explicit `AUTHORIZE SECURITY-ONLY CONTROLLED REBASELINE`**, identifying accepted runner and wrapper baseline blobs plus exact allowed security deltas, may initiate P2 protected writes. No generic `Go` substitutes for that approval.

**Decision: P1-A OFFLINE CANDIDATE NEGATIVE TESTS PASS; P1 FULL SCOPE OPEN; PROTECTED WRITE NOT AUTHORIZED; F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**
