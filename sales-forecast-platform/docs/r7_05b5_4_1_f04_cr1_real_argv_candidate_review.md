# R7-05B5-4-1 / F-04 — CR-1 Real-Shape Docker argv Candidate Review

**Status: CANDIDATE FILES COMMITTED / TESTS NOT RUN / NO-GO.** No Docker, PostgreSQL, trusted runner, Dockerfile, dependency lock, merge, or deployment changes. Not an approved controlled rebaseline.

## Change set

- `tests/postgres/real_run_argv_candidate.py`: pure candidate builder `build_review_argv(ctx)` and exact validator `validate_review_argv(ctx, proposed)`. No external command execution.
- `tests/runtime/test_r7_05b5_4_1_f04_real_argv_candidate.py`: parameterized negative tests for all five stage-specific argv vectors, including no-pull, host network, privilege, UID override, label mismatch, Docker socket mount, mutable image, writeable bind mounts, wrong test selectors, bad image references, noncanonical paths and evidence placement. **NOT RUN**.

### Scope and actual source differences

The review-only builder mirrors the original five run-site shapes, including resource limits, stage mounts, generated test credential field, per-stage nonroot/root limits and expected pytest selectors, but substitutes:
- `docker.io/library/postgres@sha256:<approved-index>` for the old config-only reference at provision/server sites.
- Explicit `--pull=never` and `--platform=linux/amd64` for all five site examples.
- A synthetic SHA256 client image identity in place of the old unapproved build result.

Names use `run_id.hex` (32 hex characters), while Docker ownership labels use `str(run_id)` (hyphenated UUID), as in the original runner. `client_image` is only syntactically checked, NOT independently bound to the reviewed wheelhouse/build. The test-only admin password format is constrained, but the builder is **not an authorization gate**, and generated argv contains a synthetic plaintext disposable password; DO NOT print or publish actual resulting argv/logs.

### Critical limitations / review questions

1. The checked vectors are generated **from the same candidate source** as their validator. Matching them proves no more than conformance to this proposal; it does not establish correspondence to the original runner's actual emitted argv or Docker CLI semantics. Independent reviewer must compare every site with original `run_postgres.py` and `run_b5_4_1_postgres.py`, including flags before image and command options after image.
2. This is not a general Docker argv parser. The candidate admits only its own exact structure by equality. It requires future deterministic builder and extraction from accepted wiring, **after controlled approval**, before it can serve as a real-run acceptance guard.
3. The proposal currently permits a review context value of `root`/ `evidence` without asserting filesystem ownership, path existence, symlink avoidance, file permissions or clean provenance. Those are controlled runtime preflight requirements. No credential is read from the environment.
4. The candidate does not implement complete locally cached client-image provenance, official Python base verification, exact sixteen-wheel file hashes, BuildKit export behavior, original runner integration or accepted no-pull enforcement.
5. No actual Docker execution or image pull has occurred; the real CLI no-pull/platform syntax and support must be checked at the approved PC validation gate.
6. The earlier `_HOST_HEALTH` fail-stop, untrusted same-process Python boundary and ambiguous Docker daemon cleanup remain explicit unresolved blockers. All new tests are **NOT RUN**.

## Next review gate

Execute the new offline pytest file alongside 92 existing F-04 new cases at the agreed B5-4-1 PC checkpoint. Independently inspect the source diff and all untrusted option/mount/env boundaries. A controlled rebaseline approval is still mandatory **before** edits to the accepted test infrastructure; separate opt-in required before any actual disposable PostgreSQL 16.15 run.

**NO-GO for F-04 signoff or B5-4-2.**
