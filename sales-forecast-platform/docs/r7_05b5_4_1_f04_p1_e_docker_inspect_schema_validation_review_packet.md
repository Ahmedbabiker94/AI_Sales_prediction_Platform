# Phase 7 / R7-05B5-4-1 / F-04 — P1-E: Docker-inspect metadata structure validation

**Recorded:** 2026-11-19. **Scope:** owner-authorized P1 **candidate-only offline negative tests** under planning-only decision A. This is **not** authorization for protected runner/wrapper edits, source promotion, a protected rebaseline, Docker/PostgreSQL, OCI/wheel operations, merge or deployment. **F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**

## Reviewer-triggered HIGH finding

Codex's read-only P1-D review at code-tested commit `5ea518216322fe8abd2d8b229e6c15ac737453b9` and candidate blob `47d54f967cabb45808d1d7466c48cb6629dbac2c` concluded `CHANGES REQUIRED`. The P1-D helper correctly suppressed malformed `JSONDecodeError.doc`, but returned the first syntactically valid JSON element without validating structure:

1. `Config.Env` containing a credential-bearing dictionary entry could trigger `AttributeError.obj` through the subsequent `s.split(...)`, potentially exposing a disposable credential.
2. `Config.Env` as a single string caused the comprehension to iterate characters, breaking the contiguous secret before the exact-string finalizer. A wrongly transformed artifact could therefore be `PASS` and meet the in-process controller's exact byte/digest check.
3. Missing, null, empty and unexpected metadata structures could raise inconsistent primary exceptions, with no safe schema boundary.

These are candidate-source-derived scenarios, **not observed real Docker incidents**.

## Exact code-tested identities

Repo `Ahmedbabiker94/AI_Sales_prediction_Platform`; branch `fix/r7-05b5-4-1-bootstrap`. **Final code-tested commit: `e40e03cd9d527eb8a864b2ecc4ae62fd634388cb`.**

| File | Git blob | Status |
|---|---|---|
| `tests/postgres/run_postgres.py` | `0863e81b733381a80b2f83560d8bbc231513ba41` | **UNCHANGED protected runner** |
| `tests/postgres/run_b5_4_1_postgres.py` | `040d0d30bb719d646eb350b229ccd9a4c5f2bf79` | **UNCHANGED protected operational wrapper** |
| `tests/postgres/run_postgres_security_patch_candidate.py` | `1189bd02f6f001d1285cea61fe6d9588618d5ff2` | **NEW P1-E candidate-only schema boundary** |
| `tests/runtime/test_r7_05b5_4_1_f04_r2_04_r4_actual_reporting_stage.py` | `fd303b6fd34d34d767d6bb9138c471870bf6be5a` | Expanded negative and positive tests |
| `tests/postgres/run_b5_4_1_reporting_stage_candidate.py` | `5a82cb0219948c0b895f92b603252a547957bed7` | Unchanged R4 reporting stage |
| `tests/postgres/run_manifest_admission_controller_candidate.py` | `f8ffa51dbf072deaad0ab7e3701965a05b471548` | Unchanged trusted synchronous bound admission |
| Repository root `.github/workflows/r7-b5-4-1-f04-candidate-offline.yml` | `daa477ceba84bd7a6a85dcb4d40fa33fa0452aea` | Unchanged explicit offline CI inventory |

All code/test paths besides the workflow are relative to `sales-forecast-platform/`.

## Test-first RED

Added a parameterized matrix driven by the **actual candidate runner + B5 reporting-stage/controller path**, but with all Docker commands, signal handlers, resource removals and publication faults synthetically mocked:

- Both **server** and **foundation client** Docker-inspect sites.
- Valid JSON with `Env` as a string containing a generated disposable password, or a list with a credential-bearing dict (the `PASSWORD=` membership path), nested list, integer, null entry, or an improperly typed container.
- Empty/null/missing outer list, unexpected first element, missing/null/array `Config`, missing/null/empty-string `Env`, and multiple outer entries.
- The direct character-array variant `list('PGPASSWORD='+secret)` demonstrates **reconstruction by joining entries despite the absence of any contiguous secret string in JSON**. A further case rejects an Env list entry that is not `KEY=VALUE`.
- Confirmed/unconfirmed owned cleanup for all shapes; **publisher fault combinations** additionally exercised for the two original critical shapes. Verify expected public failure class, safe exception `doc`, `object`, **`obj`**, recursive cause/context, formatted tracebacks, retained/published status, no successful admission, no raw secret in any accepted file, owned resource cleanup and handler/ContextVar reset.
- Strengthened earlier P1-D coverage from either credential-file disposition to **exact `NOT_PRESENT` for pre-envfile server failure and `REMOVED` for client failure**, plus absence of the envfile on disk.

