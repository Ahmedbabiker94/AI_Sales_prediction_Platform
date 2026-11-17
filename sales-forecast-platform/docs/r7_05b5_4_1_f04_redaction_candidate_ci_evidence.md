# R7-05B5-4-1 / F-04 — Context-free redaction proposal CI evidence

**Date:** 2026-11-17
**Status:** PROPOSAL TESTED / ACCEPTED RUNNER DEFECT STILL REPRODUCED / F-04 NO-GO.
**Branch:** `fix/r7-05b5-4-1-bootstrap`.

## Source-grounded confirmed defect

Original `tests/postgres/run_postgres.py` resets `_LIFECYCLE` in cleanup before calculating serialized manifest secret-redaction count. `redact_test_secrets()` only increments the active record's `secret_redactions` if `_LIFECYCLE.get()` is non-null. An unexpected disposable secret in manifest metadata can be redacted from output without the required failure-classification count; the original finalizer can propagate its triggering exception rather than `CleanupUnconfirmed`.

This is **a fail-stop accounting/classification bug**, not evidence of plaintext credentials escaping the saved manifest.

## Candidate-only proposal, no runner modification

- `tests/postgres/redaction_finalization_candidate.py`: pure in-memory proposal returning a redacted JSON string and a failure flag without relying on `_LIFECYCLE` or writing evidence. Counts exact generated disposable secret appearances in a pre-finalized serialized record, marks `cleanup_passed=False`, `cleanup_status=CLEANUP_UNCONFIRMED`, and `result=FAILED` on any redaction, then verifies that the final serialisation has the **same** redaction count. Carries forward previous redaction counts and prior cleanup failure. Rejects ambiguous overlapping/disallowed secret inventories. It is **not** imported by the accepted runner.
- `tests/runtime/test_r7_05b5_4_1_f04_redaction_finalization_candidate.py`: 19 parametrized offline pytest executions covering nested values and keys, multiple secret occurrences, clean/no-secret case, already-failed state, prior detection, invalid inputs and ContextVar reset.
- CI `.github/workflows/r7-b5-4-1-f04-candidate-offline.yml` now compiles the proposal and runs its tests in addition to the retained original red negative.

## Real CI observations

Run **44110391213**, branch SHA `fb666dbfac10daa547c87efb9f1dbe073f4fc491`:
- **CPython 3.11: 193 PASSED, 1 FAILED** (194 collected)
- **CPython 3.12: 193 PASSED, 1 FAILED** (194 collected)
- The **one FAILED** is deliberately retained accepted-runner regression `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]`. Do not xfail, exclude, suppress or change its expectation.
- Earlier syntax error in the new pure helper was corrected (commit `fb666db`); test collection now completes.
- Docker, registry, PostgreSQL, real wheel artifacts and accepted runner have **not** been exercised/modified as part of this candidate test.

## Security review requirements for proposed accepted-runner correction

Before touching protected runner code, independently decide whether to:
1. Pass the lifecycle record explicitly to a tightly scoped pure redaction/count helper, rather than depending on ContextVar state; or
2. Adjust the original finalizer's ContextVar lifetime with tightly bounded restoration in exception-safe paths.

Either option must preserve strict owner label cleanup, no daemon-prune, secret scrubbing before evidence publication, fail-stop status on any match, and prior lifecycle/context reset guarantees. Review redaction of nested metadata *keys as well as values*, emitted command/error strings and file write-failure behavior. Validate both a clean manifest and unexpected leaked secret, including branch/exception/concurrency cases, against the actual runner. A positive candidate helper test is **not** acceptance of a replacement.

This test-only candidate is for review; it is not a signed patch or authorization. A controlled-rebaseline/security approval remains required before any changes to `run_postgres.py`, `Dockerfile`, original `requirements.lock`, or real Docker integration.

## Remaining milestone blockers

- Real runner secret-redaction classification remains RED.
- `_HOST_HEALTH` intermittent failure and trusted-host/untrusted Python boundary need documented disposition.
- OCI no-pull, Python base, 16-wheel candidate-v2 provenance, hashes, licenses, BuildKit/client iidfile wiring need approval.
- Complete offline regression verification and **separately authorized** real disposable PostgreSQL 16.15 foundation/restart/B5-4-1 with inspected owned-resource cleanup are required before B5-4-1 acceptance.

**R7-05B5-4-1 / F-04 OPEN / NO-GO. No merge, deployment, B5-4-2.**
