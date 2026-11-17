# R7-05B5-4-1 / F-04 — Candidate fork default-deny subprocess review

**2026-11-17. F-04 OPEN / NO-GO. No protected runner edit or real Docker/PostgreSQL execution.**

## Finding and candidate mitigation

The isolated `tests/postgres/run_postgres_security_patch_candidate.py` inherited `command()` with a real `subprocess.Popen` path. Deleting its `__main__` entrypoint alone did not prevent imported callers from executing the original `run()` and potentially calling Docker/Git. This was a real **accidental execution risk in the review fork**, not evidence that such execution occurred.

The fork's unmocked `command()` now raises `RuntimeError('OFFLINE_REVIEW_ONLY: candidate command execution forbidden')` immediately, before any original command or `Popen` logic. The original command body remains below the guard for textual diff review, not for execution. Approved mock-only candidate tests explicitly replace this function. Do not import this fork into operational code, do not remove the hard stop or use it for Docker validation. The guard is a source-level defense against accidental invocation, not a sandbox or protection against deliberate Python monkeypatching.

## Independent tests added

`tests/runtime/test_r7_05b5_4_1_f04_fork_default_deny.py` adds **5** Python-only assertions:
- Reject an unmocked Docker version command.
- Reject an unmocked Docker run command.
- Reject an unmocked Git status command.
- Verify unmocked `run(allow=True, ...)` fails before evidence directory creation or invoking an external process.
- AST-check the early unconditional `raise` precedes the retained command implementation.

## Actual CI results

GitHub Actions run **44138026025**, commit `2559626c7175429f6da098a23a7961646e38de59`.
- Python **3.11:** 227 passed, 1 failed, 228 collected.
- Python **3.12:** 227 passed, 1 failed, 228 collected.
- All five default-deny tests passed.
- The sole failure is the unchanged accepted runner's original `unexpected_manifest_secret` fail-stop regression. Several green diagnostic tests continue to prove original vulnerabilities and **cannot be counted as acceptance evidence**.
- This suite uses PyPI-installed pytest as a CI test runner, **not** approved 16-wheel candidate-v2 supply-chain bytes.

## Protected baseline

`tests/postgres/run_postgres.py` Git blob is `0863e81b733381a80b2f83560d8bbc231513ba41`, matching the frozen accepted blob `0863e81b733381a80b2f83560d8bbc231513ba41`. No edit to protected `Dockerfile`, `requirements.lock`, test runner, merge, production, or real PG was authorized or carried out.

## Remaining review gate

Independent security reviewer must assess the minimal protected-runner security diff against all 3 evidenced failure classes, cleanup exception paths, signal restoration errors, real manifest write failure and no-leak invariants, *before* separate explicit controlled-rebaseline authorization for accepted runner edits. OCI image source and Docker29 no-pull semantics, 16-wheel provenance/license review, intermittent `_HOST_HEALTH`, and independently authorized disposable PG16.15 cleanup remain blockers. Candidate default-deny should **not** be copied into operational `run_postgres.py` verbatim.

**R7-05B5-4-1 F-04 OPEN / NO-GO. B5-4-2 BLOCKED.**
