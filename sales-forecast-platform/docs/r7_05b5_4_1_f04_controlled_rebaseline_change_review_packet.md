# R7-05B5-4-1 / F-04 — Controlled Rebaseline Change Review Packet

**Decision: REVIEW-READY PLAN ONLY / NOT AUTHORIZED / TESTS NOT RUN / NO-GO.**
Branch: `fix/r7-05b5-4-1-bootstrap`.
This packet does not authorize changing the accepted runner, running Docker/PostgreSQL, merging, deploying, or advancing to B5-4-2. Preserve all earlier F-03/F-04 evidence and the original runner hashes.

## Executive decision

**Proposed boundary:** A narrow, test-only compatibility/security rebaseline, not a rewrite of the platform. The five actual Docker run sites must remain functionally equivalent, offline, isolated, and scoped to a new disposable PostgreSQL cluster. Explicit reviewer approval is required *before* edits to protected runner/Dockerfile/lock. A second explicit operator opt-in is required *before* executing a real database.

## Source-grounded delta and ownership

| Component | Present state | Proposed change only after approval | Acceptance evidence |
| --- | --- | --- | --- |
| `tests/postgres/run_postgres.py` OCI preflight | Bare config-only `POSTGRES=sha256:6a3528...`, strict local `image inspect Id == config` | Verify cached indexed `linux/amd64` repository reference and full index→manifest→config; retain old config in historical evidence | Wrong index, platform, absent cache, registry-metadata failure reject before Docker mutation |
| `run_postgres.py` provisioning/server | Config-only `POSTGRES` used at two separate `docker run` sites; neither has no-pull | Same approved immutable PostgreSQL index in both; explicit `--pull=never` and verified Docker 29 platform behavior | Assert actual emitted argv for **both** sites, no fallback or pull |
| `run_postgres.py` client build | Missing old `BASE`; one psycopg2 wheel; classic builder `DOCKER_BUILDKIT=0` | Bounded verified candidate-v2 16-wheel build context; exact reviewed Python indexed base and full hash lock; explicitly controlled builder/platform | Reject extra/missing/symlinked/changed wheels, wrong lock, unexpected Dockerfile, incorrect iidfile/inspect |
| `tests/postgres/Dockerfile`, `requirements.lock` | Old config-only Python `FROM`, single-wheel hash lock | Rebaseline test-only artifacts with reviewed immutable base and all **16** candidate-v2 wheels; preserve explicit offline installs | Independent license/provenance review; pip hash and dependency closure; no mutable tags |
| `run_postgres.py` foundation/restart | Client image from old builder, no no-pull | Bind three client sites to one verified exact local build result, explicit no-pull for each | Actual argv comparison; non-root, read-only src/tests, socket only, fixed tests |
| `run_b5_4_1_postgres.py` reporting | Existing lifecycle registration before Docker but no explicit no-pull | Preserve registration and exact B5-4-1 tests; use same verified client image with no-pull | Registration/timeout cleanup and selection negative tests |
| Existing cleanup and evidence | Fail closed on unresolved daemon mutation, metadata redaction, owned label deletion, credential unlink | Preserve and strengthen only after individual diff review, no broad cleanup | `CLEANUP_UNCONFIRMED` on ambiguity or unconfirmed secret cleanup; manifest redacted |

## Important discovered mismatch (must resolve in approved real grammar)

The *synthetic* `runtime_run_plan_candidate.py` binds both resource names and label to the same 32-character `run_hex`. The **actual** runner uses a 32-character `run_id.hex` for container/volume names but a **hyphenated UUID** from `str(run_id)` for `r7_05b3_run` labels. The real grammar must bind both to the same underlying UUID by canonical conversion; do **not** paste the synthetic validator into the runner or change the historical label semantics. Negative cases must test wrong UUID values in name/label, especially same-prefix but foreign-owned resources.

## Proposed sequential gated implementation slices

