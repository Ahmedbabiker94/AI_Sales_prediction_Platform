# R7-05B5-4-1 — F-04 mocked-finalization expansion

**Status: TESTS COMMITTED / NOT RUN / REVIEW REQUIRED / NO-GO.**

## Scope

New file: `tests/runtime/test_r7_05b5_4_1_f04_cleanup_lifecycle.py`, six parametrized scenarios. These call the real accepted `run_postgres.run()` function but replace its external calls: Git provenance, image preflight, Docker command and scoped cleanup are simulated entirely in-memory. No Docker daemon, database, model or registry access occurs. This is test-only work, not a modification or approval of `run_postgres.py`, `run_b5_4_1_postgres.py`, the Dockerfile, wheel lock or runner wiring.

## Scenarios and expectations

1. Registered unresolved daemon mutation, even when mocked removal succeeds and listings are empty: terminal `CLEANUP_UNCONFIRMED`.
2. An owned name whose mock cleanup returns false (e.g. foreign/missing owner label): `CLEANUP_UNCONFIRMED`.
3. An owned container shown in final resource listing despite successful mock removal: `CLEANUP_UNCONFIRMED`.
4. Docker listing unavailable: both final lists recorded as `UNAVAILABLE`, `CLEANUP_UNCONFIRMED`.
5. Transient credential unlink unconfirmed: credential status `UNCONFIRMED`, terminal `CLEANUP_UNCONFIRMED`.
6. Unexpected disposable secret injected into nonstandard manifest field: value redacted, `secret_redactions` recorded and acceptance rejected.

Each scenario checks that a final manifest exists and reports `result=FAILED`, `cleanup_passed=false`, `cleanup_status=CLEANUP_UNCONFIRMED`, source commit identity and scoped attempted container ownership. Cleanup and context variables must be reset. The tests avoid real subprocess creation and accelerating bounded clock waits changes only the runner module's `time` reference within the fixture.

## Existing coverage reused

See `test_r7_05b5_4_1_pg_runner_safety.py` for stage registration, interrupts, temporary credential failure and stdout/stderr scrubbing; `test_r7_05b5_4_1_git_provenance.py` for clean-source gate; `test_r7_05b5_4_1_runtime_reference_candidate.py` for candidate image ref check; and `test_r7_05b5_4_1_f04_runner_negative.py` for Docker ownership label and unknown query semantics.

## Remaining work and limitations

- **NOT RUN** in Codex (environment offline) or on the PC yet. The files must be collected and executed as real pytest using reviewed, synchronized source. Test assertions and mocks themselves require independent review and are not evidence of passing runtime behavior.
- These mocks cannot prove actual Docker daemon eventual consistency, timing behavior, secret exposure through host/daemon inspection or complete PostgreSQL resource cleanup.
- Run the offline negative test suites and investigate any failures without weakening fail-closed rules.
- The intermittent `HostHealth` first-run failure is still unexplained. Passing subsequent runs must not erase this record.
- The original runner's legacy OCI/config-ID and one-wheel client build remain unchanged. A proposed full controlled rebaseline, independent OCI and license review and explicit authorization are prerequisites to real disposable DB integration.
- Do not claim hostile in-process Python isolation. The boundary audit demonstrates privileged-reference disclosure when the dispatcher escapes trusted host code.

**Milestone: R7-05B5-4-1 OPEN / NO-GO. No merge, deployment, B5-4-2 or real PostgreSQL run.**
