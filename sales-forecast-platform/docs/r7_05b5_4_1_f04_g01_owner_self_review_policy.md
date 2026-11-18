# F-04 / G-01 — Owner-directed self-review governance amendment

**Effective decision:** 2026-11-18. **Owner's explicit instruction:** the Sales AI Forecasting Platform is the owner's **personal project**; there is currently **no independent human security reviewer**. The owner chooses Codex as an **AI technical review adviser** and will personally assess the findings, risks, and authorization decisions.

**Scope:** Phase 7 → R7-05B5-4-1 → F-04 ONLY. This is a governance-process change, **not** technical security approval, code review completion, permission to patch the protected runner, or waiver of existing engineering safety gates.

## 1. Governing decision and transparent assurance label

The prior **mandatory independent human security-reviewer signoff** is **waived and replaced** for this personal project by this explicit, documented process:

1. A Codex agent acting as a *separate AI reviewer role* examines the pinned source, frozen accepted runner, candidate, tests and report, challenges the report, records source/line-specific findings, and recommends BLOCK / CHANGES REQUIRED / TECHNICALLY ACCEPTABLE FOR OWNER REVIEW. **Codex is not a human reviewer**; its assessment must **never** be described as independent human security signoff. No Codex findings have been received or approved as of this document.
2. The human **project owner personally reviews** the Codex report, verifies the specific findings against source/test evidence as far as feasible, and records an informed decision including unresolved issues and risks. The owner is the person accountable for acceptance; this is **owner self-assessment**, not independent human review. Owner review is required *after* Codex delivers its report; initiating Codex review does not count.
3. **Only after** that review, the owner may separately give **explicit, narrow security-only controlled-rebaseline authorization**. The authorization must identify the exact frozen baseline Git blob, reviewed candidate Git blob/commit, allowed files and change scope, required negative tests and residual risks. A generic “Go” or governance amendment **does not** constitute that authorization.
4. If the Codex review finds serious defects, or the owner is not satisfied, remain **NO-GO**. Apply fixes to review-only candidate first, run targeted RED→GREEN tests and rerun review; do not edit frozen accepted runner.
5. After an explicitly authorized patch, the **unchanged original frozen-runner `unexpected_manifest_secret` negative regression must pass on the corrected accepted runner**, without skipping, xfail or weakening. Other security/host/OCI/16-wheel/`_HOST_HEALTH`/disposable PG16.15 gates remain separate and blocked until their own requirements are met.

## 2. Exact artifacts at policy change

Repository: `Ahmedbabiker94/AI_Sales_prediction_Platform`; branch: `fix/r7-05b5-4-1-bootstrap`.

- Frozen accepted `sales-forecast-platform/tests/postgres/run_postgres.py` Git blob **`0863e81b733381a80b2f83560d8bbc231513ba41`** — **unchanged**, still protected.
- Review-only `sales-forecast-platform/tests/postgres/run_postgres_security_patch_candidate.py` Git blob **`dafd1822d58700663eaef378824c5c0e87d99428`**, with unconditional `OFFLINE_REVIEW_ONLY` default deny and no CLI entrypoint.
- [R1 consolidated evidence packet](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/blob/066200df46621542d1f0a80de38567c732db1230/sales-forecast-platform/docs/r7_05b5_4_1_f04_r1_consolidated_independent_security_review_packet.md) remains a **historical, pinned review dossier**; its old mandatory-independent-human-review instructions are superseded by **G-01 only for approval workflow**, not for technical facts, technical limitations or evidence.
- Last code-tested candidate: commit `67d0284992ed425f69571792fb09cda61d6c0edd`, CI [44262801885](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44262801885): **295 passed / 1 failed / 296 collected per Python 3.11 and 3.12**; unchanged original redaction RED persists. Focused offline [44262801941](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44262801941) succeeded; host prerequisites [44262801818](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/actions/runs/44262801818) failed. No real PG integration.
- All historical issue discussions and old R1/independent-human handoffs are preserved for audit. **Do not erase, retroactively reclassify, or claim those earlier independence conditions were fulfilled.**

## 3. What the owner explicitly accepts and does not accept

**Accepted process risk:** lack of a separate human security specialist increases the risk of undiscovered defects, correlated AI-review mistakes and overconfidence; the owner chooses to evaluate and accept/reject these risks personally for this personal project.

**NOT accepted or authorized yet:** any particular technical defect, insecure code path, unreviewed candidate delta, protected `run_postgres.py` edit, operational Docker/PG run, package/image download, merge, deployment, F-04 closure, or B5-4-2 advancement. The owner's **later** decision on the actual Codex findings is still required.

No other issue's independent review requirement is implicitly removed. If an external/customer/regulatory rule later mandates independent human review, this personal-project exception does not override that external obligation.

## 4. Required future owner decision record

Add a **new** comment to [F-04 Issue #2](https://github.com/Ahmedbabiker94/AI_Sales_prediction_Platform/issues/2) after obtaining Codex's full report and completing the owner's review, documenting:

- **Codex report**: link/commit/date; source-scoped decision; every Critical/High finding and disposition.
- **Owner personal assessment**: date, rationale for accepting/rejecting Codex findings, unresolved risks, test evidence and known limitations; explicitly `OWNER SELF-REVIEW — NO INDEPENDENT HUMAN SECURITY REVIEW`.
- **Owner decision**: `REJECT / CHANGES REQUIRED / TECHNICALLY ACCEPTED FOR CONTROLLED REBASELINE CONSIDERATION`. Never re-label it as “independent human signoff”.
- **Separate protected-runner authorization (if later granted)**: explicit `AUTHORIZE SECURITY-ONLY CONTROLLED REBASELINE`, baseline blob SHA, candidate blob SHA, exact files/delta, tests, constraints and exclusions; this is **not** granted by G-01.
- **Remaining engineering gates**: original redaction RED, OCI/Docker29 no-pull, 16-wheel provenance/OSV/licenses, `_HOST_HEALTH`, authorized disposable PG16.15 integration.

**Current status:** Codex report pending; owner technical review pending; **no controlled rebaseline authorization**. F-04 **OPEN / NO-GO**; B5-4-2 **BLOCKED**.
