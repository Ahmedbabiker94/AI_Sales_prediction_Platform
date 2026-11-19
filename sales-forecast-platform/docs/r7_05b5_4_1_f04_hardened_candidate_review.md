# R7-05B5-4-1 — F-04 hardened synthetic no-pull proposal (review record)

**State: CANDIDATE HARDENED / TESTS NOT RUN / NO-GO.**

## Change set on fix/r7-05b5-4-1-bootstrap

- `tests/postgres/runtime_run_plan_candidate.py`: replaced permissive presence-only flag checks with exact ordered synthetic argv vectors for five named stages: root CHOWN provision, nonroot PostgreSQL server, nonroot foundation/restart/B5-4-1 clients. All names and labels bind to one lowercase 32-hex `run_hex`. Exactly one pinned PostgreSQL repository index is required on both PostgreSQL stages, and only a syntactically valid exact SHA256 candidate client ID is accepted for all client stages. Mandatory no-pull, network isolation, read-only, cap drop and no-new-privileges; rejects *all* additional/reordered/duplicate flags and tokens, including conflicting split forms, mounts, Docker socket exposure, unsafe user override and post-image arguments. This validator has no executable side effects.
- `tests/runtime/test_r7_05b5_4_1_f04_run_plan_candidate.py`: existing 26 parametrized cases switched to synthetic run UUID binding, still unrun.
- `tests/runtime/test_r7_05b5_4_1_f04_run_plan_adversarial_audit.py`: **converted nine earlier vulnerability demonstrations into rejection regressions**. They must now fail if the candidate checker accepts the adversarial argv; no successful test may be interpreted as permitting insecure arguments.
- `tests/runtime/test_r7_05b5_4_1_f04_run_plan_strict_edges.py`: 17 more negative cases for malformed run UUID, mismatched name, swapped/missing stages, conflicting/unauthorized options, and an internally constructed baseline sanity assertion. Unrun.

### Count

Earlier 75 F-04 candidate test executions included the nine adversarial tests that deliberately demonstrated weakness. Those nine have been converted to positive assertions of rejection. The latest edge file contributes 17 additional test executions. **92 newly authored F-04 test executions are now pending**, counting the positive baseline and 91 negative/rejection scenarios. This is only a test inventory, not a pass/fail result.

## Important scope restriction

The *canonical synthetic* argv vectors are not the actual database integration commands: they deliberately omit the real approved harness's volume mounts, temporary environment variables, resource limits, user-selected test arguments and entrypoints. Their exact-match validation is a **safety prototype and regression exercise**, not a production Docker argv contract. It cannot be pasted into the accepted runner or used as authorization. The actual reviewed integration must define per-stage allowed mounts/volumes, env redaction, limits, reference positioning, distinct root provisioning, disposal ownership and explicit no-pull for all real run calls. It must bind a verified immutable local client image identity to the reviewed 16 wheel files, lock, Dockerfile, official Python OCI base, build process and platform.

## Blocking acceptance gates

1. **NOT RUN**: All new F-04 test suites need real pytest collection/execution and code review on an available environment. Do not change accepted code to make tests pass.
2. **Accepted runner unchanged**: the original PostgreSQL config-only references and old client one-wheel Dockerfile/lock remain; no secure runner wiring is approved. More than a synthetic flag validator is required.
3. **Unresolved prior evidence**: an early combined reporting/host-boundary test run entered terminal `HostHealth`; five subsequent independent full runs passed. Root cause and security implication remain formally unresolved.
4. **Independent supply-chain/security signoff**: source-to-image and package provenance, license obligations, cleanup/daemon ambiguity, redaction and trusted-host boundary review.
5. **PC integration**: only after controlled rebaseline approval and separate explicit opt-in, use a clean Linux-native checkout to run actual disposable PostgreSQL 16.15 and verify manifests and complete cleanup. Required before closing B5-4-1.

**Decision: R7-05B5-4-1 OPEN / NO-GO. No real Docker/PG run, runner/Dockerfile/lock edits, merge, deployment or B5-4-2.**
