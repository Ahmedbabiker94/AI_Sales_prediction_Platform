# Phase 7 / R7-05B5-4-1 / F-04 — P1-F: Allowlisted Docker-inspect publication projection

**Date:** 2026-11-19
**Authorization:** Candidate-only P1 offline tests and planning, as previously directed by the owner. **NOT** protected runner/wrapper/controller authorization, executable transfer, no-pull Docker/image or wheel acquisition, real PostgreSQL, merge or deployment.
**Status:** New candidate-only synthetic security cases PASS; new Codex AI review PENDING. **F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**

## 1. Security issue from the owner-supplied Codex P1-E review

Codex returned **CHANGES REQUIRED (HIGH)** on code-tested commit `e40e03cd9d527eb8a864b2ecc4ae62fd634388cb` and candidate blob `1189bd02f6f001d1285cea61fe6d9588618d5ff2`. The P1-E parser safely validated `Config.Env` as a list of simple `KEY=VALUE` strings and prevented `AttributeError.obj`, but it still **published arbitrary untrusted Env names and values**. An inspect response could split a genuine generated disposable secret across two or more individually valid Env assignments (or hide a fragment in an assignment key), escaping **exact contiguous-secret redaction** and producing a valid `PASS` manifest with a matching admission digest. Digest binding is not confidentiality.

**Design chosen:** change the candidate's Docker-inspect evidence contract. Do **not** try to detect every possible secret concatenation or encoding. **Do not publish arbitrary Env keys, values or unrelated source inspect fields at all.** Record only a reviewed projection with approved variable names and fixed/small known-safe diagnostic values.

## 2. Pinned identities

Repo `Ahmedbabiker94/AI_Sales_prediction_Platform`; branch `fix/r7-05b5-4-1-bootstrap`.
**Final P1-F CODE-TESTED commit:** `06c3c374232bb4fad6e549c322e3a1f6bdd0d195`.

| Source (paths relative to `sales-forecast-platform/`) | Git blob SHA / status |
|---|---|
| Protected frozen `tests/postgres/run_postgres.py` | `0863e81b733381a80b2f83560d8bbc231513ba41` **UNCHANGED** |
| Protected operational `tests/postgres/run_b5_4_1_postgres.py` | `040d0d30bb719d646eb350b229ccd9a4c5f2bf79` **UNCHANGED** |
| Review-only `tests/postgres/run_postgres_security_patch_candidate.py` | `54fbe8292f7e8054ff682cadb97812683992cd1e` **NEW P1-F source; Codex review pending** |
| `tests/runtime/test_r7_05b5_4_1_f04_r2_04_r4_actual_reporting_stage.py` | `a0a41ec4c0a30b0807fc423e384d99f8c8cd8140` |
| Synchronous `tests/postgres/run_manifest_admission_controller_candidate.py` | `f8ffa51dbf072deaad0ab7e3701965a05b471548` unchanged |
| Candidate `tests/postgres/run_b5_4_1_reporting_stage_candidate.py` | `5a82cb0219948c0b895f92b603252a547957bed7` unchanged |
| Repository-root `.github/workflows/r7-b5-4-1-f04-candidate-offline.yml` | `daa477ceba84bd7a6a85dcb4d40fa33fa0452aea` unchanged and already includes expanded tests |

The candidate's `command()` **still unconditionally raises `OFFLINE_REVIEW_ONLY` as its first executed statement** and the candidate has **no CLI entrypoint**. Fake command/signal/daemon resources are used only by offline mocks. No protected sources were patched.

## 3. P1-F projection contract (candidate-only; no operational guarantee)

The parser retains P1-D's `JSONDecodeError` protection and P1-E's strict outer `list[dict]`/dict `Config`/list `Env` schema. It additionally:
- Requires each Env name to be ASCII and match a conventional variable grammar (letter or underscore first; thereafter only letters, numbers and underscores). Control/NUL, space and unsupported keys fail closed with a constant `RuntimeError`.
- Rejects repeated Env names, even if one value is safe or the name would later be omitted; no ambiguous duplicates enter a successful projection.
- Emits **only** `{'Config': {'Env': projected_env}}`. All other raw inspect metadata fields, unknown names and unknown values are **dropped**, not even masked under their original key.
- Masks the exact supported sensitive **NAME** set (`POSTGRES_PASSWORD`, `PGPASSWORD`, and `R7_PG_{ADMIN,MIGRATOR,WRITER,READER}_PASSWORD`) with fixed `<test-secret>`; no original value retained.
- Preserves only explicit **known-safe literal value pairs**: `SAFE_FEATURE_FLAG=enabled`, `HTTP_PROXY=`, and `NAME=a=b`. If one of these approved names has another value, return the same name with `<redacted-env-value>` and **never echo** its raw contents.
- Keeps existing empty Env list support.
- Replaces both server and foundation/restart client call-site transformations with the **parser's already-sanitized projection**; no second-stage `'PASSWORD=' in string` heuristic may reintroduce unsafe values.

**Trade-off:** this intentionally removes formerly published arbitrary inspect details. The literal `SAFE_FEATURE_FLAG`, `NAME` and `HTTP_PROXY` cases are deliberately narrow test diagnostics, **not a statement that all Docker Env variables or values are safe**. Any operational dependence on the former full inspect structure or exact raw error classes/messages **must be separately audited before rebaseline**. A trusted host assumption remains; potential secrets in unrelated runner evidence, exception frame locals and memory dumps are outside this particular projection proof.