These slices are a **future review/implementation order**, not permission to begin protected edits:

**CR-0 — Freeze and independently approve evidence.** Review exact old/new OCI index, amd64 manifest, config, 16 wheel filenames/hashes, reviewed lock, Dockerfile, SBOM/licenses, Python version, BuildKit behavior. Freeze source commit and old run code digest. If evidence missing, STOP.

**CR-1 — Review pure argv builders and unit tests.** Candidate-only, separate files; stage-specific explicit grammar for all five *real* Docker `run` vectors, with dynamic UUID, generated passwords, scoped volumes/binds, approved `-e` keys, command args and explicit Docker host selection. No Docker commands. Confirm option/image boundary using tested Docker CLI semantics; prohibit Docker socket mount, host network, published ports, unrestricted capabilities, arbitrary paths/selectors. Prove actual UUID label formatting. Keep synthetic prototype separate.

**CR-2 — Controlled test-client artifact rebaseline.** After signed approval, update only test-only Dockerfile, hashed lock and bounded wheel handling; fail-closed validation of exact reviewed artifact set; deterministic offline build and locally inspectable client identity. Do not touch platform production models.

**CR-3 — Controlled real harness wiring.** After approval, use verified pinned reference in both PG run sites, verified local client identity in all three test-client run sites, `--pull=never` everywhere; keep source-provenance check before daemon mutation, narrow root CHOWN stage, original foundation/restart/B5-4-1 selectors, retry/cleanup/credential handling.

**CR-4 — Offline negative and regression tests.** Execute on approved environment before any real disposable DB. Check wrong ref, unsupported CLI options, surprise mount/env, missing wheel, stale Git, timeout/cleanup ambiguity, unexpected secrets, OCI offline behavior, source health and hosted test selectors. No weakening of `_HOST_HEALTH`.

**CR-5 — Separately authorized PC acceptance.** Only after clean Linux-native checkout, image checks, successful tests and **explicit distinct disposable PostgreSQL opt-in**, run PG16.15 foundation + restart + B5-4-1 against *new* test cluster, verify source commit, manifest, server identity, test output and owned resources removed. Any unconfirmed cleanup is NO-GO.

## Reviewer stop conditions and rollback

- **STOP** when immutable image reference cannot resolve locally, Docker 29 CLI no-pull behavior is not established, a wheel hash fails, a package license/provenance is unresolved, an unreviewed environment variable/mount or test selector appears, source is dirty, a daemon operation becomes ambiguous, or a secret cleanup is unconfirmed.
- **Rollback** is a Git revert of *proposed test-only changes* on the branch after documenting failure; never revert historic evidence or modify the dirty Windows source. No Docker prune, forced removal of foreign resources, resetting `_HOST_HEALTH`, suppression of errors, or direct production database connections.
- **Review basis:** show a separate diff for each controlled slice, expected/observed exact digest and stage evidence, negative test report, code-owner approval and status in one immutable review record. Do not aggregate a green pytest run into permission to merge or deploy.

## Deferred offline verification inventory

Currently **92 newly authored F-04 parameterized cases are NOT RUN**, including one synthetic acceptance baseline; previous five runs of 26 selected tests are not substitutes. At PC acceptance, run the new F-04 files plus the existing OCI, provenance, runner safety, reporting and host-boundary regression suites in the isolated no-network client candidate v2, with `-p no:cacheprovider`. Record the exact source commit and per-file collected/passed/failed/skipped counts. A passing simulated test does not establish real Docker daemon cleanup.

### Blockers before any B5-4-1 signoff

Independent security and supply-chain approval; resolution/disposition of prior intermittent `_HOST_HEALTH` terminal result; reviewed controlled-rebaseline of runner/Dockerfile/lock; all offline test outcomes; separately authorized real disposable PG run with verified cleanup.

**R7-05B5-4-1 OPEN / NO-GO.**
