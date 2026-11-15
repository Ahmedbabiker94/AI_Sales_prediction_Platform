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
