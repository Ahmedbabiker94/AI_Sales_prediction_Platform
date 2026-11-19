# R7-05B5-4-1 — F-03 evidence freeze and F-04 read-only security/cleanup review

**Decision: OPEN / NO-GO.** Review record only. No approved runner, client Dockerfile, historical lock, PostgreSQL database, operational entrypoint, or production environment was modified by this review. This review does not constitute independent security approval.

## Executive interpretation

The separately staged, test-only **Candidate v2** changed only the candidate client package set: `pytest==8.4.2` was replaced by `pytest==9.0.3` to respond to an OSV finding. All other 15 wheels were retained. Candidate v2 passed local integrity, dependency-closure, official PyPI exact-file/hash comparison, OSV query, offline dependency resolution, build and selected runtime tests. Its compatibility results include an **unexplained earlier combined-run failure** and five subsequent successful full runs. Neither compatibility nor the absence of known OSV findings establishes full safety, provenance attestation, license compliance or PostgreSQL correctness.

## F-03 operator-reported evidence — local Windows/WSL/Docker tests

The operator supplied the following terminal output during the review (not an independently captured/signed build attestation):

| Stage | Observed result |
| --- | --- |
| Candidate v2 generator | `CANDIDATE_ONLY_NOT_APPROVED`, 16 wheels, `pytest 9.0.3`; separate `/home/ababi/b5-4-1-candidate-wheelhouse-v2` |
| Wheelhouse v2 exact-file/lock/manifest hash gate | `PASS: Candidate v2 integrity — 16 wheels` |
| Dependency-closure unit tests | `6 passed in 2.14s` |
| v2 dependency closure | 16 wheels, 109 `Requires-Dist` items checked, 15 active base dependencies satisfied |
| External verifier unit tests | `7 passed in 2.46s` |
| PyPI release file SHA-256 and filename | `pypi_exact_file_hashes_match: true` across all 16 |
| OSV exact-version query | `no_osv_known_findings_at_query_time: true`; no IDs listed at the time of operator query |
| `pip --dry-run --ignore-installed --no-index --require-hashes` | `Would install` all 16, including pytest 9.0.3 |
| Docker candidate v2 build | `--network=none --pull=false --platform linux/amd64` PASS; image tag `b5-4-1-client-candidate-v2:local`; BuildKit exported manifest-list `sha256:950740f12e03505e38735ecf91832b40a4a5ab4d202e8e48cc12a1bef69ea1fd`, config `sha256:ac01bcfc9cee5657e7fb8e5f120314cfc9966a2fb996bd4d8fffdf05e4753741` |
| Restricted runtime imports | `pytest 9.0.3` and selected packages PASS under UID 1000, read-only, no network |
| Project imports | `CanonicalForecastStorageBatch` and `build_host_reporting_requests` PASS after correcting host source path and module name |
| Combined reporting + boundary tests, first run | **17 passed / 2 failed / 7 errors**; repeated `ForecastReadDenied: host shutdown required` after health gate became terminal; root cause not established |
| Each selected test module in isolation | Reporting Requests: `22 passed`; Host Boundary Audit: `4 passed` |
| Combined selected tests, later rerun | `26 passed in 6.52s` |
| Five independent Docker process/container runs | Each `26 passed`: 10.31s, 5.28s, 5.07s, 5.76s, 5.70s (130 successful test executions, not 130 distinct tests) |

Earlier Candidate v1 `pytest==8.4.2` PyPI file hashes all matched, but OSV reported `GHSA-6w46-j5rx-g56g` and `PYSEC-2026-1845`; the verifier correctly blocked that candidate. Keep the v1 evidence unchanged. The v2 OSV query is a time-scoped advisory lookup, **not** a proof that the image is vulnerability-free. Local immutable base image index remains `docker.io/library/python@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39`. The local client tag is mutable and must not be used as a trusted identity in the approved runner.

## F-04 read-only source review: security and cleanup

**Source inspected:** `tests/postgres/run_postgres.py`, `docs/r7_05b5_4_1_f02_runner_wiring_review.md`, `src/services/canonical_forecast_read_service.py`, the selected request and boundary tests, and the existing rebaseline proposal. This is a focused source review, not full-code coverage or a signoff.

### Controls observed

1. **Git provenance before Docker mutation:** `run()` calls `verified_clean_source()` before creating evidence or running Docker; it checks the expected outer Git worktree, HEAD format and full clean porcelain status. This prohibits the Windows-worktree path under WSL where Git metadata is not usable.
2. **No implicit production environment mount:** client test `docker run` explicitly mounts only a disposable socket volume and read-only project `src`/`tests`; uses no network, nonroot UID, read-only root, dropped capabilities, resource limits, and proxy variables cleared. PostgreSQL server listens on a UNIX socket with `listen_addresses=` and no port publish.
3. **Ownership-scoped cleanup:** creation attempts are registered before CLI daemon mutations; names are constrained to the run prefix. Removal verifies label `r7_05b3_run` matches the recorded UUID. Reconciliation is time-bounded, and an ambiguous daemon mutation prevents `CLEANUP_SUCCESS` even if nothing currently lists. No blanket `prune`.
4. **Credentials:** ephemeral generated test passwords; `redact_test_secrets` guards logged strings and final manifest; temporary client envfile is removed, and uncertainty blocks success.
5. **PostgreSQL provisioning:** the one root provisioning container is restricted to CHOWN, `--cap-drop=ALL --cap-add=CHOWN`, limited memory/PIDs and the run's disposable volume.
6. **Trust-boundary disclosure documented:** host boundary tests expressly demonstrate that handing the in-process dispatcher to untrusted Python exposes privileged private references and can bypass intended one-use semantics. Passing these tests is **evidence of a limitation**, not proof of hostile-plugin isolation.

