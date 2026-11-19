# Phase 7 / R7-05B5-4-1 / F-04 — P1-G Read-only operational admission and evidence-consumer inventory

**Recorded:** 2026-11-19. **Authority:** planning and read-only inspection only; **NO protected runner/wrapper change**, no executable candidate promotion, test execution, Docker/PostgreSQL or external-supply-chain validation. **F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**

## 1. What Codex accepted, and what it did not

Codex reviewed **P1-F code-tested commit `06c3c374232bb4fad6e549c322e3a1f6bdd0d195`** and returned **TECHNICALLY ACCEPTABLE FOR OWNER REVIEW** within a **trusted, synchronous, single-process** model. Reviewed P1-F candidate blob: `54fbe8292f7e8054ff682cadb97812683992cd1e`; test blob `a0a41ec4c0a30b0807fc423e384d99f8c8cd8140`. [CI run 44478534154](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44478534154) shows **510 passed/1 failed per Python 3.11 and 3.12**, sole failure being the **unchanged accepted frozen-runner** `unexpected_manifest_secret` regression. **Overall security CI is FAILURE.**

The approved *design for owner review* is a **lossy** Docker-inspect evidence projection of selected fixed Env names/values. It eliminates the reviewed split-secret publication path **inside that projection only**. Codex did NOT establish general worker-output secrecy, real Docker/PG runtime safety, operational consumer integration, external provenance, or owner authorization.

**Protected baseline identities verified again:**
- `tests/postgres/run_postgres.py` SHA `0863e81b733381a80b2f83560d8bbc231513ba41`;
- `tests/postgres/run_b5_4_1_postgres.py` SHA `040d0d30bb719d646eb350b229ccd9a4c5f2bf79`.

## 2. Source inventory methodology and limits

This is a **bounded, read-only** source inventory anchored to the P1-F code-tested tree at `06c3c374232bb4fad6e549c322e3a1f6bdd0d195`, not a scan of local deployments. The repository tree had **593 file blobs**. Inspected file contents of **93 selected operational/source/CLI/workflow paths** (all `tests/postgres/*.py`, `.github/workflows/*.yml`, `scripts/*.py`, principal API/dashboard/dags/job/runtime/handoff/report modules), **69 `tests/runtime/*.py`** and some additional `src/` modules. Searches looked for direct `run-manifest.json` reads/writes, `manifest_bytes`, `terminal_manifest`, direct runner imports, controller calls, and related execution references. Tests were distinguished from consumers.

**Coverage limitation:** This is a targeted source inventory, **not exhaustive proof** that none of the remaining repository files, local/remote deployments, external scripts, documentation instructions, untracked artifacts, third-party CI or future callers accept a bare PASS file. A final complete path/consumer sweep and deployment configuration inventory remains a P0 hold point **before any protected-source authorization**. Source-level findings below are not evidence of live execution.

## 3. Direct runner paths and admission boundaries