[**Initial RED CI 44469440054**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44469440054), tests-only commit `d59c7ed6934198251e13142deadca7c34fabfa74`: Python 3.11 and 3.12 each **393 passed / 73 failed** (72 new negative failures, 1 unchanged original protected redaction RED). The expanded character-array/non-assignment tests were subsequently added test-first at `4e444eaa1d89fb5734599e8b5234dd3cafbee45c`.

## Narrow candidate-only P1-E correction

The existing `parse_disposable_inspect_metadata_candidate(raw_stdout)` first retains its earlier `JSONDecodeError` suppression. Then, **before returning parsed data to either caller or allowing an Env transformation**, it requires:

- An exact built-in `list` containing **one** inspection result (one container was requested).
- That element must be a built-in `dict`.
- `Config` must be a built-in `dict`.
- `Config.Env` must be a built-in `list` (empty Env list permitted) with **only plain strings**; each entry must have a nonempty key followed by `=` and a value. This prevents a string-as-list, malformed item, or per-character list from passing silently.

Any unsupported schema yields a **fixed** `RuntimeError('disposable PostgreSQL inspection metadata schema invalid; details suppressed')` with no unsafe `AttributeError.obj`, document-bearing parsing exception or raw object in the tested public exception chain. Both server and client parse sites share this one function; no command, image pin, ownership, stage selector, source snapshot, manifest finalization or admission contract was changed.

**Important semantic note:** The additional `KEY=VALUE` requirement is a deliberate fail-closed format restriction, not merely a Python type check. A future Docker version or integration returning legitimate alternate Env entries would require a separately reviewed compatibility decision; current synthetic evidence alone cannot certify all Docker output formats.

**No operational execution:** The candidate `command()` still unconditionally throws `OFFLINE_REVIEW_ONLY` as its **first executed statement** and the candidate has **no CLI entrypoint**. Tests never invoke Docker, PostgreSQL, subprocesses, actual signals, or production credentials.

## RED → GREEN and final code-tested CI

After the parser was corrected at `ab59977944ed8eb57186aa558c6215b7b248ad3e`, all newly added security rejection cases reached their fail-closed branch. An interim CI revealed a **test fixture mistake**, not a schema implementation failure: the character-array JSON intentionally did **not** contain a contiguous password, whereas the test had expected one. The `null` top-level shape also cannot contain a password. Those assertions were corrected while retaining reconstruction checks, the rejection condition and every cleanup/admission requirement.

[**CI 44469846478**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44469846478), test-fixed commit `ed77d759dc542687cab9c413ee94ee3e86f4799c`: Python 3.11/3.12 each **473 passed / 1 failed**. All **80 new negative** P1-E cases passed.

Two additional **positive** tests verify both server and foundation-client paths still accept properly structured `Config.Env` arrays (e.g., `POSTGRES_PASSWORD=<disposable secret>`, `SAFE_FEATURE_FLAG=enabled`). The known password is replaced with `<test-secret>` before the final manifest, and a verified immutable controller receipt is returned with exact bytes and digest.

[**FINAL CI 44470033355**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44470033355), final code-tested commit `e40e03cd9d527eb8a864b2ecc4ae62fd634388cb`: Python **3.11 and 3.12 each 475 passed / 1 failed (476 collected)**. All **82 new P1-E cases** pass; prior candidate and controller negative tests remain intact. The **sole failed test is the untouched protected accepted-runner** `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]`. It was not edited, skipped, xfailed, or weakened. **Overall CI remains FAILURE**, not a release gate.

[Focused offline run 44470033338](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44470033338) succeeded; [read-only host prerequisite run 44470033417](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44470033417) failed. No real Docker/PG run was authorized.

## Limits / Codex review questions / next gate

- Codex has **not yet reviewed P1-E**. Require a fresh **READ-ONLY** AI technical review of the exact updated candidate/test blobs. Challenge the one-result `list[dict]`, `Config.Env list[plain KEY=VALUE str]` contract, direct vs indirectly split secrets, any other inspect structure/content concerns, and whether the new strict parsing could change legitimate external behavior. AI opinions are not independent human signoff.
- Public exception `.doc/.obj` and recursion checks are not memory erasure or traceback-frame-local secrecy. Avoid production logging frameworks that capture exception frame locals; real host/signal/filesystem and Docker/PG behaviors are still unverified.
- The protected operational B5 wrapper continues to use the frozen runner directly, with **no operational mandatory admission controller**. P1 operational entrypoint/consumer inventory remains open. Archives/restarts/cross-process admission remain default-deny.
- F04-G01 still requires a **new separate explicit owner authorization** before any protected runner/wrapper/controller rebaseline. The frozen original RED must then become GREEN through a reviewed source correction **without altering its test**. Docker29/OCI no-pull pins, candidate-v2 sixteen-wheel provenance/hash/license/OSV, `_HOST_HEALTH`, and explicitly authorized disposable PostgreSQL16.15 remain separately blocked.

**P1-E TARGETED CANDIDATE OFFLINE TESTS PASS (475/1 per Python), FRESH CODEX REVIEW PENDING. F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**
