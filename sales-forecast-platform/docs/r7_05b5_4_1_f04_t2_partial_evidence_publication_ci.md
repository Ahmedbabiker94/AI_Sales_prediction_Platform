# R7-05B5-4-1 / F04-T2 — incomplete evidence and atomic terminal-manifest publication

**Date:** 2026-11-18. **Scope:** review-only candidate + deterministic mock tests. **F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**

## Pinned source identities

- Frozen accepted `tests/postgres/run_postgres.py` Git blob `0863e81b733381a80b2f83560d8bbc231513ba41`: verified **UNCHANGED**.
- Review-only `tests/postgres/run_postgres_security_patch_candidate.py` blob `dafd1822d58700663eaef378824c5c0e87d99428` retains the unconditional `OFFLINE_REVIEW_ONLY` command denial and no CLI entrypoint.
- T2 regressions `tests/runtime/test_r7_05b5_4_1_f04_t2_partial_evidence_write.py` blob `e439a9230cfae43ed31dfd64f84aaaf1ab1ffb12`.
- Candidate workflow `.github/workflows/r7-b5-4-1-f04-candidate-offline.yml` blob `293db3fc7b26bb8f9785e8b006d251a1e6831d65`, explicitly includes T2 tests.
- Prior test expectation changes required by the more precise failure-precedence contract: M1 test blob `eae221dbc6ecdf1062a09b4be5f6c2c37431ceae`; fork-error test blob `ccd6660a39cd0b02100c1faadcc982d3960e2952`; scope test blob `024dfc7dc816663089a5fab2bd1ef0b0e2aacab8`.
- Fully tested head commit `854aaf90895cd9c83c9c51ad9ece2f1b6c25afe2`.

## Finding reproduced first

The previous candidate raised on terminal JSON serialization or direct `write_evidence(run-manifest.json, ...)` failure *after* promoting the retained lifecycle record to PASS. A partial write could leave `run-manifest.json` in an accepted-looking but invalid or incomplete state. An injected secondary manifest-write failure also masked the original preflight exception. Stage stdout writes lacked an explicit `EVIDENCE_UNAVAILABLE` classification.

**Pre-fix RED:** [candidate CI run 44261774325](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44261774325) on Python 3.11 and 3.12: **286 passed / 8 failed, 294 collected**. Seven new T2 negative tests failed for the targeted bugs; the eighth failure was the unchanged original `unexpected_manifest_secret` accepted-runner regression. This RED run included the seven original T2 cases; two additional publication-boundary cases were added later.

## Candidate-only correction

1. `publish_terminal_manifest_candidate(path, text)` writes the complete, redacted manifest to an exclusive random same-directory staging file via the existing `write_evidence`. Only once the write completes does `os.link(staging, run-manifest.json, follow_symlinks=False)` atomically create the accepted filename. The hard-link operation **refuses to overwrite** an already existing destination; no `os.replace` or unsafe truncation is used. The known staging file is best-effort removed; if a synthetic `os.link` creates the destination then raises, the candidate removes the final name only when it can prove it shares the staged inode. No deletion of unrelated evidence is authorized.
2. On manifest serialization, staging write or publication failure, retained `record['result']` is marked `FAILED`, with separate `record['evidence_status']='EVIDENCE_UNAVAILABLE'`. This **does not claim that resource cleanup itself failed** if cleanup was independently confirmed. A stage stdout/stderr write exception similarly marks the evidence unavailable, while leaving a possible partial diagnostic file clearly non-terminal.
3. When there was already a primary preflight/test failure, secondary serialization/write/publication failure is recorded but cannot mask that original exception. Previous fork/scope regression tests were strengthened to assert this precedence and the explicit unavailable classification; no tests were skipped or xfailed.
4. M1 regression now checks the **atomic publication operation**, rather than merely beginning the write, to continue proving that the previous SIGTERM handler is restored before terminal PASS publication.

## Mocked test matrix and CI

Nine new T2 test cases cover three writer faults (partial prefix, fully written then exception, before-write), final JSON serializer fault, publication failure before link, partial stage stdout write, primary + secondary failure precedence, existing-manifest collision/no overwrite, and a simulated link-created-then-error path. Tests run a complete fake main-body where needed; all Docker-like calls, process execution, images, signal install/restoration, and credentials are mocked or test-only. Local file operations affect only newly created pytest temporary directories. No real Docker, PostgreSQL, network, subprocess, or OS signal was used.

**After fix:** [candidate CI run 44262544922](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44262544922) for Python **3.11: 295 passed / 1 failed (296 collected)** and Python **3.12: 295 passed / 1 failed (296 collected)**. All nine T2 cases plus prior H1/H2/M1/M2/T1 mock cases passed. Only the original unchanged protected-runner `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]` is RED (unexpected `RuntimeError` instead of expected `CleanupUnconfirmed`). The aggregate matrix is deliberately **not all green**. [Focused offline verification 44262544955](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44262544955) succeeded; [read-only host prerequisite run 44262544906](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44262544906) failed/unverified.

## Deliberate limits and remaining gates

The staged hard-link protocol proves the specified local mock behavior, not durable power-loss persistence or correctness under a malicious/racing host. It does not fsync the staged file or containing directory, prevent external unlink after publication, securely erase plaintext, or guarantee recovery when the process is SIGKILLed. A partial **nonterminal** stdout artifact may remain after a write exception, but its failure is recorded and cannot become a PASS manifest in the tested paths. If an adversary tampers with a staging inode or inhibits best-effort cleanup, remaining artifacts must be independently reviewed. Human independent security signoff and separate owner authorization for any protected runner rebaseline are still required.

**Additional open gates:** original redaction RED; indexed OCI/Docker29 no-pull checks; complete verified 16-wheel provenance; `_HOST_HEALTH`; separately authorized disposable PostgreSQL 16.15 verification; further signal/filesystem and integration judgment. No protected runner edits, real Docker/PG, merge, deployment, or advancement to B5-4-2.

**Decision: F04-T2 SYNTHETIC CANDIDATE TEST GATE PASSED; F-04 OPEN / NO-GO.**