| Entry/call site | Source finding | Classification now | Required transfer policy |
|---|---|---|---|
| **Base CLI:** `tests/postgres/run_postgres.py` | Original `__main__` calls `run()` directly; around line **392** writes `evidence/run-manifest.json` before exception finalization. No bound controller in this protected source. | **Protected original / uncorrected F-04** | A separately approved source correction plus one controller-owned invocation; reject exceptions and `None`. Original redaction regression must become GREEN unchanged. |
| **B5-4-1 CLI:** `tests/postgres/run_b5_4_1_postgres.py` | Lines **12–15** import frozen `run,command,write_evidence,redact_test_secrets`; line **55** imports frozen `_LIFECYCLE`; lines **89–90** call `run(..._host_stage=host_stage)` directly. No controller receipt is required/consumed. | **Active source entrypoint; NO operational admission integration** | Future separately authorized wrapper must use the **same corrected runtime** as the controller/stage and reject no receipt; consume verified immutable `manifest_bytes` without a raw-file fallback. |
| **B5-1 CLI:** `tests/postgres/run_forecast_read.py` | Line **9** loads the frozen `run_postgres.py` via `runpy.run_path`, line **37** invokes `pg['run']` directly; lines **29–31** parse/store unprojected Docker-inspect metadata after a weak password-name heuristic; line **32** prints test output directly. | **Separate source entrypoint; candidate projection not wired** | Must be **explicitly included in a separately reviewed transfer scope** or **explicitly held out of F-04 acceptance/claims**. Raw inspected metadata and stdout are distinct confidentiality concerns if outputs may be untrusted. Do not silently claim it was fixed with B5-4-1. |
| **B5-2 CLI:** `tests/postgres/run_stored_accuracy.py` | Line **9** loads frozen runner; line **38** calls it directly; lines **30–32** retain original inspect/Env transform and direct stdout printing. | **Separate source entrypoint; candidate projection not wired** | Same explicit include-or-hold decision as B5-1, with separately designed negative tests if included. |
| **Candidate B5 entrypoint:** `tests/postgres/run_b5_4_1_postgres_admission_candidate.py` | Lines **29–46** select a fixed candidate reporting stage and call `invoke_candidate_and_admit` directly. No CLI; runtime command remains hard-stopped except when mock tests patch it. | **Reviewed proposal / offline only** | Use as source-parity design reference, **not an executable operational CLI**. |
| **Candidate admission controller:** `tests/postgres/run_manifest_admission_controller_candidate.py` | Lines **89–150** own exactly one synchronous invocation and return immutable `VerifiedCandidateManifest` only for same-run UUID, finalized exact bytes+digest, cleanup success, restored handler and redaction zero; lines **153–160** deny archive/restart admission. | **Reviewed candidate in-process trust boundary** | Rebind only to a separately owner-authorized **operational corrected runner** and same `ContextVar` identities. No bearer credential/replay/archive claim. |
| **Legacy helper:** `tests/postgres/run_postgres_security_patch_candidate.py::terminal_manifest_accepted_candidate` | Lines **103–145** accept caller-supplied `run_completed=True` plus raw file fields. Its own docstring denies using bare artifact as authoritative completion. | **Not an approved operational success oracle** | **Never transfer or invoke as an operational admission API**. Require controller-owned normal return with exact invocation binding instead of a supplied boolean/file-only check. |
| **Legacy unit test:** `tests/postgres/test_launcher_lifecycle.py` | Line **124** opens `run-manifest.json` solely after an expected failure to assert `CLEANUP_UNCONFIRMED`. Other `tests/runtime/test_r7_05b5_4_1_f04_*.py` similarly read mock manifests as *test assertions*. | **Tests, not operational admission** | Retain read-only assertions; **do not confuse their direct reads with authority to admit**. |
| **Read-only host preflight:** `tests/postgres/preflight_b5_4_1_host.py` | Checks Docker CLI/socket/image and wheel locally; explicit description and status lines 1–9, 89–107 say **no harness execution or authorization**. | **Prerequisites only; not proof of run success** | Keep separate; passing preflight cannot substitute for controller receipt or actual permitted PG integration. |
| **GitHub Actions:** `.github/workflows/r7-b5-4-1-f04-candidate-offline.yml`, `r7-b5-4-1-focused.yml`, `r7-b5-4-1-pg-preflight.yml` | Offline workflow compiles/tests candidate mocks, focused workflow runs synthetic contracts, host workflow only checks prerequisites. No automatic production manifest admission observed in these reviewed workflows. | **Offline/host-readiness evidence, not operational admission** | Continue independent approval of actual protected code and host tests; no CI green claim while frozen RED exists. |

**Source-only result:** Among the inspected **operational** Python entrypoints/workflows there is **no verified consumer implementing controller-bound admission**; the B5 candidate controller is not wired to the protected path. I also did **not identify a separate operational archive importer or raw-manifest PASS reader in the targeted subset**. This latter statement is **not a guarantee of absence** outside the audited scope. Critical uncovered execution paths are the direct original runner itself, operational B5-4-1, and adjacent B5-1/B5-2 wrappers.

