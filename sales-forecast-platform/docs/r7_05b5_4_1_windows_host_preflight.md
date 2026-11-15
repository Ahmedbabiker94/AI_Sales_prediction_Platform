# B5-4-1 — Windows + Docker Desktop trusted-host preflight

**Status:** READ-ONLY CHECK PREPARED, NOT PostgreSQL 16.15 execution. This
guide does not authorize pulling images, starting containers, changing the
approved Docker harness, running native workers, or accessing production data.

## Step 1 — Windows PowerShell (no mutation)

```powershell
wsl --status
wsl --list --verbose
docker version
```

The original PostgreSQL harness is **Linux/amd64**, requires a Unix Docker
socket and locally cached exact Docker image IDs. Do not run the harness
directly from native Windows Python or PowerShell. If no suitable WSL2 Linux
distribution with Docker Desktop WSL integration is available, stop here and
report the output (omit personal paths/usernames if sharing logs).

## Step 2 — In the trusted WSL2 Linux shell (no mutation)

Use a trusted local checkout of the isolated branch. Keep unrelated worktree
changes intact and never use force checkout or reset. The script does not
require or download any Docker images.

```bash
cd /path/to/AI_Sales_prediction_Platform/sales-forecast-platform
python3 tests/postgres/preflight_b5_4_1_host.py --wheelhouse /absolute/path/to/verified/wheelhouse
```

Replace both example paths with existing local directories. It checks Linux
amd64, the Unix Docker socket, Docker CLI, absence of remote-Docker overrides,
**exact local image IDs** and SHA-256 of the **existing** pinned CPython 3.11
psycopg2-binary wheel. Output includes only PASS/BLOCKED booleans, no passwords
or production paths. Exit 2 means prerequisites are missing; it does not mean
product code tests failed.

Expected exact image IDs are pinned in `tests/postgres/run_postgres.py`. A
similar PostgreSQL tag or image from another registry is **not** accepted.
Do not use a replacement, unverified download, or network pull to make this
check pass.

## Step 3 — only after explicit reviewed preconditions

An independent reviewer must approve the checkout/harness diff and the
trusted-host-only threat model. Only then run:

```bash
python3 tests/postgres/run_b5_4_1_postgres.py \
  --allow-disposable-postgres \
  --wheelhouse /absolute/path/to/verified/wheelhouse \
  --evidence /absolute/path/to/new-unused-evidence-directory
```

The directory **must not exist** before the run. Inspect all stdout/stderr
evidence, `result`, `credential_file_cleanup`, `secret_redactions`,
`cleanup_passed`, `cleanup_status`, creation attempts and remaining
containers/volumes. Do not upload artifacts containing secrets. The harness
only deletes resources labeled and registered to the exact disposable run,
never production or other user Docker resources.

If the preflight fails, stop and report only the prerequisite summary — no
Docker modifications are authorized by this guide. B5-4-1 remains OPEN / NO-GO
until real PostgreSQL evidence AND independent security review pass.
