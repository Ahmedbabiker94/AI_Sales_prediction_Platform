# R7-05B5-4-1 / F-04 — Protected-to-fork operational AST parity review

**Date:** 2026-11-17. **Status: INTERNAL SOURCE REVIEW COMPLETE / INDEPENDENT SECURITY APPROVAL PENDING / NO-GO.**

## Frozen source and tested revision

- Accepted runner: `tests/postgres/run_postgres.py`, Git blob **`0863e81b733381a80b2f83560d8bbc231513ba41`** (unchanged; expected `0863e81b733381a80b2f83560d8bbc231513ba41`).
- Review-only fork: `tests/postgres/run_postgres_security_patch_candidate.py`, with an unconditional default-deny `command()` guard that must NOT be copied into operational code.
- GitHub Actions F-04 run **44138715353**, head `38468bd7091dc3323a3535efbed8fc4b80517b59`.
- Python **3.11: 233 passed / 1 failed** (234 collected); Python **3.12: 233 passed / 1 failed** (234 collected).
- The one failure in both matrices remains the frozen runner's unmodified `unexpected_manifest_secret` regression; **do not xfail or exclude**. Passing diagnostic counterexamples still intentionally demonstrate flaws in the accepted runner.

## Six newly executed AST-level source parity checks

`tests/runtime/test_r7_05b5_4_1_f04_fork_ast_operational_parity.py` uses `ast.parse` over both full sources and compares structural expression trees, rather than trusting builder/validator code from the same candidate.

1. Every original `command(...)` call expression inside `run()` remains byte-independent AST-identical and equal in multiplicity inside the fork, irrespective of enclosing indentation.
2. Helper function bodies for `inspect_image`, `remove_owned`, `build_client`, `remove_transient_credential_file` and `verified_clean_source` are AST-identical between source and fork.
3. Every direct `remove_owned(...)` and `remove_transient_credential_file(...)` call in `run()` remains structurally present and identically parameterized.
4. The accepted runner does not import or invoke the candidate finalizer. The fork invokes that function exactly once.
5. The review-only fork has no `__main__` CLI entrypoint.
6. The constants `BASE`, `POSTGRES`, `WHEEL`, `WHEEL_SHA256` and the fixed literal `tests/postgres/...` test selector strings match.

## Critical limitations of structural parity

AST agreement does **not** prove equal control flow, exception precedence, cleanup timing, worker isolation, daemon quiescence or runtime Docker argv placement. Specifically the candidate deliberately changes the span of the active `_LIFECYCLE` ContextVar to cover more of `finally`. That affects lifecycle registration and timeout logic, and needs an independently reviewed behavior-level test plan. The original `command` helper body is retained behind a hard stop in the fork, but that guard is **only an accidental execution prevention measure**, not a sandbox.

The candidate and accepted code are both run under mock-only contexts; this review **did not** run Docker, PostgreSQL, a wheelhouse verification, or OCI provenance checks. Nor did it produce a human independent signature or approve replacement of the frozen runner.

## Next gate before protected file modification

1. Independent reviewer examines exact diff including early setup ContextVar restoration, SIGTERM restoration failure precedence, retained lifetime and label-scoped resource cleanup, and source identity.
2. Reviewer checks how `secret_redactions` from prior stdout/stderr encounters and from final manifest are reconciled, and fail-stop classifications on JSON/evidence-write/credential-unlink failure.
3. Obtain **explicit controlled-rebaseline approval** to change `tests/postgres/run_postgres.py`. That approval does not grant Docker/PG execution, OCI/16-wheel rebaseline, merge or deployment.
4. After approved correction, the original `unexpected_manifest_secret` test must pass and currently vulnerability-affirming tests must be replaced/converted into **negative absence-of-leak** assertions, then rerun both Python CI matrices and the retained safety suite.

**R7-05B5-4-1 / F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**
