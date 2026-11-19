# R7-05B5-4-1 / F-04 — Local synthetic credential file cleanup under interrupted reconciliation

**2026-11-17 — candidate-only, NO-GO.** Not acceptance of protected runner, real Docker/PG or secret erasure.

## New test evidence

New module `tests/runtime/test_r7_05b5_4_1_f04_local_envfile_interrupt.py` adds two **real local filesystem** tests under pytest's isolated `tmp_path`, without any external commands. The test invokes only the review-only fork `run_postgres_security_patch_candidate.py`, whose unmocked `command()` has an unconditional execution hard stop. The test substitutes mock `inspect_image` and `remove_owned`, injects synthetic unresolved owned-container metadata and a `KeyboardInterrupt` on reconciliation sleep.

**Case 1:** create `evidence/test-client.env` as a regular file holding a fake, non-credential marker. On interrupted reconciliation the fork's outer fallback calls the unchanged `remove_transient_credential_file()` implementation. It returns `REMOVED`; the test verifies the file no longer exists and that neither ContextVar remains bound after the synthetic exception.

**Case 2:** instead create `evidence/test-client.env` as a symlink to a second, synthetic file **inside the same temporary fixture directory**. The unchanged helper returns `UNCONFIRMED` without unlinking the symlink target. The test verifies the target content remains unchanged, the candidate record is `FAILED / CLEANUP_UNCONFIRMED`, and both ContextVars are restored. Test teardown unlinks the test-owned symlink only.

These are narrowly scoped **temporary local file** assertions, not proof of race resistance, secure erase, daemon quiescence, or real live database credentials. The helper uses `is_symlink()` / `exists()` / `unlink()` and is still subject to host trust and possible filesystem race assumptions. The test does not simulate those races.

## CI results

GitHub Actions run **44142579839** tested SHA `2dd2e3d0fbb40c5cc537d307927e55610b183a75`:
- Python **3.11: 251 passed, 1 failed, 252 collected**.
- Python **3.12: 251 passed, 1 failed, 252 collected**.
- Both newly introduced local-filesystem cases passed.
- The **only failing test** is still the frozen accepted-runner regression `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]`, showing original synthetic preflight `RuntimeError` in place of `CleanupUnconfirmed`. It has not been skipped or weakened.
- Some earlier passing diagnostics intentionally demonstrate security defects in the accepted runner. Therefore **251 green cases do not constitute security signoff**.

## Unchanged protected baseline and next gate

Original `tests/postgres/run_postgres.py` Git blob: `0863e81b733381a80b2f83560d8bbc231513ba41`, matching required frozen `0863e81b733381a80b2f83560d8bbc231513ba41`. No protected edits, Docker, PostgreSQL, 16-wheel artifacts, OCI ref rebasing, merge, or deployment were made.

Next: independent reviewer must decide whether the current candidate's overall control flow, ContextVar restoration, cleanup classification and publication semantics are sufficient for a **separately authorized security-only protected-runner diff**. Additional host/OCI/wheel and real PG16.15 gates remain separate. Original failing test must turn green only from an authorized runner correction, with no-leak regressions replacing vulnerability-affirming diagnostics where appropriate.

**F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**
