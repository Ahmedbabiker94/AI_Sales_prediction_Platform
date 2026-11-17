# R7-05B5-4-1 / F-04 — Real Runner Wiring Contract (review proposal only)

**Status: SOURCE REVIEW COMPLETE / PROPOSAL ONLY / NOT IMPLEMENTED / NOT RUN / NO-GO.**

This design supplements, and does not supersede, `r7_05b5_4_1_f02_runner_wiring_review.md`, `r7_05b5_4_1_controlled_rebaseline_implementation_proposal.md` and the F-04 adverse-case reviews. It is **not** permission to change the accepted runner, Dockerfile or lock; invoke Docker or PostgreSQL; merge/deploy; or advance to B5-4-2.

## Verified source inventory on fix/r7-05b5-4-1-bootstrap

- `tests/postgres/run_postgres.py`: `BASE = sha256:417063...` (unavailable old base), `POSTGRES = sha256:6a3528...` (config digest, not immutable full registry ref), `inspect_image()` requires exact `image inspect Id == identity`, `build_client()` copies one `psycopg2_binary` wheel plus original `Dockerfile`/`requirements.lock`, and selects `DOCKER_BUILDKIT=0` via the build command.
- Original `run_postgres.run()` has **four** distinct `docker run` construction sites: (1) root CHOWN provision, (2) PostgreSQL server, (3) foundation client, (4) restart client. An extra client site (5) exists in `run_b5_4_1_postgres.reporting_stage()`. The `--pull=never` flag does not appear on those current run constructions.
- `run_postgres.command()` prepends `docker --host=unix:///var/run/docker.sock` and registers named create/start/restart and volume create attempts before spawning the CLI. The finalizer keeps `CLEANUP_UNCONFIRMED` for ambiguous daemon outcomes, missing cleanup certainty, secret redactions or failed credential-file removal; it intentionally does not prune.
- Existing source provenance checks refuse unclean Git source before resource mutation. `run_b5_4_1_postgres.reporting_stage()` registers the named B5-4-1 container with the active original lifecycle before calling Docker.
- Existing client Dockerfile uses the unavailable config-only Python base; current lock has just `psycopg2-binary==2.9.12`. The reviewed *candidate v2* on the operator PC instead used official Python 3.11.18 slim-bookworm indexed base, 16 offline hashed wheels including pytest 9.0.3. That evidence does not mean the old client build was updated.

## Design contract to review before controlled rebaseline

### A. Pin, platform and no-pull

1. Once a separate security reviewer approves the base/index/manifest/config chain and locally cached image identity, request `verified_postgres_runtime_reference()` exactly once at authorized preflight. It must return the fully qualified `docker.io/library/postgres@sha256:e8a07d2c7579b038938cdba6c539e580e5001caf9ea203d397b5cc83e76bd938` only when a matching **linux/amd64** image exists locally and the approved registry metadata chain is intact. No fallback to `postgres:16.15`, unqualified config ID or mutable local tag.
2. Use precisely that same reference in **both** PostgreSQL `run` sites, with `--pull=never` before the image and platform explicitly `linux/amd64` (subject to verifying Docker 29 syntax on the host). Neither stage may trigger implicit image pulls.
3. Verify the candidate official Python base from `docker.io/library/python@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39`, with its selected manifest/config identities. Rebaseline the dedicated test-only Dockerfile *only after approval*; do not change production model/runtime image references.
4. Build the test-client from the **entire** reviewed candidate-v2 wheel set and strict hash lock in a bounded context. Reject missing, extra, changed or symlinked wheel files and stale locks. Disable network and pulls for the build; establish a reproducible local build identity and inspect platform and digest. BuildKit `iidfile` and `docker image inspect Id` require host-specific verification; a local digest or tag by itself does not prove package provenance.
5. For the **three** clients (foundation, restart, B5-4-1), reuse only the verified local built-image identity; each client `run` must carry its own explicit `--pull=never` and never fall back to an arbitrary local tag.

### B. Exact real-argv formation

Define a trusted, deterministic **builder** for each distinct site, then compare its emitted arguments against a reviewed stage-specific grammar; do not accept arbitrary caller-supplied flags. In contrast with the current *synthetic* exact-match prototype, the real grammar must include the original precise mounts, env source, limits, entrypoint and command payloads:

