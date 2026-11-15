# R7-05B5-4-1 — Controlled Rebaseline Implementation Proposal

**Decision: REVIEW REQUIRED / NOT APPROVED / NO-GO.** This is a proposed
minimal test-infrastructure change set, **not** an implementation authorization.
The accepted `tests/postgres/Dockerfile`, `requirements.lock`,
`run_postgres.py`, `run_b5_4_1_postgres.py` and preflight remain unchanged.
Do not run the database harness until the independent review gates below pass.

## Business summary

The separate Python client needed for the disposable PostgreSQL 16.15 tests
was originally pinned by a config SHA-256 whose registry source is unavailable.
An exact digest-pinned, official Python 3.11.18 slim-bookworm candidate has now
been downloaded, built with 16 hashed offline wheels, and exercised on the
trusted Ubuntu WSL2/Docker Desktop 29 host. This establishes *candidate*
compatibility, **not** approval. A separate compatibility issue exists because
Docker's containerd image store returns an OCI index ID where the old harness
expects a config ID. Both need a narrowly scoped, reviewed change.

## Recorded host observations — candidate test evidence, not DB proof

- OS: Ubuntu 26.04.1 WSL2, `linux/amd64`; system Python 3.14.4 was left
  intact. Independent uv-managed Python 3.11.18 staging, pip 26.3.
- Candidate base: `docker.io/library/python@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39`.
  Registry linux/amd64 manifest:
  `sha256:e8c69d86735f6df3efa06b324694fe51f0422785f83a3bd463052636c2421944`.
  Manifest config:
  `sha256:cf2dad23925ed51a00735f57606b2998aedf4705a1634cb9ae1053b5c145d5ba`.
  Local Docker inspect reported index ID plus `linux amd64`; repo digest
  matched this index. Image-reported user empty (= root default), no
  entrypoint, default command `python3`.
- Debian Bookworm base inside a `--network none --read-only --user 1000:1000
  --cap-drop ALL --security-opt no-new-privileges` temporary client container
  reported glibc **2.36**; tested CPython runtime under UID 1000.
- `~/b5-4-1-candidate-wheelhouse`: 16 wheels; 16 candidate lock lines; 16
  SHA-256 entries; independent wheel-hash verification PASS.
- `pip --dry-run --ignore-installed --no-index --require-hashes` PASS;
  actual offline installation in dedicated Ubuntu staging PASS; `pip check`
  PASS; project contract and host-reporting imports PASS.
- Separately built local disposable candidate image:
  `b5-4-1-client-candidate:local`, built with `docker build --network=none
  --pull=false --platform linux/amd64`, using immutable base and all 16
  local wheels + hashed lock. Build config:
  `sha256:917774e30ed62ad4a5e08bfce066476b6fb20b871727846b369e29a9e0c07e71`.
  Image-index/manifest-list exported by BuildKit:
  `sha256:abdeb8d49f310d6f81b625cc571f1ea748ca98e574db394a7bdf6849961803b3`.
  Offline install + `pip check` build stage PASS. Temporary runtime
  container imported pytest 8.4.2, pydantic 2.13.3, pandas 2.3.3,
  numpy 2.4.4 and psycopg2 2.9.12 under UID 1000 with no network;
  read-only source bind test imported `CanonicalForecastStorageBatch`
  and `build_host_reporting_requests` successfully.
- Original PostgreSQL identity is **unchanged**: repo index
  `sha256:e8a07d2c7579b038938cdba6c539e580e5001caf9ea203d397b5cc83e76bd938`,
  amd64 manifest
  `sha256:cf5d353f55240ecc2f811827a15c7d1f2d562f08019b49dca12b5a59f4ab5962`,
  config
  `sha256:6a35283e53fbe883da6a6ffbce8d3999dc66076cc5c4e2f1d21bdbd9e26bf86f`.
  `verify_postgres_oci.py` PASS on user's host. Original config-ID-only
  `inspect_image` and `docker run POSTGRES` are NOT proven compatible.
- **No actual PostgreSQL integration run**. The named candidate client image
  and wheelhouse are on the user's machine, not uploaded and not an approved
  reproducible registry artifact. A local mutable tag is never trusted identity.

## Proposed scoped change set — ordered, review each before implementing

### 1. Freeze reproducible, reviewed client artifacts

Keep existing original test-client config ID
`sha256:417063b71e35c7a9889c10ac2516c328f16a483e065ec858df101eb9728c2df2`
visible in migration/rebaseline evidence; never quietly rewrite the historical
record. Confirm the official base's exact index->amd64 manifest->config
chain and its source revision independently. Review provenance, package
licenses, wheel metadata (including dependencies), architecture, and
integrity. Retain all 16 **exact wheel filenames, versions and verified
SHA-256 values** in a proposed separately named lock and signed/verified
artifact inventory. Do not use mutable tags or unverified local pip cache.

### 2. Proposed test-client-only Docker build

