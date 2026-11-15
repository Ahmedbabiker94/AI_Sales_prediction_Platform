# R7-05B5-4-1 — PostgreSQL 16.15 execution gate

**Status:** PREPARED / **NOT EXECUTED** / **NO-GO FOR ACCEPTANCE**.
Reviewed branch: `fix/r7-05b5-4-1-bootstrap`. No production DB, no generic DSN, no TCP database port, no unpinned image pulls.

## Execution prerequisites — all mandatory

1. Trusted Linux/amd64 Docker host with locally available **exact image IDs**, inspected by the existing harness: PostgreSQL `sha256:6a35283e53fbe883da6a6ffbce8d3999dc66076cc5c4e2f1d21bdbd9e26bf86f`; client base `sha256:417063b71e35c7a9889c10ac2516c328f16a483e065ec858df101eb9728c2df2`. Avoid on-demand image overrides or network pulling.
2. Preverified `psycopg2_binary-2.9.12-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.whl` inside a trusted wheelhouse, SHA-256 `e4e184b1fb6072bf05388aa41c697e1b2d01b3473f107e7ec44f186a32cfd0b8`; original harness checks the hash.
3. Trusted checkout of the reviewed commit; clean working tree. The original harness records commit and tree status in its evidence manifest. A new, absent, local evidence directory (no symlink).
4. Independent human preflight of `tests/postgres/run_postgres.py`, `run_b5_4_1_postgres.py`, `tests/postgres/conftest.py`, and migration scope before opt-in. The runner is restricted to synthetic test-only content and original role/bootstrap process. Never set production secrets or `DATABASE_URL`.

## Explicit command (from sales-forecast-platform/ on the trusted host)

```bash
python tests/postgres/run_b5_4_1_postgres.py \
  --allow-disposable-postgres \
  --wheelhouse /ABSOLUTE/PATH/TO/VERIFIED/WHEELHOUSE \
  --evidence /ABSOLUTE/PATH/TO/NEW/UNUSED/EVIDENCE-DIRECTORY
```

The script reuses `run_postgres.run` with its original initialization, bootstrap, role checks, explicit checksum-pinned migration, foundation pytest, restart and durability test. Only after success does it run the fixed B5-2, B5-3 and B5-4-1 reporting PostgreSQL test modules in a separately named run-scoped container. No arbitrary selector or connection target is accepted.

## Mandatory acceptance evidence

- Exact source commit and clean `source_status` from `run-manifest.json`.
- `result: PASS`, `cleanup_status: CLEANUP_SUCCESS`, `cleanup_passed: true`, no remaining owned containers or volumes, no unresolved creation attempts. If cleanup is unconfirmed, halt acceptance and examine only recorded owned identities, without blanket Docker pruning.
- Foundation, restart and B5-4-1 stage stdout/stderr logs; all stage `exit_code: 0`, and no skipped relevant tests. The B5-4-1 stage is explicitly identified by `stage: b5-4-1`.
- PostgreSQL 16.x proven by original guard and exact image identity; reader restricted to SELECT-only and pinned scope; no operational/ML/native-worker imports.
- Independent diff/security review addressing in-process dispatcher reference disclosure. Passing database tests **cannot** close this separate trust-boundary finding.

## Current blockers

The present assistant container has Python 3.13.5 and does **not** have `docker`, `postgres` or `psql`, as checked on 2026-11-15. GitHub Actions focused unit workflow only compiles the runner and simulates Docker commands. It does not run the disposable DB harness. This document is an execution gate, NOT an execution report.

**Milestone:** R7-05B5-4-1 remains OPEN / NO-GO; no merge, no deployment, no B5-4-2.


## Transient test-credential evidence hardening — 2026-11-15

The original harness' `test-client.env` contains synthetic but plaintext role passwords. It is written with `0600` permissions within a `0700` evidence directory for client `--env-file` use. Previously the file could survive the run.

The harness now calls `remove_transient_credential_file` in its `finally` path before publishing the run manifest. Results are `REMOVED` or `NOT_PRESENT`, or `UNCONFIRMED` on symlink replacement, permission failure or unexpected unlink errors. An unconfirmed result forces `cleanup_status: CLEANUP_UNCONFIRMED` and prevents acceptance. This does not guarantee secure erase or eliminate Docker daemon/host privilege exposure. Do not upload an evidence directory until checking the transient file is absent and no other credential data appears in logs.

Focused CI run `43727698288`, commit `6a6f3928`, has **55/55 passing on CPython 3.11 and 55/55 on CPython 3.12**. Four additional tests simulate credentials cleanup and failure; they do not run PostgreSQL.

Remaining: review manifest/log redaction independently, real disposable PG16.15 execution and cleanup evidence, and separate host-only security boundary review. Status remains **OPEN / NO-GO**.


## Evidence secret-redaction checkpoint (2026-11-15)

- Exact disposable password values are now retained in a process-context redaction registry during test execution and removed from reported stdout/stderr, command failure messages, written evidence logs, and serialized manifest content. This is content filtering, **not isolation of host process arguments or Docker daemon metadata**.
- A detected occurrence increments `secret_redactions`, forces `cleanup_status: CLEANUP_UNCONFIRMED`, `result: FAILED` and rejects acceptance even though the copy written to evidence is redacted. Transient `test-client.env` is separately deleted in the final cleanup path; any uncertain deletion similarly rejects acceptance.
- GitHub Actions run `43728104695` on commit `75e3facf`: **58/58 passing tests** on both CPython 3.11 and 3.12, including mocked evidence/console redaction checks. Subsequent manifest status fix requires its own fresh CI validation.
- **No actual Docker/PostgreSQL 16.15 test has run from this CI.** No evidence directory should be shared unless it passes manual credential inspection and the original scoped cleanup manifest is confirmed. Independent security review remains outstanding.