| Stage | Identity and execution | Minimum preserved restrictions |
| --- | --- | --- |
| Provision | pinned PostgreSQL index; root `0:0` strictly for `chown 999:999 /var/lib/postgresql/data` | disposable owned data volume only; `--cap-drop=ALL --cap-add=CHOWN`; no host mounts, network or published ports |
| PostgreSQL server | same pinned index, `--user=999:999` | disposable data + UNIX socket volumes only; `listen_addresses=`; approved `PGDATA`, DB name and generated test password; read-only root FS, tmpfs, memory/cpu/pid caps |
| Foundation | verified built client, UID/GID `1000:1000` | read-only socket, read-only project src/tests, scoped generated envfile; exact foundation pytest module, nonroot isolation |
| Restart | verified built client, UID/GID `1000:1000` | same scoped assets; exact restart module, durability check and same server/cluster |
| B5-4-1 | verified built client, UID/GID `1000:1000` | same scoped assets; exact three B5-4-1 test modules from `run_b5_4_1_postgres.TESTS` |

All five sites require explicit no-pull; no `--privileged`, host network, published port, Docker socket, unapproved bind/volume, root client/server override, extra capability, duplicate contradictory option, or arbitrary Python/pytest module selection. Bind `--name r7-05b3-<run_hex>-<stage>` and the label `r7_05b3_run=<uuid-string>` to the **same actual UUID** (hex for names; hyphenated UUID form for the label). Strictly distinguish Docker run flags **before the image** and expected image command arguments **after the image**. Reject split/equals flag aliases unless each form is deliberately supported and tested.

The original `command()` Docker socket selector is a trusted-host host-side CLI connection, **not** permission to mount the socket inside containers.

### C. Mutation ownership, cleanup and evidence

- Preserve the existing clean Linux-native Git checkout/provenance gate and explicit operator opt-in before any mutation.
- Register each possible daemon creation **before** invocation, including extra B5-4-1 container; label ownership must be checked before **every** destructive cleanup. On command timeout, interruption, rejected deletion, delayed appearance, failed listing or credential/sensitive metadata scrub, reject the run as `CLEANUP_UNCONFIRMED`. Never adopt a no-resources-now observation as proof an ambiguous daemon request cannot complete later.
- Keep randomly generated test-only credentials out of source, logs and stored evidence; temporary env file must not remain. Treat redaction as a detectable rejected-run event, not as permission to mark PASS.
- Preserve old config identities for historical audit; record exact approved runtime ref, platform, client build identity, Dockerfile/lock/wheel hashes, source commit, selected modules and final owned-resource cleanup outcome. Do not claim that a locally constructed SBOM/hash list proves publisher provenance or licensing.
- Keep the known `_HOST_HEALTH` intermittent fail-stop finding and in-process Python boundary limitations visible as unresolved items.

## Offline negative matrix required before any runner edit

1. Both PostgreSQL run sites must reject old config-only ref, wrong index, wrong platform, missing cache, automatic pull and tag substitution.
2. Every client site must reject omitted `--pull=never`, mismatched build identity, UID override, writeable project mounts, Docker socket/host mount, TCP/published network, foreign/unscoped resource name and wrong test selectors.
3. Builder rejects duplicate/split conflicting options, dangerous capabilities, commands following the image that escape approved test selector, unsafe environment inheritance, and unexpected build-context files.
4. Preflight fails before mutation for any missing provenance, mismatched Dockerfile/lock/wheel, registry metadata outage under the chosen verification policy and dirty checkout.
5. Lifecycle mock tests reject inconsistent UUID name/label, ambiguous mutation, creation-after-timeout reconciliation, failed inspect/delete, delayed resources, vanished credentials, and any leaked test-secret evidence.
6. Run the whole existing offline regression suite plus F-04 tests after reviewer approval; do not change accepted runner to satisfy a test without its own diff review. Preserve the first-run intermittent HostHealth failure for investigation.

## Explicit decisions needed for next gate

- Independent reviewer must accept the exact Docker 29 CLI semantics for platform/no-pull and the full real stage-specific argument grammar, including the root-only CHOWN exception and post-image argument boundaries.
- Independent supply-chain reviewer must sign off on candidate-v2 hashes, licenses, metadata and scope; zero OSV advisories at a given time is not a blanket security assurance.
- Reviewer must approve a **controlled rebaseline** change set for test-only `run_postgres.py`, `run_b5_4_1_postgres.py`, Dockerfile, lock and associated tests, separately from any authorization to run Docker/PostgreSQL.
- Only **after** that controlled review, run offline and then separately opt-in to an isolated **real disposable PostgreSQL 16.15** test at the B5-4-1 checkpoint, examine cleanup manifest, and obtain B5-4-1 signoff before B5-4-2.

**This document does NOT authorize or implement the real runner rebaseline. R7-05B5-4-1 OPEN / NO-GO.**