## 4. Proposed admission contract for the future authorized source delta

1. One **single** corrected operational module owns `run()`, `command()`, `_LIFECYCLE`, `_TEST_SECRETS`, redaction/evidence writer, creation ledger, cleanup and signals; no import mixture or `runpy` duplicated ContextVars in the integrated B5-4-1 path.
2. A trusted synchronous controller **must invoke that exact run** and capture **that invocation's** run UUID and final redacted record. Only a **normal return** and verified byte/digest equality can create an immutable receipt.
3. The operational B5-4-1 CLI must explicitly **reject an exception or `None`**, return nonzero, and never convert stale `PASS`/existing `run-manifest.json` or `run_completed=True` to success. Downstream consumers should accept **receipt.manifest_bytes**, not reopen the pathname.
4. Keep **archive, restart and cross-process imports default-deny** until a separately reviewed durable, trusted completion-attestation/replay design exists. The candidate's frozen dataclass is **not** an externally authenticated receipt.
5. Before granting protected write authorization, implement **mocked operational entrypoint and identical-runtime/ContextVar regressions**, plus a strict file-level proposed delta. **Do not patch accepted source** while this remains only a plan.
6. The **source-projection compatibility** decision is distinct: prior arbitrary Docker inspect user/mount/network/label metadata is now deliberately omitted; **no missing-field-as-safe interpretation** is permitted. Review needed security facts and add bounded projections only if the owner accepts them. Existing label checks for removal are a different private trusted reconciliation path.

## 5. New owner decision points (NO ASSUMED ANSWERS)

- **A — Evidence policy:** Does the owner agree that the F-04 terminal evidence may store only approved fixed Docker-inspect diagnostics, not full raw Docker inspection metadata? If original inspect fields are needed for security audit, enumerate them for a new narrowly reviewed projection. **Until decision: unresolved.**
- **B — Trust boundary:** Are worker/test stdout/stderr, readiness strings and trusted host callbacks considered **trusted internal test producers**? The current exact-secret redactor does **not** generally prevent split or encoded credential disclosure in unrelated worker outputs. If those producers are untrusted, separate output minimization/sanitization must be in protected transfer scope. **Until decision: unresolved.**
- **C — Other entrypoints:** Should B5-1 `run_forecast_read.py` and B5-2 `run_stored_accuracy.py` be explicitly remediated in an expanded rebaseline or held **not approved/not claimed F-04-safe**? These remain distinct original-runner callers with raw inspect capture. **Until decision: unresolved.**
- **D — Review/authorization:** The G-01 owner self-review decision (no independent human specialist required for this personal project) must separately accept residual risks and identify exact updated Git blobs. Technical Codex acceptance is **not** independent human signoff, not owner acceptance, and **not** `AUTHORIZE SECURITY-ONLY CONTROLLED REBASELINE`.

**Non-negotiable gates:** original protected-runner regression must pass unchanged after a separate approved security-only patch; all 3.11/3.12 required security tests must pass; host Docker29/no-pull pinned OCI, sixteen-wheel hashes/license/OSV, `_HOST_HEALTH` and permission for disposable PG16.15 are separately blocked. No live integration is authorized by this report.

## 6. Hold point, next work, and explicit result

**Inventory-A: useful entrypoint findings confirmed, but full exhaustive sweep is still required before the P0 hold can be considered complete.** Next read-only work: review remaining `src/` and `docs/` references, external/deployment scripts and current source-transfer diff, obtain a fresh scoped Codex AI assessment of admission inventory and owner-specific decision points. No owner decision is silently recorded by creating this document.

**State: P1-F technically acceptable FOR OWNER REVIEW; P0/P1 operational admission inventory OPEN; protected edits NOT AUTHORIZED; F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**


---

## 7. P1-G extended tracked-source inventory addendum — completed 2026-11-19

**This addendum supersedes only the incomplete-coverage description in Sections 2 and 6 above; it does NOT change the security disposition or grant the owner’s approval.**

