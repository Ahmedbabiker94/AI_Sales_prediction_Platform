# R7-05B5-4-1 — Test Client Dependency Compatibility Review

**Decision: GAP CONFIRMED — NOT APPROVED FOR REBASELINE.**
This is a source-level import inventory, not evidence of installing packages
inside the proposed official Python client. It changes no approved runner,
Dockerfile, dependency lock or PostgreSQL schema.

## Executive interpretation

The old test-client base image cannot be recovered from the available registry
provenance. A candidate official Python 3.11.18 slim-bookworm image has
independently inspected immutable OCI identities, but merely replacing the
`FROM` line cannot reproduce the old test client's package environment.
The old Dockerfile installs **only psycopg2-binary** after starting from the
unknown original base; the PostgreSQL integration tests rely on other packages.

## Source-grounded import inventory

| Package | Observed source dependency | Assessment |
| --- | --- | --- |
| CPython 3.11 | Approved `psycopg2_binary-2.9.12-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.whl`; client executes `python -m pytest` | Candidate 3.11.18 matches ABI family; not a runtime test |
| pytest | `tests/postgres/conftest.py`, foundation `test_forecast_evidence_postgres.py`, B5-4-1 PostgreSQL modules and `tests/runtime/test_stored_forecast_accuracy.py`; client entrypoint `python -m pytest` | Required; NOT installed by current `requirements.lock` |
| psycopg2-binary == 2.9.12 | `src/database/offline_forecast_postgres.py` explicitly imports/checks this version; tests access psycopg2 connection/exception APIs | Existing wheel SHA256 verified by user, retained in lock |
| pydantic (2.x) | `src/data/contracts/forecast_evidence_storage.py` uses `BaseModel`, `BeforeValidator`, `ConfigDict`, `model_validator`; other contracts use `field_validator`, `model_validate_json` | Required; NOT installed by current lock |
| pandas | `src/data/contracts/forecast_evidence_storage.py` imports `pandas as pd` at module load | Required; NOT installed by current lock |
| numpy | pandas' standard binary/runtime dependency; NOT separately established as direct import of the reviewed test module | Required transitively if specified by final pandas wheel metadata; capture exact wheel in resolved dependency lock |
| Other transitives | pytest, pydantic and pandas distribution metadata | Must resolve, record full dependency graph, verify individual wheel hashes |

This review inspected the actual foundation, restart, canonical-reporting,
stored-forecast-accuracy and host-reporting PostgreSQL tests, the imported
`tests/postgres/conftest.py`, `tests/runtime/test_stored_forecast_accuracy.py`,
`forecast_evidence_storage.py`, `features.py`, `mapping.py`,
`stored_forecast_accuracy.py`, `canonical_forecast_read.py`,
`offline_forecast_postgres.py` and relevant repositories/services. It does
not claim to have enumerated every dynamic runtime import or all transitive
libraries. A clean offline import probe and independent review are required.

## Identity evidence — candidate only

- Proposed name: `docker.io/library/python:3.11-slim-bookworm`, registry
  metadata reported version `3.11.18-slim-bookworm` on 2026-11-15.
- OCI index: `sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39`
- linux/amd64 manifest: `sha256:e8c69d86735f6df3efa06b324694fe51f0422785f83a3bd463052636c2421944`
- linux/amd64 config: `sha256:cf2dad23925ed51a00735f57606b2998aedf4705a1634cb9ae1053b5c145d5ba`

These three immutable references were provided by the user after read-only
`docker buildx imagetools inspect`. They are **not** approved Dockerfile pins.
The mutable tag must never be used as a security identity. The actual client
image is not yet downloaded, built or exercised.

## Mandatory approval gates (sequential)

1. Reviewer confirms exact client module/import graph from a trusted Linux
   checkout, including runtime dynamic imports. No operational model-fitting
   imports, migrations or source adapters may be added.
2. Create a **new proposed client-only lock and offline wheelhouse** with
   exact versions/sha256 for pytest, pydantic, pandas and all dependencies
   (including the already verified psycopg2 wheel). Verify Python 3.11 Linux
   amd64 wheel ABI, package provenance and license constraints. Do not infer
   that GitHub Actions' online pip environment equals the offline client.
3. Review a separate candidate Docker build with digest-pinned base and
   `pip --no-index --require-hashes`. Confirm `pytest --version`, imports of
   test/contract modules, nonroot runtime and absence of network dependence
   **before** any disposable database test.
4. Independently evaluate supply-chain acceptability and Docker Desktop
   containerd index-vs-config reference compatibility on both PostgreSQL and
   client images. Preserve strict fail-closed checks and the original
   docker `--network=none`, read-only, capabilities, resource isolation.
5. Fix the Windows-created Worktree Git metadata incompatibility by making a
   separate clean **Linux-native checkout** for source provenance before
   executing the original PostgreSQL harness. Do not alter Windows Worktree
   internals or overwrite the existing production-model directory.
6. Obtain explicit reviewed rebaseline approval. Only then consider changing
   the separate test client's Dockerfile, lock and the original runner together
   under B5-4-1 with offline negative tests and independent review.
7. Execute real disposable PostgreSQL 16.15 only after all prerequisites
   pass and user explicitly authorizes it. Collect redacted result, cleanup
   ownership and the independent in-process trust-boundary review.

**Current decision: R7-05B5-4-1 OPEN / NO-GO.**
No image substitution, Docker pull, client build, live database execution,
merge, deployment or move to B5-4-2 is authorized by this document.


## Candidate wheelhouse generator — staged, not executed

Added `tests/postgres/prepare_client_wheelhouse_candidate.py` as a separate optional **proposal artifact**. It does not replace the accepted `tests/postgres/requirements.lock` or Dockerfile, build a container, start PostgreSQL, or write to existing evidence. A reviewer-controlled Python 3.11 Linux/amd64 staging host may invoke it only with `--allow-pypi-download` and a **new absent output directory**; it uses official PyPI for binary-only top-level versions already exercised by focused CI: pytest 8.4.2, pydantic 2.13.3, pandas 2.3.3, numpy 2.4.4, and the original externally verified psycopg2-binary 2.9.12 wheel.

Outputs are explicitly named `candidate-requirements.lock` and `candidate-manifest.json`; every downloaded wheel is SHA-256 inventoried. This is an **unreviewed candidate**, not a proven complete dependency lock. Failure leaves `INCOMPLETE_DO_NOT_USE`. Later review must verify distribution metadata and **all transitive dependency closure offline**, and prove import compatibility with the exact proposed CPython image in an isolated test before updating any accepted harness.

The Windows-created Git worktree is not usable by WSL Git without a separate clean Linux checkout; do not run original PostgreSQL harness from that worktree. No wheel downloads are performed by the GitHub-focused Python test workflow.
