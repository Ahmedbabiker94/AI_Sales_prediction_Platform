# R7-05B5-4-1 / F-04 — Unified candidate scope model: first CI result

**Evidence date:** 2026-11-17
**CI:** GitHub Actions run `44125794584`; tested head `7d28b63ce75e70f140477afb71beb9cdfadb1d0e`.
**State:** CANDIDATE REVIEW ONLY / ORIGINAL RUNNER FAILS CLOSED TEST / NO-GO.

## Verified result

- **CPython 3.11:** 213 passed, 1 failed (214 collected).
- **CPython 3.12:** 213 passed, 1 failed (214 collected).
- The **only failed test in each matrix** is the previously retained accepted-runner regression `test_r7_05b5_4_1_f04_cleanup_lifecycle.py::test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]`; it still receives the synthetic preflight `RuntimeError` rather than required `CleanupUnconfirmed`.
- 12 added pure candidate scope tests passed, including partial handler installation, password creation failure, action/body failure, restoration failure and restoring outer nested ContextVar values.
- Other *passing* F-04 counterexamples intentionally demonstrate currently unsafe behavior in the unchanged runner. Passing them does not certify security.
- No Docker or PostgreSQL execution or approved Dockerfile/runner/lock changes took place.

## Candidate change inventory

`tests/postgres/run_context_scope_candidate.py` is a separate in-memory context manager modeling lifecycle binding, attempted handler installation, disposable secret binding and robust token restoration. It accepts test callbacks and never calls `signal.signal`, Docker, or accepted `run_postgres.run()`.

`tests/runtime/test_r7_05b5_4_1_f04_scope_candidate.py` tests the candidate with injected setup/teardown faults.

`docs/r7_05b5_4_1_f04_unified_protected_runner_patch_review.md` maps the **single protected security-only patch** to original source lines and lists independent acceptance requirements. It is a review-only blueprint; not an applied diff or a controlled-rebaseline authorization.

## Admission and blockers

Before changing the original runner, require explicit independent approval for security-only test-harness rebaseline, including error precedence on signal restoration failures, early/late ContextVar reset, unexpected JSON content, manifest publication failures, timeout/ambiguous cleanup and test-only credentials. The original red regression is not to be xfailed or weakened merely to get green CI. Corrected runner negative tests must assert absence of leaks; present green vulnerability demonstrations must not be mistaken for passing security controls.

CR-0 OCI, sixteen wheel source artifacts, license/provenance, and earlier HostHealth finding remain unresolved. Actual isolated PostgreSQL 16.15 and resource cleanup require separate opt-in. The draft PR must not merge/deploy or advance to B5-4-2.

**R7-05B5-4-1 / F-04 OPEN / NO-GO.**