### 7.1 Extended audit method and coverage

All observations are anchored to **P1-F source-tested commit `06c3c374232bb4fad6e549c322e3a1f6bdd0d195`**. GitHub's recursive tree was reported **not truncated**, with **593 tracked file blobs**, divided into:

| Tracked path class | Files | Direct-reference audit |
|---|---:|---|
| `sales-forecast-platform/src/` | **244** | All **239** `.py/.json/.yml` source/config entries and **5** remaining tracked `.md/.sql/.save` source-support files inspected |
| `sales-forecast-platform/tests/` | **187** | **69** runtime test modules, **37** PostgreSQL test/launcher files, **81** other test/native/persistence files inspected. Source-unit tests reading the manifest were distinguished from actual callable integration entrypoints |
| `sales-forecast-platform/docs/` | **84** | All 84 tracked documents inspected for operational runner commands, PASS-file instructions, and admission/archival references |
| Other tracked paths (including `.github/`) | **78** | 65 text/config/script/reference entries inspected for direct references and entrypoints; remaining binary/model/CSV/notebook/archive assets not interpreted as audited executable code |

The aggregate scan searched direct `run-manifest.json`, `run_postgres.py`, `run_b5_4_1_postgres.py`, `manifest_bytes`, `terminal_manifest`, `VerifiedCandidateManifest`, `admit_archive`, related invocation/admission terms, and checked candidate controller/entrypoint source independently. A separate `fetch_file` UTF-8 recovery successfully inspected root `README.md` and `sales-forecast-platform/output_nb.txt` after `fetch_blob` decoding refused BOM-prefixed content.

**Scope caveat:** This is a **tracked text-source reference sweep**, not a formal static call-graph proof. It does **not** inspect embedded code or historical files inside `*.ipynb`, `*.tar.gz`, binaries/models, PDFs/images or external/untracked/locally deployed files, nor dynamically constructed or encrypted strings. No local checkout or repository-wide code-execution analysis was performed. Therefore the statement is **“no additional direct references found in examined tracked text,” not “no possible consumer exists anywhere.”**

The broad source sweep did **not find another direct PG F-04 manifest admission reader in the 244 tracked `src/` files**, app/API/dashboard/jobs/monitoring/workflow text, or unrelated training tests. `model_candidate.py` and `local_dataset_repository.py` use their **own distinct model/dataset manifests**, not `run-manifest.json`. Their existence does not establish or bypass PostgreSQL F-04 evidence admission.

### 7.2 Additional discovered executable entrypoints — correction to Section 3

The earlier list of base runner, B5-1, B5-2 and B5-4-1 was **not exhaustive**. This expanded scan identifies a further, materially relevant **native + PostgreSQL persistence** route:

| New source | Exact evidence | Source-only risk and required disposition |
|---|---|---|
| `tests/persistence/run_authorized_persistence.py`, blob `817873f82131439d34e5fce2cef7a05e5720d50a` | Lines **21–26** require `--authorized-native-postgres`, but use `runpy.run_path(...'tests/postgres/run_postgres.py')`; lines **53–58** parse Docker inspect metadata and retain the same old name-based password heuristic; line **139** calls `pg['run']` directly, **without the synchronous F-04 controller**; line **140** prints a separate B4b `PASS` indicator after normal return | **Separate runnable host integration**, not remediated by P1-F's bounded inspect projection. Must be **explicitly in future protected-transfer scope with tests, or excluded/held NO-GO**. Do not infer that an existing native host-admission handle substitutes for F-04 PostgreSQL evidence admission. |
| `tests/persistence/test_interruption_recovery.py`, blob `63f53811b246487fd6729719f0388d911d18fda0` | Executable `main()` at lines **23–29**; lines **84–87** invoke that other genuine persistence runner; lines **88–91** read `run-manifest.json` directly and assert `result=='PASS'` / `cleanup_status=='CLEANUP_SUCCESS'`; lines **92–94** write separate proof and print `interruption_recovery='PASS'`. No verified same-invocation receipt checked | **Direct raw-manifest PASS assertion in an executable integration script**, not a mere isolated unit test. It is currently an observed validation step, **not evidence of a demonstrated exploit or external production admission**, but must **not be treated as F-04-authoritative admission**. It must either use the separately approved bound controller receipt and same-run identity, or be explicitly blocked from making F-04-safe claims. |

