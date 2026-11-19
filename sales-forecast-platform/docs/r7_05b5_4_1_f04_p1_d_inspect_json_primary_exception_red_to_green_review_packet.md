# Phase 7 / R7-05B5-4-1 / F-04 — P1-D Unsafe Primary Docker-Inspect JSONDecodeError

**Recorded:** 2026-11-19  
**Authorization:** User agreed earlier to **planning and P1 candidate-only offline negative testing**. This is NOT separate authorization for editing protected runner/wrapper, execution against Docker/PostgreSQL, OCI or wheel acquisition, merging, rebaselining or deployment.  
**Fresh Codex AI verdict:** **PENDING** for this new P1-D candidate patch. The most recent supplied Codex review was **CHANGES REQUIRED** on P1-C commit `8e22e03701b317b99cc8583cea8e7bc44f2776c6` and candidate `2cc9a34eee3c3a3a3fe5cd2c023fd2fc81965be5`.  
**Global gates:** **F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**

## 1. Exact source identifiers and test/run distinction

Repository: `Ahmedbabiker94/AI_Sales_prediction_Platform`, branch: `fix/r7-05b5-4-1-bootstrap`.

| Artifact | Git blob SHA | Scope |
|---|---|---|
| **Protected frozen runner** `tests/postgres/run_postgres.py` | `0863e81b733381a80b2f83560d8bbc231513ba41` | **UNCHANGED** |
| **Protected B5 operational wrapper** `tests/postgres/run_b5_4_1_postgres.py` | `040d0d30bb719d646eb350b229ccd9a4c5f2bf79` | **UNCHANGED** |
| Review-only patched `tests/postgres/run_postgres_security_patch_candidate.py` | `47d54f967cabb45808d1d7466c48cb6629dbac2c` | P1-D inspected JSON error boundary |
| R4/P1 tests `tests/runtime/test_r7_05b5_4_1_f04_r2_04_r4_actual_reporting_stage.py` | `a09a93cb1f67bbbe1c07036a875bf93d4ac24e33` | Eight P1-D negative scenarios + genuine publisher invalid byte fixture |
| Secondary interruption tests `tests/runtime/test_r7_05b5_4_1_f04_r2_01_secondary_reap_sanitization.py` | `60b97206c494d58ae9560557c8c02d10cee70a00` | Last **test-only** fixture fix: actual invalid UTF-8 byte |
| Candidate test workflow `.github/workflows/r7-b5-4-1-f04-candidate-offline.yml` | `daa477ceba84bd7a6a85dcb4d40fa33fa0452aea` | Explicit original offline test inventory, no Docker/PG steps |

All code/test paths except workflow are relative to `sales-forecast-platform/`.

**IMPORTANT exact CI scope:** The last **verified CI run was on commit `e6d0b12b9708a8446a316899e70e5a8499818316`**, before a final test-only byte literal change at commit **`59b14ae25545ff22f4f9dd9469fee7a1913e54e4`**. The GitHub Actions run collection had **no newer P1-D candidate run** at the time of preparing this packet. Accordingly, the quoted `393 passed / 1 failed` is fully verified for `e6d0b12...`, **NOT falsely claimed for the later `59b14ae...` tree**. A fresh CI run against latest branch/test blob **must be obtained and checked before recommending technical owner acceptance or protected transfer**. The final change only replaces a double-escaped literal `b"\\xff"` with a real invalid-UTF-8 byte literal `b"\xff"` in an existing synthetic fixture; it is not a runtime-source modification.

## 2. Reviewer finding and test-first RED

Codex AI P1-C (read-only) reported a **HIGH** source-derived disclosure: both `json.loads(command(['docker', 'inspect', server]).stdout)[0]` and the foundation/restart client-inspect `json.loads(...)[0]` could raise `json.JSONDecodeError` **before** the `Config.Env` password scrubbing statement ran. The exception's `.doc` holds the full raw Docker response and may include generated disposable passwords. With owned cleanup confirmed the raw exception escaped; with cleanup unconfirmed it was accessible through `CleanupUnconfirmed.__context__`; with a secondary publisher failure it could be preserved as `pending_error`. This is a **source-derived conditional scenario**, not a claim that normal inspect output is malformed.

**Test first:** Added **8 end-to-end mocked negative cases**: 2 real source sites (server inspect and foundation client inspect) × 4 fault conditions:
1. Confirmed resource cleanup, failed-manifest publication succeeds.
2. Confirmed cleanup, unsafe publisher error also occurs.
3. Unconfirmed resource cleanup.
4. Unconfirmed cleanup with unsafe publisher error.

Each injects malformed Docker-inspect JSON **containing an actual runtime-generated test-only password** in simulated `Config.Env`, not a known hardcoded placeholder. Tests verify generated test credentials are present in the *raw synthetic* inspect response yet absent from the *public* `str/repr/args/vars/doc/object/filename/stderr`, recursive `__context__` and `__cause__`, and formatted tracebacks; they also assert retained failure and `error_type`, safe cleanup precedence, credential-file cleanup, no received admission receipt, default-deny archive, expected terminal-manifest status when publication succeeds, SIGTERM restoration and ContextVar resets.

[**P1-D RED CI 44462540432**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44462540432) on test-only commit `a65abc054fccf7b077572826578b3826f7de07fc`: Python 3.11 and 3.12 each **385 passed / 9 failed (394 collected)**: all eight new source-derived scenarios RED; one previously existing protected frozen-runner RED.

## 3. Narrow candidate-only correction

Add **`parse_disposable_inspect_metadata_candidate(raw_stdout)`**, a context-free JSON parsing boundary in the review-only candidate:

