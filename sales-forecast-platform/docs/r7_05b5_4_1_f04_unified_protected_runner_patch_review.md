# R7-05B5-4-1 / F-04 — One cohesive protected runner patch: review-only change blueprint

**2026-11-17. NO-GO. NOT AUTHORIZED. Original accepted runner unchanged.**
Accepted baseline `tests/postgres/run_postgres.py` blob: `0863e81b733381a80b2f83560d8bbc231513ba41`.
This is a reviewer change blueprint, not an applied patch, not acceptance and not an instruction to execute PostgreSQL or Docker.

## Problem and goal

Three source/mock-confirmed runner failure classes:
1. Final manifest secret redaction occurs after `_LIFECYCLE.reset(token)`, so `redact_test_secrets()` scrubs text but fails to increment `secret_redactions` and may permit false `CLEANUP_SUCCESS`.
2. JSON serialization failure before `write_evidence` bypasses the present `_TEST_SECRETS.reset(secret_token)` in its narrow `finally`, leaving disposable credentials in the current context.
3. Signal installation and password creation occur before the main `try`; an exception can leave `_LIFECYCLE` bound. Additional handler partial-install behavior must be independently tested.

Target: **fail closed on every detected credential in the manifest, restore ContextVars and signal handler exactly once on all setup/finalization exceptions, preserve ownership-scoped cleanup**, and retain the accepted original runner's behavior for valid disposable database runs. Never silence the retained RED regression.

## One proposed patch boundary (subject to separate approval)

| Original lines | Approved-stage edit proposal | Security invariant |
| --- | --- | --- |
| 227–250 | Introduce a *single outer exception-safe scope* immediately after `_LIFECYCLE.set(record)`; track whether signal installation was **attempted**, and whether the secrets ContextVar token exists, including mid-setup exceptions | Early errors never retain the lifecycle or secret context; partial handler installation receives bounded restore attempt |
| 250–329 | Preserve original DB orchestration inside the same protected scope, with existing authorized Docker operation ledger and `except BaseException` record failure handling | Do not change source/provenance validation, DB selectors, mounts, capabilities, OCI ref or resource ownership |
| 330–361 | Preserve owned-container/volume reconciliation and cleanup deadline. Move ownership-context restoration to exactly one **outer** finally rather than resetting `_LIFECYCLE` inside nested cleanup. Avoid providing active lifecycle to unrelated work after cleanup by explicit record-based redaction | All interrupted/ambiguous outcomes remain `CLEANUP_UNCONFIRMED`; no foreign cleanup |
| 362–381 | Keep label-scoped daemon listings and `remove_transient_credential_file(envfile)` as fail-closed checks; if unexpected exceptions occur, mark unaccepted and still restore all contexts | No PASS after unconfirmed credential file removal, daemon listing or unexpected cleanup exception |
| 382–396 | Replace reliance on `_LIFECYCLE` for final manifest redaction count with an explicitly scoped, pure **record + generated-secrets** finalizer, then publish only redacted JSON. Never use double-redaction as a substitute for explicit hit counting. Treat serialization and evidence-write failures as terminal | Any hit -> FAILED/CLEANUP_UNCONFIRMED; JSON failure cannot silently leave a successful result; status and manifest agree |
| All execution exits | Restore signal handler (if installation attempted), `_TEST_SECRETS` (if token exists), `_LIFECYCLE` (always after binding) in guarded `finally` layers; avoid a restore failure blocking the remaining resets | Exception paths clear sensitive contexts; errors propagate, never fake success |

### Key design choice: define ownership and context lifetimes distinctly

The protected `_LIFECYCLE` context currently also drives command registration/timeout/secret-redaction counting. If an outer `finally` is introduced, first decide precisely **which phase owns resource mutation registration**: keep it bound during cleanup/reconciliation, but don't let later unrelated calls adopt it. Avoid merely moving `_LIFECYCLE.reset` below code where extra daemon queries can unexpectedly register resources. A reviewed explicit `record` argument to finalization is preferable to relying on ambient ContextVar.

Candidate-only support now exists:
- `tests/postgres/redaction_finalization_candidate.py`: independent record/secrets redaction; no accepted runner integration.
- `tests/postgres/run_context_scope_candidate.py`: isolated ContextVar/handler-lifecycle model. Uses injectable callbacks, **not** real signal API or real Docker.
- `tests/runtime/test_r7_05b5_4_1_f04_scope_candidate.py`: expected restoration under installation, generation, body, restore, and partial handler installation failures, plus invalid inputs.

These tests cannot establish that the actual complex `run()` finalizer is correct. Independent review must challenge re-entrancy, nested ContextVar tokens, interruptions (`BaseException`), unexpected JSON types, and restore failure precedence.

## Critical additional gaps to check before implementation

1. **Handler restoration failure:** test that a partially successful install followed by an error gets a restore attempt; if restoration itself fails, still reset both ContextVars and report an unaccepted terminal state. Never assume a Python-only model simulates OS signal races.
2. **Evidence publishing failure:** `write_evidence` rejects symlink outputs with `O_EXCL` and `O_NOFOLLOW`. Preserve these; no overwrite, append, delete, or optimistic PASS when output cannot be published. A failed manifest write may have no trustworthy final artifact.
3. **Cleanup exceptions:** inspect potentially interrupting paths prior to `record.pop('cleanup_deadline')` and after it; ensure token restoration is never skipped. Do not add broad `except: pass` around destructive deletion.
4. **Secret inventory during partial setup:** if password generation fails before all four values exist, ensure no plaintext generated so far is logged/serialized. Safest design validates complete values before binding and never publishes partial credentials; review partial creation under `KeyboardInterrupt`.
5. **Classification:** malformed JSON or secret redaction helper failure should propagate terminal error and never produce fake `CLEANUP_SUCCESS`. `redaction_finalization_candidate.py` checks exact match counts twice, `allow_nan=False`; any difference is error.
6. **Scope:** do not include OCI/no-pull rebaseline or v2 wheel changes in this first security-only diff without a separately reviewed atomic-scope decision.

## Proposed independent acceptance matrix (not yet run against corrected accepted runner)

- Original `unexpected_manifest_secret` expected `CleanupUnconfirmed` remains intact and must **turn green only through verified runner correction**.
- Change the existing **diagnostic** counterexample tests to assert *no* retained `_LIFECYCLE` / `_TEST_SECRETS` after approved change. Until then, their green result denotes a vulnerability.
- Pre-try signal installation exception, password-generation exception, JSON failure, redaction failure, manifest evidence-write failure, credential unlink `UNCONFIRMED`, daemon listing unavailable, late daemon mutation and interrupted cleanup should all be covered with no actual Docker access.
- Verify clean/no-secret result, accurate multi-hit count, key/value redaction, no plaintext in manifest, no false PASS, no cross-run ContextVar contamination, fixed resource names/labels and no unexpected data source.
- Before any execution authorization, run focused 3.11 and 3.12 CI and original host regressions; **separately** approve disposable PostgreSQL 16.15, OCI, wheel hashes, licenses, cleanup manifest and trusted-host boundary.

## Approval required

A human/independent reviewer must explicitly authorize a **security-only protected-file diff** with source blob and acceptance tests. This user instruction to continue candidate analysis does **not** by itself authorize changing accepted `run_postgres.py`. Any review approval to change files remains separate from approval to invoke Docker, to run PostgreSQL, or to merge/deploy.

**R7-05B5-4-1 / F-04 OPEN / NO-GO. B5-4-2 BLOCKED.**
