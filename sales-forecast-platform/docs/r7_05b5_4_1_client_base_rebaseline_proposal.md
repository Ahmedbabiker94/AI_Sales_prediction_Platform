# R7-05B5-4-1 — Test-client base image Controlled Rebaseline proposal

**Status: PROPOSAL ONLY — NOT APPROVED.** No change to the accepted Dockerfile, Docker/image pins, harness, PostgreSQL schema, source imports, or test selection. No real PostgreSQL test run is evidenced here. PR remains draft.

## CEO-level reason

The isolated PostgreSQL test requires a precise, pre-approved Python test-client image. The old image is absent on the trusted Windows Docker Desktop/Ubuntu WSL2 host, and its registry source cannot be reconstructed from retained B3 documents. Silently replacing it would weaken reproducibility. We need a newly reviewed, independently verifiable image and the same isolated test behavior.

## Proven baseline and candidate

- Existing pinned test-client **config** SHA-256: `417063b71e35c7a9889c10ac2516c328f16a483e065ec858df101eb9728c2df2`. The client `tests/postgres/Dockerfile` uses `FROM sha256:...`. The original B3 document explicitly notes that this base's supply-chain limitations remain and does not state a registry/repository digest.
- **Candidate for review, not approved:** `docker.io/library/python:3.11-slim-bookworm`, a Docker Official Image intended for Debian Bookworm and CPython 3.11. Official catalog: https://hub.docker.com/_/python/tags?name=3.11-slim-bookworm and official source manifest: https://github.com/docker-library/official-images/blob/master/library/python . Its TAG IS MUTABLE. Do **not** use this tag in an approved Dockerfile.
- A qualified reviewer must capture a full immutable repository-index digest, the exact linux/amd64 platform manifest digest and its config digest at one inspection moment; verify OCI media types and local presence; then record all three values and source provenance. No digest is proposed as approved in this document.
- The verified wheel remains `psycopg2_binary-2.9.12-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.whl`, SHA-256 `e4e184b1fb6072bf05388aa41c697e1b2d01b3473f107e7ec44f186a32cfd0b8`. Preserve the `--no-index --no-deps --require-hashes` offline-install requirement.

## Compatibility gap that MUST be closed first

The currently accepted test client is expected to run the PostgreSQL pytest modules. The old Dockerfile only installs **psycopg2-binary**, and the base-image source and installed packages are unknown. An unmodified official Python image does **not** establish that the pytest runner or other Python imports required by these tests exist. Replacing `FROM` alone is **not sufficient**.

Before any approval:

1. Enumerate exact imports across foundation, restart, B5-4-1 and retained contract modules, including dependencies transitively needed for tests. Do not import operational/native or model-fitting code during enumeration. Verify the exact CPython ABI and supported wheel platforms.
2. Build a **separate reviewed dependency lock/wheelhouse** (e.g., pytest and required transitives) containing exact version and SHA-256 for every package; verify software licenses and provenance. Preserve offline install and `--network=none` for the original test build. No unreviewed online `pip install`.
3. Independently audit the candidate base's interpreter, executables, non-root runtime, absence of unexpected entrypoints/network prerequisites, and availability of minimal OS libraries. The build must remain compatible with the read-only, rootless, network-none, no-port, no-Docker-socket, tmpfs-isolated test runtime.
4. Handle Docker Desktop 29/containerd's different reporting of OCI **index** versus config ID without accepting arbitrary local image IDs. The standalone `verify_postgres_oci.py` is only read-only identity evidence for **PostgreSQL**, not authorization to run the original harness. Any future runner change must bind the immutable remote index -> amd64 manifest -> expected config and a locally usable reference for both images, with offline mismatch tests.
5. A trusted reviewer must inspect the original `tests/postgres/run_postgres.py` execution and cleanup lifecycle, including credential redaction and any resource mutations. Windows-created Git Worktrees may have `.git` files pointing at `C:/...` that fail under WSL Git; the original harness records `git status` and a source commit. Require a **clean Linux-compatible checkout** for real evidence, never edit Windows Git internals or pretend a failing Git command is evidence.
6. Run negative tests for changed digest, architecture, missing manifest, absent offline wheels, import failures, ambiguous daemon creation and cleanup uncertainty. Ensure fail-closed semantics. Reviewer approval of a new supply-chain baseline is **separate** from acceptance of the service host/in-process dispatcher trust boundary.
7. Only after explicit approval, make a scoped implementation change on this B5-4-1 branch, run independent CI, run opt-in disposable PostgreSQL on the trusted host, inspect provenance/evidence/owned cleanup and repeat negative tests. No deployment, merge, B5-4-2, or migration expansion beforehand.

## Read-only candidate metadata query (Ubuntu WSL2)

This queries registry metadata but does **not** pull images or create containers:

```bash
docker buildx imagetools inspect docker.io/library/python:3.11-slim-bookworm
```

The output may show multiple architectures and an OCI index digest. Before using a candidate, inspect the **digest-pinned** linux/amd64 manifest with `--raw` to recover config digest; never infer a config digest from a mutable tag or from a truncated UI string. Capture the exact full digests in the security review record.

## Current decision

- Approved PostgreSQL 16.15 index/manifest/config identity: independent read-only verifier PASS on the user's trusted WSL2 host; the original harness still uses config-digest lookup and is not yet compatible with Docker Desktop containerd.
- Existing psycopg2 2.9.12 wheel: SHA-256 verified on host.
- Exact original client base config digest: **missing**.
- New base image: **candidate only**, not verified/approved.
- Real PostgreSQL execution/cleanup evidence: **not yet obtained**.
- Independent security/trust-boundary approval: **pending**.

**R7-05B5-4-1 = OPEN / NO-GO.**
