# R7-05B5-4-1 — F-04 adversarial code review and deferred PC verification gate

**Decision: OPEN / NO-GO.** Focused independent-in-method source review by ChatGPT using the connected GitHub branch; **not independent third-party security sign-off**. No Docker, PostgreSQL, CI, production or model execution. New tests are **NOT RUN**.

## Scope reviewed (branch fix/r7-05b5-4-1-bootstrap)

- `tests/postgres/runtime_run_plan_candidate.py` and `tests/runtime/test_r7_05b5_4_1_f04_run_plan_candidate.py`.
- `tests/runtime/test_r7_05b5_4_1_f04_cleanup_lifecycle.py`.
- `tests/runtime/test_r7_05b5_4_1_f04_oci_negative.py`.
- Earlier safety/runner sources reviewed in `docs/r7_05b5_4_1_f03_evidence_f04_security_review_findings.md`.

## Confirmed source-level weakness in candidate run-plan validator (not the accepted runner)

The pure `validate_candidate_run_plan` checks **presence** of fixed safe flags and permits additional unparsed or contradictory arguments. It also checks only a textual prefix for the resource name and label, not actual UUID equality or a shared run identity, and does not require the image to be the last Docker-image-positioned token. An image string or one-use image ID by itself is not supply-chain provenance.

Examples that the *source logic* currently permits on an otherwise well-formed synthetic plan:
- `--network none` plus an additional split `--network bridge` (candidate only checks fixed `--network=none`).
- `--privileged=true` (only blocks the exact `--privileged` token).
- `--cap-add=SYS_ADMIN` (only requires `--cap-drop=ALL`; does not restrict extra add-back capabilities).
- `--mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock` (no mount allowlist in the validator).
- An extra `--user=0:0` on a PostgreSQL-server plan despite the presence of `--user=999:999`.
- Mismatched `r7-05b3-*` name and `r7_05b3_run=*` label values.
- An additional argument after the single expected image token.

**These source-level observations do not show that Docker ran them.** Exact semantics depend on actual Docker CLI parsing. Treat the contract as **insufficient for signoff** and independently verify it against a reviewed deterministic builder/parser with explicit allowlists before wiring the accepted runner.

## Adversarial regression evidence (test-only)

Added `tests/runtime/test_r7_05b5_4_1_f04_run_plan_adversarial_audit.py`: **9 unrun parametrized counterexamples**. The assertions intentionally expect the current validator to accept such adversarial synthetic commands. A passing test documents the validator's permissiveness, **not** a security success. If the checker is subsequently tightened, replace these assertions with explicit rejection expectations; do not preserve an insecure result for the sake of a green build.

Previously added F-04 tests: 66 pending executions (including one positive candidate baseline). **Total now 75 newly added test executions**, of which 9 are *demonstration-of-weakness*, not acceptance tests. **None has been executed here.**

## Review limitations and additional blockers

1. **Original approved runner remains unchanged:** bare old config digest in two PostgreSQL run sites; no explicit no-pull; original one-wheel client Dockerfile/lock. The candidate checker is not invoked by the runner. No no-pull guarantee has been established.
2. **PostgreSQL source isolation:** Host-only `_HOST_HEALTH` fail-stop previously failed once then passed five subsequent runs. Root cause remains unresolved. Never reset the security gate to make tests pass.
3. **Untrusted same-process Python:** dispatcher reference disclosure is demonstrated by the existing boundary audit; a true process trust boundary is required if hostile in-process code must be supported.
4. **Cleanup mocks are not daemon proof:** prior mocked finally tests must be executed/reviewed; mock time/reconciliation cannot prove absence of deferred daemon actions or secret persistence.
5. **Supply-chain and license approval pending:** PyPI exact SHA comparison and time-scoped OSV zero findings do not provide release provenance attestation or full software-bill-of-materials validation.

## Deferred PC validation package (no real database yet)

At the agreed end-of-B5-4-1 validation checkpoint, synchronize an isolated **clean Linux-native checkout** to the reviewed branch commit; do not run the database harness against the existing Windows .git pointer or dirty production worktree. In the checkout's `sales-forecast-platform` folder, use the previously built, isolated candidate-v2 image in a read-only, no-network container to run targeted test files:

```bash
docker run --rm --platform linux/amd64 --network none --read-only \
  --cap-drop ALL --security-opt no-new-privileges --user 1000:1000 \
  --pids-limit 64 --memory 1g \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --mount "type=bind,src=$(pwd),dst=/code,readonly" \
  --entrypoint python b5-4-1-client-candidate-v2:local \
  -m pytest \
  tests/runtime/test_r7_05b5_4_1_f04_runner_negative.py \
  tests/runtime/test_r7_05b5_4_1_f04_cleanup_lifecycle.py \
  tests/runtime/test_r7_05b5_4_1_f04_oci_negative.py \
  tests/runtime/test_r7_05b5_4_1_f04_run_plan_candidate.py \
  tests/runtime/test_r7_05b5_4_1_f04_run_plan_adversarial_audit.py \
  -q -x --tb=short -p no:cacheprovider
```

**Important interpretation:** all 75 cases passing would NOT be F-04 acceptance; nine intentionally document unsafe candidate-validation paths. Before any signoff, replace counterexample acceptance assertions with rejection tests against a corrected, reviewed candidate. Retest selected existing safety, OCI, source provenance, reporting request and boundary tests to detect interactions.

## Required next development/review stage

Design and review a hardened candidate checker with an explicit, syntax-aware Docker argv grammar, deterministic positioning of image and subcommand, a strict, per-stage option/mount/environment allowlist, immutable and locally present images, a bound resource UUID, and exact expected stages. Test negative cases for duplicate/split options, unauthorized capability adds, Docker socket and host/production mounts, unsafe uid override and conflicting network/pull behavior.

**No controlled-rebaseline runner/Dockerfile/lock edits before explicit approval.** Real disposable PostgreSQL 16.15 integration requires separate explicit opt-in, post-approval, before closing B5-4-1. No merge, deploy or B5-4-2.