### Blockers and questions for independent review

**F04-01 — Blocker: old reference and implicit runtime pull semantics.**
The accepted runner still sets `BASE=sha256:417063...`, `POSTGRES=sha256:6a35283...`, uses `inspect_image(identity)['Id'] == identity`, and references `POSTGRES` in **both** root provisioning and PostgreSQL server `docker run` calls. The Docker Desktop 29 image store can expose an index ID; a bare config ref may be unrunnable. Final wiring must use the validated immutable repository index after proper registry->manifest->config/local-platform verification and explicitly prohibit implicit pulls at both run sites. An `inspect_image()` change alone is insufficient. The old client base remains unavailable/unrebaselined.

**F04-02 — Blocker: test client build provenance and complete wheels.**
The accepted `build_client` copies **only psycopg2-binary**, still uses the old Dockerfile/lock, sets `DOCKER_BUILDKIT=0`, and trusts `--iidfile` followed by old exact `inspect_image`. It does not consume the reviewed 16-wheel v2 lock/context, nor establish an immutable client build identity bound to its Dockerfile, base and wheel artifacts. Validate BuildKit/classic image-ID behavior, amd64 selection, complete hashes and no network in the future controlled rebaseline.

**F04-03 — Open risk: earlier fail-stop test failure remains unexplained.**
The original 26-test run produced `host shutdown required` in multiple tests, while independent runs and later 5/5 repeated runs passed. `_HOST_HEALTH` is intentionally process-global and terminal; never reset it or paper over this failure. Investigate sequencing, lock operation obligation lifecycle and reproducibility before formal stability acceptance. This is not proof of a pytest defect.

**F04-04 — Review required: daemon mutation & cleanup uncertainty.**
Check all `docker run/create/start/restart` paths and the extra `_host_stage` callback ownership accounting, especially timeout/interrupt windows between daemon success and client acknowledgement. Reconciliation has bounded time and label checks, but no bounded finite poll proves absence of a deferred Docker mutation. Keep `CLEANUP_UNCONFIRMED` as mandatory fail-closed. Verify negative tests for foreign resource labels, missing/late resources, unknown daemon outcomes and credentials not removed.

**F04-05 — Review required: secret disclosure and evidence.**
Docker server env and argv receive disposable passwords; `Config.Env` is explicitly sanitized in recorded inspect payload, and exact-string redaction covers other record fields. Independent reviewer must inspect additional Docker metadata fields and local host/daemon exposure paths; secret-file unlink is not secure erase. Unexpected secret redactions must remain a rejected run. Avoid any publication of raw local credentials or daemon inspections.

**F04-06 — Review required: license obligations and provenance attestations.**
All 16 wheels have some declared license information, but compatibility with intended distribution and inclusion of third-party notices (notably NumPy/pandas and psycopg2-binary) is not independently accepted. Exact PyPI SHA matching proves file identity relative to PyPI, **not** verified publisher attestations, repository source-build correspondence, or a complete vulnerability scan of the base OS and binary bundled libraries. License and supply-chain acceptance stays pending.

**F04-07 — Blocker: trusted-host boundary.**
An untrusted Python module in the same process is not isolated from dispatcher internals by the opaque handle mechanism. No claim of hostile plugin sandboxing or production-grade isolation. Require an independently reviewed process boundary if hostile code must be supported; do not change operational API imports opportunistically.

### Next review gates (not authorization to execute)

1. Independently review this F-03 evidence and F-04 source findings, including additional negative-test evidence for daemon lifecycle, identity checks, failure redaction and isolated host security boundary. Define a disposition for the intermittent fail-stop result.
2. Re-review and freeze the **exact** proposed v2 wheelhouse hashes/lock and client Dockerfile, signed provenance where feasible, licensing obligations and reproducible-build material. Candidate v2 results are not a release artifact approval.
3. Produce a precise runner wiring proposal with both immutable PostgreSQL runtime references, index/config handling, client build identity, no-pull semantics and complete explicit test selectors, plus negative tests; obtain explicit controlled rebaseline approval **before** editing accepted runner/Dockerfile/lock.
4. Only after prerequisites pass, create a separate clean Linux-native checkout. A real disposable PostgreSQL16.15 run requires a separate explicit opt-in and evidence/cleanup validation. Keep original dirty Windows project and production state untouched.

**Final gate: R7-05B5-4-1 OPEN / NO-GO. No PostgreSQL integration, merge, deployment, B5-4-2 or implicit rebaseline approval.**
