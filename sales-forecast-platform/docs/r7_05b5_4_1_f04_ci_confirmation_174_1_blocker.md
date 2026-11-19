# F-04 CI follow-up — 174 PASS / 1 FAIL, real fail-stop blocker confirmed

**Date:** 2026-11-17
**Workflow:** `R7 B5-4-1 F-04 candidate offline tests`, run **44109233094**, commit `9f5c261270671f3cda4892c54e42b48b8e0e0aa6`.
**Evidence:** Both CPython 3.11 and 3.12 matrix jobs report **174 passed, 1 failed**, among 175 actually collected F-04 cases. Focused legacy unit workflow on the same commit completed success, but it does not cover the new failing F-04 regression. PostgreSQL preflight on the same commit failed by design/absent prerequisites; it is not a database run.

## The one retained F-04 RED regression

`tests/runtime/test_r7_05b5_4_1_f04_cleanup_lifecycle.py::test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]` injects one generated disposable secret into unexpected lifecycle metadata, mocks all daemon operations, and requires `CleanupUnconfirmed` after the manifest redact-and-detect sequence.

**Observed:** the runner raises the original synthetic `RuntimeError` rather than `CleanupUnconfirmed`. Source inspection explains why: the active `_LIFECYCLE` context is reset before `redact_test_secrets` sees the manifest. The secret text can be redacted, but the count `record['secret_redactions']` does not increment; consequently cleanup may remain marked as confirmed. This is a concrete security **classification/integrity blocker**, not evidence that a password escaped in the saved manifest.

**Do not neutralize or xfail the regression** to get CI green. It must be resolved by an independently reviewed, narrowly scoped correction to the accepted test runner after explicit controlled-rebaseline authorization; keep credential file cleanup, lifecycle context restoration and no-broad-deletion rules intact.

## Next authorization gates

1. Security reviewer examine the exact runner-finally ordering and proposed correction. In particular, make redaction accounting independent of the lifetime of `_LIFECYCLE` while preserving expected redacted manifest and `CLEANUP_UNCONFIRMED` classification on any detection; check no-secret success and error-path ContextVar reset.
2. Get explicit permission for protected `run_postgres.py` modification; **no protected edit performed** in this checkpoint.
3. Repeat both matrices and separately the retained safety/OCI/provenance suites. Only after wider OCI/wheel/license/build approval can controlled runner rebaseline be accepted.
4. Perform separately authorized disposable PostgreSQL 16.15 integration on clean trusted PC checkout, with cleanup verification, before B5-4-1 close.

**F-04 OPEN / NO-GO. No merge, deploy, or B5-4-2.**
