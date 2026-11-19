# R7-05B5-4-1 — Preimplementation source review findings

**Status: preliminary technical review only, NOT an independent security approval.
Milestone remains OPEN / NO-GO.** Reviewed exact branch
`fix/r7-05b5-4-1-bootstrap` against
`r7_05b5_4_1_controlled_rebaseline_implementation_proposal.md`.
No approved runner, pins, Dockerfile or dependency lock have been changed by
this review. No PostgreSQL integration test was run.

## Blocking findings

### F-01 (HIGH): Source provenance is recorded but not fail-closed

In `tests/postgres/run_postgres.py::run`, the record captures `git rev-parse
HEAD` and `git status --porcelain` before Docker mutations, but does not
reject a nonempty status. As written, source provenance evidence may truthfully
record dirty source while still allowing a database run to proceed. Also,
Windows-created worktrees' `.git` pointer is not readable by WSL Git.

**Required**: Use clean separate native-Linux checkout; enforce checked Git
command return code, exact expected commit/approved ref and empty status before
first Docker mutation; negative tests for nonzero Git command, dirty/untracked
files, invalid path and changed source. Do not reset or rewrite Windows Git
metadata or user's dirty production model state.

### F-02 (HIGH): OCI index versus config digest mismatch extends to running

`inspect_image(identity)` requires local `.Id == identity`. On the trusted
Docker Desktop 29 host, `image inspect` by pinned repository digest returned
the **index** digest, while the original `POSTGRES` constant is a **config**
digest. This affects not only the initial inspect but the provisioning and
server `docker run` calls. The original base `FROM sha256:417063...` is
also unavailable and has no recovered registry source.

**Required**: Review immutable repository-index -> unique linux/amd64
manifest -> expected config binding for *each* base; explicitly test local
presence and runnable immutable refs. Negative tests for mismatched hashes,
platforms, unavailability and altered local image references. Registry
metadata-only preflight is separate from offline Docker run; do not weaken
original SHA-256 identities or implicitly pull on run.

### F-03 (HIGH): Old client builder cannot reproduce full test dependencies

`build_client()` copies only the psycopg2 wheel and the old
`requirements.lock`, which pins only psycopg2-binary, not pytest, pandas,
pydantic and their dependencies. User's independent candidate wheelhouse with
16 wheels passed SHA-256 checks, offline resolver and installation, Ubuntu
imports/`pip check`, and a separately built nonroot isolated Docker
import probe. Those are *candidate evidence* only.

**Required**: Independently pin and review all 16 exact wheel files and
hashes, transitive metadata closure and license/security provenance.
Implement bounded safe-file validation in proposed test-only builder, reject
symlinks and extra/missing/mismatched/duplicated wheels. Build only from
reviewed immutable base and offline lock, without changing product image.

### F-04 (SECURITY GATE): Existing lifecycle isolation remains unproven in failures

The current harness includes ownership-scoped container/volume cleanup,
secret redaction, and rejection on ambiguous daemon mutation, but real Docker
failure and interruption paths have NOT been demonstrated on the user's host.
The one-shot root CHOWN provisioning step (`--user=0:0 --cap-add=CHOWN`) is
distinct from the nonroot test client; its narrow volume scope must remain
independently reviewed. Do not call the setup hostile-plugin safe: in-process
dispatcher reference disclosure is a separate unresolved boundary.

**Required**: Offline adversarial negative tests first; opt-in real disposable
PostgreSQL run only after rebaseline approval; inspect cleanup status,
run-scoped volumes/containers and redacted logs. Independent security reviewer
must approve relevant isolation and trust-boundary findings separately.

## Evidence already observed, without overstating

- Official Python 3.11.18 candidate base with fixed index
  `sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39`
  and amd64 manifest
  `sha256:e8c69d86735f6df3efa06b324694fe51f0422785f83a3bd463052636c2421944`,
  config `sha256:cf2dad23925ed51a00735f57606b2998aedf4705a1634cb9ae1053b5c145d5ba`.
- PostgreSQL original identity remained index
  `sha256:e8a07d2c7579b038938cdba6c539e580e5001caf9ea203d397b5cc83e76bd938`,
  manifest `sha256:cf5d353f55240ecc2f811827a15c7d1f2d562f08019b49dca12b5a59f4ab5962`,
  config `sha256:6a35283e53fbe883da6a6ffbce8d3999dc66076cc5c4e2f1d21bdbd9e26bf86f`.
- 16-wheel candidate lock and offline install/import checks passed in the
  user's staging and candidate Docker client. Local BuildKit config digest
  `sha256:917774e30ed62ad4a5e08bfce066476b6fb20b871727846b369e29a9e0c07e71`
  is not a registry-signed production or approved client identity.

## Proposed sequence and decision

1. Fix F-01 source-provenance gate first with negative tests *on an isolated
   branch* and CI. Do not run database.
2. Prepare reviewed F-02 OCI identity/runtime-reference design and negative
   tests for both base images.
3. Implement F-03 **only after** full wheel manifest is independently
   reviewed and rebaseline approval is explicit.
4. Complete F-04 negative tests and separate security review.
5. Use a clean Linux-native checkout for a separate authorized disposable
   PG16.15 integration run, inspect source/cleanup/redaction evidence.

**GO only for proposal refinement and separate review.
NO-GO for modifying approved pins, live DB tests, merge, deployment,
or B5-4-2.**