## 4. Test-first RED and correction

Added **23 P1-F cases before runtime correction**:
- **18** negative/safe-projection paths = 3 actual inspect destinations (server, foundation, **restart**) × 6 synthetic attacks: two Env-value fragments, four fragments, split between key and value, full generated credential under unexpected variable name, unknown nested inspect metadata combined with fragments, and an approved HTTP_PROXY name carrying a secret fragment.
- **3** conflicting duplicate approved Env-name cases (all three destinations), requiring fixed fail-closed error and denied admission.
- **2** positive cases confirming approved `POSTGRES_PASSWORD=<test-secret>`, `SAFE_FEATURE_FLAG=enabled`, `HTTP_PROXY=`, and `NAME=a=b`, with unknown variables and untrusted extra metadata omitted from the immutable, verified PASS snapshot.

**[RED CI 44477966000](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44477966000)**, test-only commit `0660513dcb8f0ce1574736e5f97136716a710365`: both Python 3.11 and 3.12 **475 passed / 24 failed (499 collected)**. Of the 24 failures, **23** were new P1-F cases, the remaining one the unchanged frozen-runner regression.

The candidate projection code landed at `1abf95e8e194bf8f04bd1f3f4c917f3ce2016f6c`. A temporary run [44478172066](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44478172066) yielded **492 passed / 7 failed** per Python. Six P1-F test expectations incorrectly insisted that:
- A split credential containing a disallowed key character must be safely **admitted**, whereas safe schema **rejection** is also permitted; and
- An untrusted `HTTP_PROXY` value must disappear entirely, whereas the fixed safe marker `HTTP_PROXY=<redacted-env-value>` is also permitted.

The tests were revised **without weakening secrecy, cleanup, or admission rules**: a split key must cause fixed-message `RuntimeError`, a failed manifest and no admitted receipt; an approved name with an unapproved value must use only the fixed marker. [**CI 44478339485**](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44478339485) then confirmed Python 3.11/3.12 **each 498 passed / 1 failed**. No protected regression was skipped/xfail/modified.

## 5. Additional adversarial fault coverage and FINAL CI

Added **12 further P1-F negative tests**:
- **6** = 3 inspect destinations × 2 secondary faults: a credential fragmented across two approved-syntax but **unknown** variable names, combined with (a) a mocked owned server-container cleanup refusal or (b) an intentionally credential-bearing terminal publisher exception. Confirm public fail-closed classification, zero fragments in terminal file if one exists, credential unlink, context reset and no admitted receipt.
- **6** = 2 inspect sites × 3 credential-bearing illegal Env names containing NUL, newline or space. Must reject before publication, avoid raw exception attributes, leave failed evidence, and remove or confirm absent envfile.

**[FINAL P1-F CI 44478534154](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44478534154)** on code-tested commit `06c3c374232bb4fad6e549c322e3a1f6bdd0d195`: **Python 3.11 and 3.12 each 510 passed / 1 failed (511 collected)**. All **35 P1-F cases** and previous candidate security tests passed. The **sole failed test remains the untouched protected runner** `test_run_finalization_fails_closed_on_negative_scenarios[unexpected_manifest_secret]`. The **overall CI conclusion is FAILURE**, not a green release gate. [Focused offline verification 44478534206](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44478534206) succeeded; [read-only host prerequisites 44478534128](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44478534128) failed. No real Docker, subprocess, OS signal or PG invoked.

## 6. Unverified boundaries and required next action

**Review questions for new Codex P1-F (READ-ONLY):**
1. Is dropping all raw inspection fields except a fixed `Config.Env` projection sufficient to avoid reconstruction of generated secrets split across valid assignments, **including in key names**, extra nested fields and restart inspect output?
2. Does the new explicit whitelist + fixed literal handling appropriately cover the intended synthetic business contract, while clearly breaking old raw-inspection compatibility? Are the hardcoded diagnostic names and values overly permissive or underspecified for operational transfer?
3. Are unsafe metadata parser/exception paths still exposed after strict schema validation, ASCII Env-key grammar, duplicate rejection, and early projection? Does raising from within any unsafe active handler allow raw `__context__`?
4. Confirm both positive fixture paths and all 35 new negative cases exercise the actual candidate B5 controller and preserve fail-closed cleanup/publication precedence.
5. Are there other manifest fields that can contain **reconstructable secret fragments**, independent of Docker-inspect Env? This projection alone must **not** be claimed to provide universal confidentiality.

P1-F is **synthetic, single-process**, not actual daemon quiescence, process cleanup, image or wheel provenance, filesystem crash durability, or real production-safe operation. Exception graph checks are not memory/traceback-frame-local sanitization. Archive/restart/cross-process admission remains **default deny**.

**Owner pending:** full P1 operational manifest-consumer and entrypoint inventory, source-transfer exact-sha risk review, **separate explicit F04-G01 owner authorization** before modifying protected runner/wrapper/controller. Original immutable frozen-runner negative regression must pass GREEN under a separately approved protected correction. Docker29/OCI no-pull, sixteen-wheel hash/provenance/license/OSV, host health and disposable PG16.15 remain separate gated requirements.

**Final: P1-F CANDIDATE TARGETED TESTS PASS; FRESH CODEX REVIEW PENDING. F-04 OPEN / NO-GO; B5-4-2 BLOCKED.**
