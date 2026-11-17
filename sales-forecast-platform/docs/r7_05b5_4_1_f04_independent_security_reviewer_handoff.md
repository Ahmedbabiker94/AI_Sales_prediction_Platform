# R7-05B5-4-1 / F-04 — Independent security reviewer handoff (NOT AN APPROVAL)

**Prepared:** 2026-11-17. **Decision today: NO-GO.** The author of this review package is **not** acting as an independent approver. Nothing in this document authorizes a protected edit, Docker/PG execution, rebaseline, merge or B5-4-2.

## 1. Frozen provenance and candidate separation

Repository: `Ahmedbabiker94/AI_Sales_prediction_Platform`, branch `fix/r7-05b5-4-1-bootstrap`.

| Item | Git blob SHA / identity | Treatment |
|---|---|---|
| Accepted `tests/postgres/run_postgres.py` | `0863e81b733381a80b2f83560d8bbc231513ba41` | **Frozen, unchanged** |
| Candidate `tests/postgres/run_postgres_security_patch_candidate.py` | `4af3deb7e2bd7a14f227e9d9c8d2bd902028b356` | Review fork ONLY, no CLI, unmocked `command()` has a default-deny RuntimeError |
| Candidate manifest helper `tests/postgres/redaction_finalization_candidate.py` | `86f0b33ea460eec078991ba1f70d9c9c9ba67bae` | Standalone in-memory proposal, not imported by accepted runner |

The candidate is **not** a drop-in replacement: its command hard-stop must NOT be copied to operational runner; its altered context and cleanup control flow must be independently assessed. The source-structure parity tests do not prove semantic equivalence.

## 2. Security defects and expected protected correction

| Ref | Demonstrated issue in accepted source/review | Acceptance condition |
|---|---|---|
| F04-A | Secret text scrubbed in final JSON, but ambient `_LIFECYCLE` already reset so counts may not trigger fail-closed classification | `unexpected_manifest_secret` original regression becomes green **without modifying its expectation**; JSON redacted and manifest marked FAILED / CLEANUP_UNCONFIRMED |
| F04-B | `_TEST_SECRETS` can remain bound after serialization/evidence finalization exception | No residual disposable secret ContextVar after any exit; original/failure exception causality reviewed |
| F04-C | Signal install/password creation can fail before inner `try/finally`, leaking lifecycle ContextVar | Restore saved SIGTERM handler if installation attempted, and reset lifecycle token even if setup or restoration fails |
| F04-D | Interrupted owned-resource reconciliation can skip transient credential-file deletion | Bounded single attempt to remove only `evidence/test-client.env`, even on injected `KeyboardInterrupt`; absent/unconfirmed file and ambiguous Docker outcome never counted as success |
| F04-E | Unknown or interrupted unlink result can be treated ambiguously | Only `REMOVED` / `NOT_PRESENT` affirmative; unknown/exception -> UNCONFIRMED + FAILED; no second unsafe unlink |

## 3. CI observations, not signoff

Latest completed candidate-specific evidence at head `4cf1db8468ddfc8e2c72e3e30aeef900d68d60af` has mock-only F-04 tests (Python 3.11 and 3.12). Last run at `2dd2e3d0fbb40c5cc537d307927e55610b183a75` (ID **44142579839**) was **251 passed / 1 failed, 252 collected** per Python version. The sole known failing test is the original runner `unexpected_manifest_secret` classification. Some green diagnostic tests deliberately establish that accepted runner vulnerabilities exist; the pass count is not acceptance.

Locally scoped test `test_r7_05b5_4_1_f04_local_envfile_interrupt.py` uses a temporary synthetic credential marker, proves filesystem `unlink` of that own file after injected reconciliation interruption, and checks symlink refusal with an untouched temporary target. It does not prove protection against TOCTOU symlink races or secure disk erase.

Other candidate evidence covers handler registration/restoration, nested context reset, early setup error, JSON error, evidence write failure, unknown unlink status, and AST preservation of original Docker/cleanup call expression trees. These are mocks and static checks.

## 4. Required independent reviewer checklist — BLOCKING until signed

- [ ] Identify named reviewer, independent of patch author, and attach a source-scoped signed approval/objection with date and exact baseline/candidate SHAs
- [ ] Audit precise `run()` exception paths and order of cleanup, signal restoration, ContextVar token reset and manifest publication; review `BaseException` and subsequent interruption precedence
- [ ] Confirm the **active lifecycle scope** is retained for owned-resource reconciliation but suspended before final inventory queries; check behavior of actual `command()` timeout and mutation registration
- [ ] Evaluate manifest helper against unexpected nested/key/value content, previous `secret_redactions`, JSON serializer failure, status consistency and no raw test credential publication
- [ ] Confirm transient `envfile` cleanup is bounded, single-attempt, symlink-refusing and fail-closed; explicitly document remaining host filesystem race assumption
- [ ] Confirm no changes to fixed PostgreSQL test selectors, production network boundaries, mounted paths, OCI pinned images, Python lockfile, driver wheel provenance or daemon-trusted-host assumption
- [ ] Require **new negative safety regressions against the approved, corrected accepted runner**; diagnostic tests that previously passed *because of a vulnerability* must not be treated as acceptance tests
- [ ] Review implications of early setup failures that create evidence directory but cannot guarantee a manifest, and distinguish EVIDENCE_UNAVAILABLE from success
- [ ] State explicit **NO-GO** if any above requirement cannot be proven; never accept `pytest` by excluding the existing red case

## 5. Exact next authorized gate

**Do not patch protected `run_postgres.py` on a bare “Go”/“Continue”.** After the independent source review, the owner must separately authorize a narrowly scoped **security-only protected-runner controlled rebaseline**, specifying baseline SHA, intended delta, and conditions. A separate permission would still be needed for real Docker/PostgreSQL 16.15 and owned cleanup integration. The independent reviewer may object and request further candidate changes without issuing authorization.

Broader Phase 7 gates remain: indexed OCI/Docker29 no-pull behavior, verified candidate-v2 sixteen-wheel bytes+lock and OSV/license/provenance, unresolved intermittent `_HOST_HEALTH` and real isolated disposable PG16.15. None are waived by these tests.

**F-04 OPEN / NO-GO. B5-4-2 BLOCKED.**
