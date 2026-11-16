# R7-05B5-4-1 — F-02 runner wiring review (proposal only)

**Status: DESIGN REVIEW / NO-GO for runner changes.** The reusable pinned OCI verifier and its offline tests passed locally (27/27 reported by operator). This document does not approve changing the existing `run_postgres.py`, `Dockerfile`, old client base/config pin or PostgreSQL run. No DB test has been run.

## Source-grounded problem

`run_postgres.py` currently uses `POSTGRES = sha256:6a3528...` (config digest) in both `inspect_image(POSTGRES)` and two separate `docker run` calls: the narrowly scoped provisioning container and the actual PostgreSQL server. The old `inspect_image` checks `image inspect(...)[0]['Id'] == identity`, which is incompatible with the index-valued `.Id` observed on Docker Desktop 29/containerd. Changing only that check does NOT ensure that the subsequent config-digest-only `docker run` can resolve the image.

`build_client` similarly checks unavailable old client base `sha256:417063...`, copies only the psycopg2-binary wheel, copies the original Dockerfile/lock and uses `docker build --network=none --pull=false --iidfile ...`. The returned iidfile digest may be a config or OCI index depending on builder/exporter. Checking `.Id` against iidfile alone is not a sufficient proof of either provenance or successful client dependency closure. In addition, builder selection is currently influenced by `DOCKER_BUILDKIT=0` in the host `command(build=True)` path; builder behavior must be explicitly reverified before any client rebaseline.

`verify_postgres_oci.py` now provides read-only `verified_postgres_runtime_reference()` and `verified_client_base_runtime_reference()`. Both return immutable `docker.io/library/{postgres|python}@sha256:<index>` only after registry/index/manifest/config and local platform checks. They do not mutate the original runner, perform an offline integration run, verify the resulting locally built client or authorize implicit pulls.

## Required wiring contract (after independent approval)

1. **Preflight and provenance**: require explicit disposable test opt-in; clean Linux-native Git source; local Unix daemon; pinned PostgreSQL and client-base repository refs cached. Treat metadata-registry outage as NO-GO, unless a separately reviewed signed/offline manifest verification is adopted. Never silently fall back to image config ID, tag, remote context, or an arbitrary cached image.
2. **PostgreSQL run args**: compute `postgres_ref = verified_postgres_runtime_reference()` once before first Docker resource mutation. Use that same immutable `postgres_ref` for BOTH root-CHOWN provisioning and server `docker run`; preserve original PostgreSQL 16.15 index, selected amd64 manifest and config digests in evidence and all original resource isolation flags. Explicit `--pull=never`/supported equivalent is recommended and must be verified for the actual host version, so a missing cache can never trigger a registry pull during `docker run`.
3. **Client base**: only after the separate F-03 controlled rebaseline, use `verified_client_base_runtime_reference()` as the `FROM` reference in the approved test-only Dockerfile. Ensure the reviewed Dockerfile and all 16 exact reviewed wheels/lock hashes are the entire bounded build context; no project code, credentials, symlinks or other unknown sources. Preserve `--network=none --pull=false`.
4. **Locally built client**: independently validate that `--iidfile` is an exact SHA256 reference with acceptable form, verify it resolves locally as linux/amd64, and bind the tested runtime reference to that build result (not to an unrelated local tag). Capture a trustworthy build provenance record linking exact Dockerfile content hash, wheel lock/hashes, pinned base index, build-args/platform and client build identity. A local image ID alone is NOT a supply-chain attestation. Reject mismatch, missing image, ambiguous multi-platform build or unexpected image metadata.
5. **Run lifecycle**: maintain existing original isolation for foundation/restart/B5-4-1 clients and owned-resource cleanup; no Docker socket, network, TCP exposure, external volumes or inherited secrets. Retain historical config digests in evidence even if the runtime ref is a repository index.
6. **Negative test first**: wrong repo/index/manifest/config/platform/local inspect, metadata outage, missing cache, invalid iidfile, changed image ID, no offline wheels, altered build context, build failure, interrupted daemon mutation and cleanup uncertainty must fail closed. Tests of argument formation should assert the exact PostgreSQL ref occurs at **both** run sites and that neither site will pull. Do not start PostgreSQL as part of offline unit tests.

## Important independent-review questions

- OCI verifier currently accepts local `.Id` equal to index, manifest or config digest after remote metadata checks. Reviewer must evaluate whether same local index reference is sufficiently tied to the *selected* platform content in Docker Desktop's image store, and whether local `RepoDigests` and platform inspect should be checked in the final gate. Avoid claiming defense against a malicious daemon.
- Confirm how BuildKit vs classic builder populate iidfile and `docker image inspect` for the exact host. Differentiate image config ID, manifest/index ID, tag and repository digest.
- Check the original lifecycle's trusted-host assumption, volume-provisioner root with CHOWN only, cleanup reconciliation and credential redaction. Integration success does not establish the independent in-process dispatcher security boundary.

## Decision

**GO** for review/refinement of this wiring design and offline mock-based argument tests. **NO-GO** for applying the approved runner/Dockerfile/requirements changes until controlled rebaseline approval and independent security review. NO-GO for any disposable PostgreSQL run until additional explicit permission and readiness evidence. No merge, deployment or B5-4-2.
