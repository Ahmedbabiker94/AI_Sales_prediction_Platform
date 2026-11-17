# R7-05B5-4-1 — F-04 OCI identity negative review

**Status: Candidate-only tests committed; NOT RUN; NO-GO.**

## Inspected current implementation

- `tests/postgres/verify_postgres_oci.py`: read-only OCI index → linux/amd64 manifest → config comparison for pinned PostgreSQL 16.15 and official Python base. Uses a platform-constrained local inspect and two digest-pinned `buildx imagetools inspect --raw` metadata calls; fails closed on missing remote metadata.
- `tests/postgres/runtime_reference_candidate.py`: candidate-only selected `postgres_run_references()` and `local_build_identity()`; no actual Docker run wiring.
- `tests/runtime/test_r7_05b5_4_1_oci_identity.py` and `test_r7_05b5_4_1_runtime_reference_candidate.py`: existing index/config mismatch, invalid platform, duplicate amd64, metadata outages, invalid iidfile and both PostgreSQL reference placeholders.
- Accepted `tests/postgres/run_postgres.py`: still uses old bare config digest references and original one-wheel build. Not modified.

## New tests

`tests/runtime/test_r7_05b5_4_1_f04_oci_negative.py`: 20 parametrized cases (6 malformed index, 5 malformed manifest, 5 wrong local identity, 3 missing metadata calls, 1 exact immutable-lookup argv). No Docker or registry operations are executed by these mocks.

The 20 cases extend the previously committed 14 owner/mutation negative cases and six finalization cases: **40 newly added F-04 test executions are pending validation**. This number describes generated pytest parameter cases, not proof that they pass.

## Critically still blocked

1. **No-pull guarantee is NOT implemented** in the accepted runner. Existing verify functions do not substitute for reviewed `docker run --pull=never` (or exact supported equivalent) on both PostgreSQL run sites and for clients; do not mistake a unit test of metadata lookup for runtime no-pull enforcement.
2. **BuildKit iidfile binding** to immutable artifact provenance, approved sixteen-wheel lock, complete hashes, exact Python base and selected platform is incomplete. Need separately reviewed controlled rebaseline design, negative tests and explicit authorization before editing runner/Dockerfile/lock.
3. No OSV result constitutes proof of no vulnerabilities; no PyPI SHA check constitutes independently verified publisher attestation. Licenses and source-to-artifact provenance remain independent signoff requirements.
4. Real disposable PostgreSQL test still requires distinct explicit opt-in **before** closing B5-4-1, even if all offline tests pass.
5. Earlier intermittent `HostHealth` fail-stop requires documented disposition. Passing a set of F-04 tests cannot prove hostile same-process Python isolation or prove daemon cleanup in real conditions.

**R7-05B5-4-1: OPEN / NO-GO. No merge, deployment or B5-4-2.**