**Important trust separation:** `src/handoff/host_admission.py` is a **native handoff authorization** mechanism, **not** the PostgreSQL `run-manifest.json` same-invocation admission controller. Similar terminology must not merge these trust domains.

### 7.3 Documentation/instructions needing alignment before any operational GO

- `docs/r7_05b5_4_1_pg16_execution_gate.md`: older human-inspection checklist considers manifest source status and `result: PASS` / cleanup fields as evidence. **This is insufficient as the authoritative admission path** given R2-04's ambiguous publication and file-swap scenarios. Treat it as a historical manual-check aid, not safe completion attestation. It also contains a historical independent-human-reviewer instruction superseded **only** by F04-G01 owner self-review for this personal project.
- `docs/r7_05b3_postgres_forecast_evidence.md` and `docs/r7_05b5_4_1_windows_host_preflight.md` document running the original harness/B5 wrapper. They must **not** be used to bypass the current F-04 NO-GO or provide live PG permission.
- `docs/r7_05b5_1_canonical_read.md` and `docs/r7_05b5_2_stored_forecast_accuracy.md` point to the B5-1/B5-2 wrappers. Preserve them as historical evidence, but **no operational F-04 safety claim** applies until their sources are separately scoped and verified.

**No automatic document overwrite or edits were made to these historical pages.** Their provenance remains intact. Any new operational runbook must explicitly require authoritative same-invocation receipt and maintain NO-GO for old entrypoints pending owner-controlled scope.

### 7.4 Updated inventory decision gates

**P1-G source-reference inventory: COMPLETED for tracked text at the pinned P1-F tree, with exclusions above; external deployment inventory and consumer assurance NOT complete.**

**Material direct-invocation paths needing owner disposition (five families):**
1. Base `tests/postgres/run_postgres.py` CLI;
2. B5-4-1 `tests/postgres/run_b5_4_1_postgres.py`;
3. B5-1 `tests/postgres/run_forecast_read.py`;
4. B5-2 `tests/postgres/run_stored_accuracy.py`;
5. Native/B4b persistence `tests/persistence/run_authorized_persistence.py` + `test_interruption_recovery.py`.

**Next NO-WRITE hold:**
- Have Codex perform a **fresh READ-ONLY P1-G review** of these exact source-only findings, especially whether the B4b interruption script's raw PASS is a non-authoritative test observation versus a consumer making acceptance claims.
- Owner must explicitly pick **narrow F-04 B5-4-1-only transfer with all other entrypoints held NO-GO**, or **an expanded separately reviewed multi-entrypoint rebaseline**. No inference from `Go`.
- Owner must accept/reject the **lossy inspect evidence projection** and define whether worker/test stdout, readiness output and host callbacks are trusted. If not, non-inspect output publication needs its own reviewed minimization policy.
- Before any protected-source patch: exact file-level proposed diff, mock-only operational-entrypoint `exception/None/raw PASS` negative tests, same-runtime/`ContextVar` binding proof and **fresh explicit scoped protected-write authorization**, with pinned SHAs. All permitted source changes must retain candidate `OFFLINE_REVIEW_ONLY` and absent CLI unchanged.
- Keep original `unexpected_manifest_secret` frozen-runner test unchanged and RED until authorized source correction makes it GREEN; no real Docker/PG, OCI or 16-wheel acquisition, merging, deployment or B5-4-2 advancement.

**No extra files changed by this read-only audit apart from this documentation addendum and the Issue #2 evidence entry. F-04 remains OPEN / NO-GO, B5-4-2 BLOCKED.**
