# R7-05B5-4-1 / F-04 — CR-0 evidence register and independent review gate

**Status: INVENTORY REVIEW COMPLETED FROM GITHUB / EXTERNAL ARTIFACT VALIDATION NOT PERFORMED / NO-GO.**
**Branch:** `fix/r7-05b5-4-1-bootstrap`.
This record is an internal source review, **not** an independent security, license, or supply-chain approval. Do not modify accepted runner, Dockerfile, dependency lock, OCI pins, or test host; no Docker, PostgreSQL, pip, PyPI/OSV network request, merge or deployment was executed for this checkpoint.

## Exact repository inputs inspected

- `tests/postgres/verify_postgres_oci.py`: immutable PostgreSQL 16.15 and Python 3.11 official repository-index, linux/amd64 manifest/config constants, read-only local/registry metadata comparator. Its read-only Docker/buildx metadata queries **cannot be run from reviewing source alone**.
- `tests/postgres/prepare_client_wheelhouse_v2_candidate.py`: transforms fully checked candidate-v1 into 16-file v2, replacing pytest 8.4.2 with **9.0.3**. Requires explicit PyPI authorization to download one replacement wheel. Is not a trusted artifact source or accepted Dockerfile.
- `tests/postgres/verify_client_dependency_closure_candidate.py`: verifies declared active base wheel dependencies for CPython 3.11, linux/amd64; explicitly does not verify extras, publisher identity or licensing.
- `tests/postgres/review_client_supply_chain_candidate.py`: extracts embedded wheel license metadata and sets `needs_license_manual_review=True`, `pypi_artifact_provenance_verified=False`, and `vulnerability_review_completed=False` for every package. This inventory does **not** approve licenses.
- `tests/postgres/check_external_client_artifacts_candidate.py`: opt-in PyPI release-specific filename/SHA/yanked comparison plus OSV batch lookups. May fail closed on metadata errors; a timestamped zero-findings result does not prove absence of vulnerabilities or independently attest publisher identity.
- `tests/postgres/requirements.lock` and `Dockerfile`: still the original **one-wheel** lock and original unapproved config-only Python `FROM`; not replaced by v2.
- `docs/r7_05b5_4_1_controlled_rebaseline_implementation_proposal.md`: previously operator-reported candidate-v1 preparation and isolated container imports; treated as *historical observations*, not rerun or independently verified here.
- `docs/r7_05b5_4_1_security_review_blockers.md`: existing trusted-host-only boundary and unexecuted PostgreSQL integration blockers remain open.

## Artifact evidence inventory and release gate

| CR-0 item | Evidence in repository or historical report | Missing minimum acceptance proof |
| --- | --- | --- |
| PostgreSQL OCI | Pinned index `sha256:e8a07d2c7579b038938cdba6c539e580e5001caf9ea203d397b5cc83e76bd938`; selected amd64 manifest `sha256:cf5d353f55240ecc2f811827a15c7d1f2d562f08019b49dca12b5a59f4ab5962`; config `sha256:6a35283e53fbe883da6a6ffbce8d3999dc66076cc5c4e2f1d21bdbd9e26bf86f` | Independent digest-chain review, locally cached resolvable reference on approved host, supported no-pull + platform semantics and attestations/trace |
| Python base OCI | Pinned `docker.io/library/python@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39` with manifest/config stored in verifier | Independent exact image revision and provenance review; local base resolution and offline build repeatability |
| Wheel candidate v2 | Preparation script expects **exactly 16** hashed regular wheels, pytest 9.0.3, no unexpected files; historical candidate-v2 operator record | **Exact 16-file v2 manifest, lock, per-wheel filename/SHA and archive** reviewed as immutable inputs; no assumption that local operator files exist on GitHub |
| Python/ABI and dependency closure | CPython 3.11.18 Linux/amd64 dependency checker and historical import checks | Fresh per-package report, `pip check` and package imports on approved client image; matching actual base glibc/ABI |
| PyPI release SHA / OSV | Time-bounded opt-in checker supports 16 release-specific SHA comparisons and OSV querybatch | Timestamped complete evidence for **v2** (not v1), reviewer assessment of findings and advisory freshness |
| Licenses and origin attestations | Declared license headers/files can be inventoried, without adjudication | Manual per-package license review, obligations, artifact origin and provenance verification by independent reviewer |
| Build reproducibility | Historical locally built candidate-v2 test image, with local tags/IDs and old accepted one-wheel build still separate | Reviewed candidate Dockerfile content SHA, full wheel/lock hashes, exact base ref, BuildKit settings, iidfile/inspect comparison, no-tag runtime binding |
| Host security/cleanup | Existing fail-closed source-level lifecycle tests; `_HOST_HEALTH` intermittent failure record; dispatcher trust-boundary limitation | Security reviewer signoff, root-cause/disposition and actual disposable DB/daemon cleanup evidence at authorized acceptance checkpoint |

## Explicit policy for missing evidence

**Do not fabricate or transcribe wheel hashes from general reports.** A filename list, incomplete hash output, PyPI result for v1, or named local Docker image is not equivalent to the exact 16-wheel v2 artifact inventory. Do not import a mutable Docker tag as a trusted identity. The registry may be unavailable: fail closed rather than silently fall back.

### Source-review risk notes

1. `review_client_supply_chain_candidate.py` intentionally marks every package for license manual review and origin attestation unverified: a count of license header records alone cannot be a release approval.
2. `verify_client_dependency_closure_candidate.py` evaluates only active base dependencies for the specified environment. Extras and platform-specific packaging must be reviewed separately.
3. `check_external_client_artifacts_candidate.py` validates PyPI release-specific SHA and OSV known findings when explicitly run, but it is not cryptographic trusted-publisher verification or signed SBOM.
4. `prepare_client_wheelhouse_v2_candidate.py` can download under separate explicit opt-in. No such download is authorized by this design review.
5. The original `tests/postgres/requirements.lock` still contains only psycopg2-binary; accepted `Dockerfile` still pins the historic missing base. A controlled rebaseline has **not** occurred.
6. Source-only review cannot establish Docker Desktop 29 registry cache, buildx exporter semantics, host-specific local image identities, or real daemon cleanup.

## Ordered next gate (not authorization)

**CR-0A:** Obtain or materialize the exact candidate-v2 manifest, lock, 16 wheels and associated previous recorded evidence for a reviewer, without changing the original worktree or exposing secrets. If artifacts unavailable, record CR-0 as **BLOCKED**.

**CR-0B:** Independently verify every wheel hash, Python marker, dependency closure, package license and source-to-artifact provenance. Verify OCI refs/platform and reviewed Dockerfile SHA independently. Store time-stamped evidence containing exact source commit and explicit reviewer identity/decision; avoid printing passwords or host-private paths.

**CR-0C:** Resolve acceptable trusted-host-only threat model and earlier first-run `_HOST_HEALTH` fail-stop; obtain separate independent security and supply-chain signoff. Only then seek **explicit controlled-rebaseline authorization** for CR-2/CR-3; CR-1 code is candidate-only and its tests remain **NOT RUN**.

**CR-4/CR-5:** Execute offline tests and, with a separate explicit operator opt-in, real disposable PostgreSQL 16.15 integration on a clean Linux-native checkout before closing B5-4-1. All resource deletion must be owned/label-verified; no pruning.

**Decision: R7-05B5-4-1 / F-04 OPEN / NO-GO.** No Docker, PostgreSQL, accepted runner edits, merge, deploy or B5-4-2.
