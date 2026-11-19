# R7-05B5-4-1 / F-04 — CR-1 Independent Source-Parity Review

**Status: REVIEW TESTS COMMITTED / NOT RUN / NO-GO.** This is a focused source-based review and does not constitute third-party independent security signoff or runtime approval. The original runners, Dockerfile, lock and OCI pins remain unchanged.

## What was added

`tests/runtime/test_r7_05b5_4_1_f04_cr1_independent_source_parity.py` defines **21 parameterized pytest executions**, using the original accepted `run_postgres.py` and `run_b5_4_1_postgres.py` files as independent AST/reference sources rather than calling `validate_review_argv()` from the candidate itself.

It checks:
- Original foundation/restart selector literals and original B5-4-1 `TESTS` tuple.
- Exact post-image `pytest` selectors and command arguments for foundation, restart and B5-4-1; no Docker mount/env options after the client image.
- Explicit no-pull/platform/isolation flags before the image at all five proposal sites; the proposed no-pull flags are not asserted to exist in the accepted runner.
- Correct `run_id.hex` resource names and `str(run_id)` hyphenated UUID labels at every site.
- Exact read-only socket and source/test bind destinations for each client; no container Docker socket.
- Root CHOWN-only provision command and scoped data volume; PostgreSQL server nonroot, data/socket-only mounts, UNIX-socket-only listening.
- Proxy variable names, sourced independently from the accepted runner's AST, and the candidate's network-isolation environment clearing.
- Absence of any runner or Docker invocation in the pure candidate module.

The accepted runner itself remains incompatible with the candidate indexed/no-pull runtime. This file proves neither that `run_postgres.py` emits these new flags nor that Docker would interpret the argv as intended.

## F-04 tally clarification

The previously documented **147** new cases (92 F-04 cases plus 55 real-shape CR-1 candidate cases) now have **21** additional source-parity test executions, for **168 newly authored F-04/CR-1 test executions pending validation**. This counts parametrized pytest executions; it does **not** mean 168 tests passed, and it includes positive baselines.

## Source review notes and risks

1. The candidate uses a synthetic `client_image` SHA256 syntax and synthetic disposable password; local image provenance, exact reviewed 16-wheel hashes and real credential handling remain unverified.
2. Proposed `root` and `evidence` string validation cannot prove real filesystem ownership/absence of symlinks or Git cleanliness; those are runtime provenance gates.
3. `docker --host=unix:///var/run/docker.sock` is an intended trusted host-side selector in the accepted `command()` function; it must not become a container mount.
4. The candidate's source-based AST comparison verifies literal original selectors and preserved proposal boundaries. It does not validate every dynamic Docker flag or all file paths; a future reviewer must compare all actual output vectors with a separately authored golden specification and Docker 29's parser semantics.
5. The proposed PostgreSQL server command contains a generated disposable test password as an argv environment argument, as the original harness does. Do not print/store such live argv in logs, code review artifacts or evidence. The host Docker daemon is trusted and the attacker model does not protect against a malicious host.
6. The first combined reporting/host-boundary test's intermittent `_HOST_HEALTH` failure remains unresolved. Passing additional test files does not erase it.

## Deferred verification and signoff

Execute the added offline AST/CR-1 tests along with all existing F-04 cases on a reviewed, clean checkout using the approved isolated Python 3.11 candidate-v2 client image when the operator proceeds with the PC checkpoint. Record actual collected/pass/fail counts and source commit. Independent review of third-party package licenses and provenance, OCI runtime/cache/no-pull behavior, cleanup and boundaries still required. The accepted runner/Dockerfile/lock cannot be changed until separately authorized controlled rebaseline; actual disposable PostgreSQL 16.15 needs a further separate opt-in before B5-4-1 signoff.

**R7-05B5-4-1 / F-04 OPEN / NO-GO. No DB execution, merge, deployment or B5-4-2.**