```python
invalid_json = False
try:
    parsed = json.loads(raw_stdout)
except json.JSONDecodeError:
    invalid_json = True
if invalid_json:
    raise RuntimeError(
        'disposable PostgreSQL inspection JSON invalid; details suppressed') from None
return parsed[0]
```

Use it **only in the two affected Docker inspect sites**, before any access/scrubbing of `Config.Env`. No raw `JSONDecodeError` object or raw document is stored in the lifecycle record or re-raised. The public constant `RuntimeError` is created **outside** the parser's raw exception handler, so the unsafe `.doc/.args` object does not remain in the tested public exception graph. In unconfirmed cleanup the earlier `CleanupUnconfirmed` priority remains; in confirmed cleanup, the safe primary object is retained even if publishing fails. Existing R2/R4/P1-C admission gates and runner cleanup are untouched.

**Review-only guard remains:** `command()` still **unconditionally raises `OFFLINE_REVIEW_ONLY` as its first executed statement**; there is **no CLI entrypoint** in the candidate. All tests use in-process Python fakes; no real Docker, PostgreSQL, OS signals, subprocess, networking, downloading or real credentials.

## 4. RED → GREEN verification and separate fixture adjustment

- [**GREEN-targeted CI 44462662177**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44462662177), runtime patch commit `d943a0f9ed78e51acbe4f0be0a6a4aea45470276`: Python 3.11 and 3.12 each **393 passed / 1 failed (394 collected)**.
- [**Additional CI 44462825802**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44462825802), commit `e6d0b12b9708a8446a316899e70e5a8499818316` with first Unicode fixture byte correction: Python 3.11 and 3.12 each **393 passed / 1 failed**. [Focused offline 44462825857](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44462825857) succeeded; [read-only host prerequisite 44462825803](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44462825803) failed.
- Last fixture-only commit `59b14ae25545ff22f4f9dd9469fee7a1913e54e4` changes only the synthetic secondary-reaping Unicode byte literal to the correct `b"\xff"`. **No verified GitHub Action run for this exact last commit yet**. Do NOT cite e6 run as verifying this later code snapshot.

**Every verified P1-D new test passes as of e6.** The **sole unchanged failing** test remains the protected runner `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]` (synthetic preflight RuntimeError rather than required CleanupUnconfirmed). The workflow conclusion is **FAILURE — not full green**; no original test was skipped, xfailed, deleted or weakened.

## 5. Limitations, remaining gates and decision sequence

- The demonstrated contract is **exception-object/chain safety for malformed inspect JSON at two selected call sites**, under trusted synchronous single-process mocks. It does **not** establish memory erasure, secrecy of arbitrary **traceback frame locals** (which can reference raw inspect stdout/passwords), arbitrary third-party exceptions, malicious filesystem/host, asynchronous record mutation, actual Docker daemon quiescence, OS signals or real PG cleanup. Operational logs must not capture traceback locals containing credentials.
- The helper specifically handles `json.JSONDecodeError` from these inspect responses; other external data/metadata parsers, structure/index errors and arbitrary read paths are **not blanket-certified**. Ask Codex to check for related unprotected raw-error surfaces and for the suitability of the new public `RuntimeError` contract.
- **P1 operational acceptance-path inventory remains open:** the protected operational wrapper still imports frozen code and calls it directly; no mandatory controller admission exists in operational source. No raw PASS fallback may be allowed in a future rebaseline; archive and restart acceptance remain default-deny.
- Before any protected runner/wrapper/controller edit: require a fresh **read-only Codex P1-D review of exact tested/latest Git blobs**, review all remaining operational consumers and explicit owner F04-G01 authorization for narrow protected source changes. After any future authorized security rebaseline, make the original immutable frozen-runner negative regression green without altering its test. Real Docker 29/OCI no-pull pins, 16-wheel candidate-v2 provenance/license/OSV, `_HOST_HEALTH`, and disposable PG16.15 require separate permission/evidence.

**Decision recorded:** P1-D **TARGETED SYNTHETIC TESTS PASS at last code-tested e6 snapshot; final fixture-only tree STILL NEEDS ITS OWN CI RUN; FRESH CODEX AI REVIEW PENDING**. **F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**


## 6. Post-publication exact-source verification addendum (2026-11-19)

**This addendum supersedes the earlier timestamped CI-pending wording in Sections 1 and 4.** After the initial report was created, the GitHub PR workflow ran against the **report creation commit `5ea518216322fe8abd2d8b229e6c15ac737453b9`**. That commit includes **BOTH** fixture-only corrections, including the final `59b14ae25545ff22f4f9dd9469fee7a1913e54e4` change. Thus the exact latest source/test tree in this review packet **now has verified CI**. The report commit itself changed only documentation after the tests had been written.

**Verified [latest full candidate offline CI run 44463174519](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44463174519):** Python **3.11 and 3.12 each 393 passed / 1 failed (394 collected)**. The **sole fail** remains the unchanged protected accepted-runner `unexpected_manifest_secret` regression. Full workflow conclusion is **FAILURE** and is *not* a green release gate. [Focused offline verification 44463174473](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44463174473) succeeded; [host read-only prerequisites 44463174479](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44463174479) failed.

**P1-D candidate-only targeted tests: PASS on the final full source/test tree.** This updates the prior separate last-commit verification limitation, **not** the approval status: fresh read-only Codex P1-D review and operational-consumer inventory are still pending; F-04 remains OPEN / NO-GO, B5-4-2 BLOCKED. No protected-source, real Docker or PostgreSQL permission granted.
