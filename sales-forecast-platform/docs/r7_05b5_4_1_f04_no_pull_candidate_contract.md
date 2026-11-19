# R7-05B5-4-1 — F-04 Candidate-only no-pull runner contract

**Status: committed / NOT RUN / NO-GO. No accepted runner changes.**

## Why this exists

The approved `tests/postgres/run_postgres.py` still uses a bare config-only PostgreSQL digest for two `docker run` sites and contains no explicit `--pull=never` in its run arguments. The old `build_client` still consumes an obsolete base and one driver wheel. Successful OCI metadata inspection does **not** replace safe argument formation or establish runtime image identity.

## Candidate-only proposed contract

`tests/postgres/runtime_run_plan_candidate.py` provides a **pure validator**, `validate_candidate_run_plan(plan, verified_postgres_ref, client_image_id)`. It does not construct/run commands, call Docker/registries, modify production sources, or grant approval.

The validator requires:
- Exactly the validated pinned PostgreSQL repo-index reference in both provision and server argv, and a syntactically exact SHA256 local client build identity on every test-client stage.
- Explicit `--pull=never`, `--network=none`, `--read-only`, `--cap-drop=ALL`, `--security-opt=no-new-privileges` on every proposed Docker `run`.
- Scope-marked disposable name/label; no publish/host network/privileged/pull-always options.
- A narrowly bounded root CHOWN-only provisioning exception, server UID 999 and test-client UID 1000.

**Caveat:** The validator is intentionally minimal and only suitable as a negative test guard for future reviewed argv plans. It does not prove exact Docker argument semantics, trusted source commits, uid correctness of an actual image, ownership UUID consistency, benign arguments after image, daemon availability, runtime no-pull support or image integrity. It does not inspect the original runner's real arguments; *runner wiring is still absent*. A later independent review must address these conditions before any accepted harness change.

## New negative tests

`tests/runtime/test_r7_05b5_4_1_f04_run_plan_candidate.py` contains 26 parametrized cases: one positive baseline, three missing-no-pull cases, three altered-image sites, five forbidden-option cases, four client-isolation cases, four user/provisioning cases, four invalid client image IDs, one missing site and one wrong PostgreSQL index. All tests are **NOT RUN**; no result is implied by being committed.

Together with the previous 40 unrun F-04 additions, **66 new test executions are pending validation**, including the one positive baseline. This is not 66 successes and not 66 distinct scenarios.

## Review and acceptance gates

1. Read-only independent review of the validator, argv semantics and all new offline tests. Execute targeted offline pytest in a suitable environment at the milestone validation checkpoint.
2. Do not use the validator as an approval bypass: a controlled rebaseline must separately demonstrate verified and runnable immutable index refs and no-pull flags for **both** PostgreSQL containers and all clients; a verified 16-wheel offline image build and explicit runtime identity binding; negative tests on actual wired arguments.
3. Resolve/record the intermittent `HostHealth` fail-stop result; independently examine sensitive dispatcher in-process exposure, cleanup and redaction, artifacts/licenses and supply-chain evidence.
4. Real disposable PostgreSQL 16.15 tests require separate explicit opt-in and own-resource cleanup evidence **before B5-4-1 signoff**.

**R7-05B5-4-1 — OPEN / NO-GO. No PostgreSQL integration, accepted runner edits, merge, deployment or B5-4-2.**
