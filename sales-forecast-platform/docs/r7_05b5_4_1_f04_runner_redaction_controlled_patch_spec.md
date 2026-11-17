# R7-05B5-4-1 / F-04 — Protected-runner redaction correction: controlled patch specification

**Status: REVIEW-ONLY / NO PROTECTED FILE MODIFIED / NOT AUTHORISED / NO-GO.**
**Original accepted file:** `tests/postgres/run_postgres.py` blob SHA `0863e81b733381a80b2f83560d8bbc231513ba41`. Any change to the protected file requires separate controlled-rebaseline approval and review of the complete diff.

## Established evidence

GitHub Actions run **44110391213**: 193/194 pass on CPython 3.11 and on CPython 3.12. The retained red case `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]` proves `CleanupUnconfirmed` is not raised when unexpected secret-bearing metadata is included in the manifest despite otherwise successful mocked cleanup. The initial pure candidate `redaction_finalization_candidate.py` passes its own offline tests but is **not** integrated into the accepted runner.

## Precise root cause (source review)

- `run().finally` resets `_LIFECYCLE` in the inner cleanup `finally`, *before* `redact_test_secrets(json.dumps(record))`.
- `redact_test_secrets` depends on the ContextVar for **accounting**, though the string replacement itself succeeds with `_TEST_SECRETS`.
- Hence a new secret occurrence during manifest serialization can be scrubbed without increasing `record['secret_redactions']`. That can permit `cleanup_passed=True` and return the pre-existing `RuntimeError`, rather than raising `CleanupUnconfirmed`.
- **Additional not-yet-reproduced exception-path risk:** `_TEST_SECRETS.reset(secret_token)` is only inside a `finally` around `write_evidence`; a prior `json.dumps(record)` or redaction exception could escape without resetting secrets ContextVar. `_LIFECYCLE` may already be reset. This requires explicit negative tests, not an assertion of observed credential leakage.

## Minimal proposed design — NOT a patch applied to runner

1. Add a private pure helper accepting explicit `record` and the **generated test-secret tuple**, without looking up `_LIFECYCLE` for redaction accounting. Use validated plain JSON serialization, count all exact generated secret occurrences in **serialized JSON including nested keys/values**, and redact them. Carry forward a previously recorded positive `secret_redactions` count. Preserve the existing `CleanupUnconfirmed` outcome if there was any detection or cleanup uncertainty.
2. Retain the existing `redact_test_secrets()` functionality for stdout/stderr and command error redaction, but do not rely on it alone for the final manifest count once the lifecycle ContextVar is reset. Do not resurrect the lifecycle merely to make manifest redaction pass: ownership is bound to the trusted cleanup section and should not silently extend across subsequent daemon queries.
3. Rework only the trusted finalizer's **context restoration boundaries**, ensuring `_LIFECYCLE.reset(token)`, `_TEST_SECRETS.reset(secret_token)`, and original SIGTERM handler restoration on **all** exception paths that occur after setup. Use an exception-safe nested `try/finally` with exactly-once resets; review whether the surrounding `signal.signal` / `secrets.token_hex` initialization can fail before a secret token exists and must be separately guarded.
4. Separate manifest **decision before write** from the final write. If JSON serialization, redaction counting, manifest write or credential-file deletion is unconfirmed, never report PASS. Ensure final artifact is redacted; if publishing the manifest fails, propagate a terminal failure and explicitly record evidence unavailability rather than fabricate a successful run. No broad daemon prune, no opening production paths.
5. Keep existing owned-resource reconciliation, label-based deletion, bounded ambiguity and source-provenance checks intact. Avoid changing OCI pins, `Dockerfile`, wheel lock, test selection, database credentials or runner invocation in this narrow patch.

### Why merely moving `_LIFECYCLE.reset(token)` is insufficient

Moving the reset below `redact_test_secrets()` might make the one red test pass, but introduces new exception paths: if JSON encoding, evidence writing or a nested finalizer raises before the moved reset, the lifecycle may stay bound. A reviewed correction needs guaranteed exactly-once restoration and does not use an active lifecycle ContextVar as its only redaction counter.

### Required acceptance matrix — targeted tests first

- Unexpected secret in manifest value **and key**; multi-appearance counts; previously counted secret without new appearance.
- No-secret normal finalization must not generate false failure or alter counters.
- Existing cleanup failure or ambiguous daemon mutation must never be reset to PASS.
- JSON serialization error **before manifest write**, `write_evidence` failure, and secret-redaction helper exception: context tokens restored; failure never written as PASS.
- Credential-file removal `UNCONFIRMED` and failed daemon listing still produce terminal refusal; all event-handling paths avoid unsafe deletion.
- Verify no generated secret appears in saved manifest and that read-only source files, credentials and Docker commands remain unexposed.
- Maintain the original red regression **without xfail**, then rerun entire F-04 test matrix in 3.11 and 3.12 and retained safety suites. Confirm `run_postgres.py` hash changes only after explicit authorisation and records old/new SHA in signoff.

## Approval questions

1. Does the reviewer accept *explicit, context-free finalization counting* rather than prolonging the lifecycle ContextVar?
2. Does the proposal adequately handle every early and late exception path and prevent false success if evidence publication fails?
3. Is the trusted-host threat model still accurately documented, including in-process caller exposure?
4. Can this be approved as an isolated test-runner security patch **before** the broader OCI/client-image rebaseline, or should it be reviewed in one atomic controlled change set?

**No changes to accepted `run_postgres.py`, `Dockerfile`, or `requirements.lock` are authorized by this document. No Docker/PG tests, merge, deployment or B5-4-2. F-04 NO-GO.**
