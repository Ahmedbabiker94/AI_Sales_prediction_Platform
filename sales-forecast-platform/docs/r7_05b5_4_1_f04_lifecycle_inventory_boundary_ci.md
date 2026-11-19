# F-04 review fork: inventory context lifetime divergence corrected

**Date:** 2026-11-17. **Status:** candidate review only; **F-04 OPEN / NO-GO**.

## Confirmed behavioral divergence

The accepted `run_postgres.py` resets `_LIFECYCLE` immediately after owned-resource reconciliation and **before** final read-only Docker inventory queries (`ps -aq`, `volume ls -q`). The review fork's first outer-guard version accidentally kept `_LIFECYCLE` active during those queries, though the `command` helper consults the active record to enforce remaining deadlines and register daemon operations. Thus earlier AST-identical call expression checks did **not** prove equivalent behavior.

The fork now uses a temporary ContextVar token (`inspection_token = _LIFECYCLE.set(None)`) after resource reconciliation and before final inventory queries and evidence publication. The outer `finally` unwinds the inspection token and the original lifecycle token in order; cleanup commands retain their original ownership context. This change is in **`run_postgres_security_patch_candidate.py` only**, not in the accepted runner.

## Test evidence

`tests/runtime/test_r7_05b5_4_1_f04_fork_ownership_scope.py` adds **3** mock-only tests: final listing normal, listing unavailable, and manifest write failure. They assert `_LIFECYCLE.get() is None` inside both inventory queries and evidence publishing, and that nested caller ContextVar values are restored after errors. No Docker command is executed; all queries are synthetic mocks. The original runner's helper `command()` still has different live behavior from the fork's default-deny command stub.

**GitHub Actions run 44139343600**, head `41f245ed65b5508de98c6ebd35700bc5529d2afd`:
- CPython 3.11: **236 passed, 1 failed (237 collected)**.
- CPython 3.12: **236 passed, 1 failed (237 collected)**.
- The one failure remains the original accepted-runner `unexpected_manifest_secret` regression. Several passing diagnostic cases still affirm original vulnerabilities, not safety clearance.

## Deliberate limitations and remaining gate

The review fork has a hard stop on unmocked command execution; no Docker/PG test occurred. No human independent review approval, protected-file modification, OCI/wheel provenance signoff, or controlled rebaseline was granted. Do not confuse candidate tests passing with an accepted security patch. A later reviewer must examine whether context suspension causes any unseen interactions with nested callbacks, exception ordering, SIGTERM races, and manifest redaction accounting. The accepted runner must be updated only after separate explicit authorization, then tested with strengthened no-leak regressions.

**Accepted runner Git blob remains `0863e81b733381a80b2f83560d8bbc231513ba41`**, which matches frozen source baseline `0863e81b733381a80b2f83560d8bbc231513ba41`. No merge, deployment, B5-4-2, or real disposable PostgreSQL authorization.

**R7-05B5-4-1 / F-04 OPEN / NO-GO.**