Only after approval, change `tests/postgres/Dockerfile` to use the immutable
**repository digest** of the reviewed official base, not the missing
config-digest-only `FROM` or a mutable tag. Stage the full reviewed offline
wheelhouse and proposed lock (do not reuse `requirements.lock` until the
transition is explicitly approved). Preserve `--no-index`,
`--require-hashes`, `--network=none`, `--pull=false`, version checks,
and `pip check`. A candidate Dockerfile is not a production Dockerfile.
Set the runtime to UID/GID 1000 or retain the explicit original
`--user=1000:1000`; never relax that runtime flag. Review source-bind and
wheel-file permissions under the non-root client.

### 3. Split image identity from Docker runtime reference

Replace the **test-only** assumption `inspect_image(identity)['Id'] ==
config_digest` with a verified identity model containing:
`registry index`, selected `linux/amd64` manifest, config digest, local
inspect result, and an **immutable locally runnable image reference**.
Compare exact pinned values and OCI media types; verify single unambiguous
amd64 entry. Do not treat any arbitrary local .Id as valid. The already added
`verify_postgres_oci.py` can inform the verifier but cannot authorize a run
by itself; it reads remote immutable registry metadata and fails closed on
outage. Define explicitly if the final opt-in harness requires network for
the registry-metadata preflight or consumes independently secured local
manifest evidence instead. Database build/run must remain offline and must
never pull an image implicitly.

Every Docker `run`, `build FROM`, `restart` and `inspect` path must use
a tested reference that Docker Desktop 29/containerd can actually resolve.
Preserve the old PostgreSQL config SHA-256 in evidence while using the
reviewed immutable repo-index reference for runtime commands where necessary.
In particular the original `POSTGRES` constant is used not only by
`inspect_image`, but also for the provisioning and server `docker run`
commands. Confirm both are handled. The built test-client image's inspect
identity may be its index digest rather than BuildKit's config digest.

### 4. Enforce complete artifact and source provenance

Replace original one-wheel copying in `build_client` with strict, bounded,
safe-file verification of all required wheels against the reviewed lock;
reject symlinks, duplicate distributions, unexpected wheels, missing hashes,
architecture mismatch, and unsigned/unreviewed inventory. Capture source
commit, original approved digests, all candidate base/build digests, all
wheel hashes and test stage identity in **redacted** run evidence.

The Windows-created Git worktree has a Windows-style `.git` pointer
(`C:/Users/...`) and fails under WSL Git. Prepare a clean independent
Linux-native checkout in `~/...` for runtime provenance. Do not edit Windows
worktree Git metadata or overwrite dirty original production-model files.
Require checked `git rev-parse` and an **empty** `git status --porcelain`
before first Docker mutation. Existing original `run_postgres.py`
records these values but does not demonstrate that a dirty source is rejected
before execution; add and negative-test that gate.

### 5. Preserve resource isolation and lifecycle

No TCP/published ports, network access, inherited database credentials,
Docker socket mounts, operational model imports, or external/production
data mounts. Keep non-root clients, read-only filesystem, no-new-privileges,
cap-drop, tmpfs, resource limits, exact original test selectors and original
PostgreSQL 16.15 guards. The original **root CHOWN-only** provisioning step
must remain narrowly reviewed with `--cap-add=CHOWN` and disposable
owned-volume scope. Never substitute a broad Docker cleanup/prune.

Keep lifecycle ownership registration before daemon mutations, bounded
reconciliation and `CLEANUP_UNCONFIRMED` on ambiguous daemon outcomes.
Maintain transient test-credential file removal and redaction; never copy
plain-text test secrets into evidence or GitHub. Reassess cleanup and
secret-redaction controls independently.

### 6. Acceptance verification and review gates

Run a test matrix of **negative** cases first: wrong index, wrong manifest,
wrong config, wrong platform, no local image, registry metadata unavailable,
changed wheel hash/filename, extra/duplicate/symlink wheel, missing transitives,
stale/dirty Git checkout, unresolved Docker daemon mutation, interrupted
provision/start/run, credential cleanup failure, and missed resource cleanup.
Then independent CI for Python 3.11/3.12 host-only tests; client build/import
evidence is separate from DB integration. Independent security review must
address the existing **in-process dispatcher reference disclosure**: do NOT
claim hostile plugin isolation even if every DB test passes.

Only after explicit reviewed controlled-rebaseline approval and user's
separate opt-in, run the original foundation + restart + B5-4-1 tests against
**new disposable PostgreSQL 16.15** via the source-pinned harness; inspect
`run-manifest.json`, tests, redacted evidence and exact owned-resource
cleanup. No merges or B5-4-2 until all acceptance checks are independently
verified.

## Recommendation / current gate

**GO** to independent review of this *implementation proposal* only.

**NO-GO** for modifying the accepted pins, running the integrated database
harness, merging, deployment or closing R7-05B5-4-1. Candidate Docker
imports do not establish database correctness or fix in-process privilege
boundaries.
