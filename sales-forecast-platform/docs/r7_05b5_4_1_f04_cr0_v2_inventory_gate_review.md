# R7-05B5-4-1 / F-04 — CR-0 v2 supply-chain candidate gate correction

**State: TEST-ONLY CANDIDATE CORRECTION COMMITTED / NOT RUN / REVIEW REQUIRED / NO-GO.**

## Source-grounded issue

The existing `review_client_supply_chain_candidate.py` unconditionally called `verify_candidate_wheelhouse(folder)`, whose default `TOP_LEVEL` includes `pytest==8.4.2` (v1). Candidate-v2 explicitly replaces pytest with **9.0.3** via `V2_TOP_LEVEL` in `prepare_client_wheelhouse_v2_candidate.py`. Thus passing v2 to the unmodified license inventory would fail its version precheck, **even if the v2 artifacts were complete and correctly hashed**. The external PyPI/OSV checker and dependency closure verifier already offered explicit `top_level` selection; inventory was inconsistent.

## Narrow candidate-only correction

The license/supply-chain inventory now accepts optional `candidate_version` with default `v1` for backward compatibility:
- `v1`: unchanged integrity gate and version requirements.
- `v2`: explicitly call `verify_candidate_wheelhouse(folder, top_level=V2_TOP_LEVEL)` before reading wheel metadata.
- Any other version: reject before package metadata or network activity.
- CLI accepts `--candidate-version {v1,v2}`, default v1.
- Status remains **`REVIEW_REQUIRED_NOT_APPROVED`**, every package keeps `needs_license_manual_review=True`, `pypi_artifact_provenance_verified=False`, `vulnerability_review_completed=False`. No approval flag was relaxed.

New `tests/runtime/test_r7_05b5_4_1_f04_cr0_v2_supply_chain_gate.py` defines **8 offline parameterized pytest executions**, covering v2 routing, v1 default, four invalid versions, hash-check failure, and v1/v2 cross-version rejection. Mocked integrity checks do not verify actual 16 wheels; these tests are **NOT RUN**.

## Remaining CR-0 evidence blocked on external bytes

The repo contains candidate scripts, but not the actual operator-held v2 16-wheel directory, `candidate-manifest.json`, `candidate-requirements.lock` and signed review. This correction **does not** grant provenance, license or vulnerability signoff. At the PC acceptance gate run the real v2 wheelhouse verification, dependency closure and version-selected license inventory against the exact provided bytes, with recorded file digests, environment, source commit and independent review.

Do not upload the 16 wheels to GitHub automatically, do not use production credentials, do not modify original accepted `tests/postgres/requirements.lock` or `Dockerfile`, and do not call `--allow-network` merely to populate missing evidence.

**Reported F-04 inventory:** earlier 168 newly authored pytest executions + 8 = **176 newly authored candidate/offline cases, all NOT RUN**. They are not 176 passes; some are limited mock contracts. No runner rebaseline authorized.

**R7-05B5-4-1 / F-04 OPEN / NO-GO.**
