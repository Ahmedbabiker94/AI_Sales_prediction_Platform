# R7-05B5-4-1 — F-04 offline negative-test coverage update

**Status: IMPLEMENTED / NOT RUN / NO-GO.** The Codex environment could not access a usable shell or repository workspace. A connected GitHub inspection was used instead; this does NOT count as test execution or independent security signoff.

## Read-only coverage analysis

Reviewed the accepted `tests/postgres/run_postgres.py`, the B5-4-1 callback `run_b5_4_1_postgres.py`, and the existing `test_r7_05b5_4_1_pg_runner_safety.py`, `test_r7_05b5_4_1_git_provenance.py`, and candidate runtime reference tests.

**Already tested:** B5-4-1 stage registers an owned container before running Docker, failures/interruptions preserve ownership, no lifecycle rejects execution, credential unlink/symlink failure, evidence redaction, nested manifest scrub, Git dirty/wrong-root/unavailable handling, explicit opt-in, candidate reference helper wrong values.

**Added:** `tests/runtime/test_r7_05b5_4_1_f04_runner_negative.py`. Contains 14 parametrized offline cases:
- Foreign or missing ownership labels reject deletion for container/volume (4).
- Unknown Docker inspect failures are not treated as absence (2).
- Explicit missing-resource errors are distinguished from unknown failure (2); this does not establish deferred-daemon safety.
- Interrupted or failed Docker CLI spawn retains an already-recorded `DAEMON_UNRESOLVED` mutation (2).
- Foreign named Docker resource creation is rejected before process invocation (1).
- Old runner `inspect_image` rejects mismatched ID, platform or OS (3).

**Not yet demonstrated by test execution:** actual pytest collection/results for new file. No Docker/PG command was executed.

## Remaining acceptance coverage and blockers

1. Full `run()` finally-cleanup integration through mocked command sequences: late resources, mismatched label, ambiguous mutation followed by absent resources, expired reconciliation deadline, failed credential cleanup and unexpected secret in final manifest, each producing `CLEANUP_UNCONFIRMED` and `result=FAILED`. Avoid resetting security state or hiding existing failed tests. Exercise with separate offline fixtures and no daemon.
2. Verify B5-4-1 `_host_stage` cleanup ownership in end-to-end mocked lifecycle, not just callback registration. Verify all published evidence remains redacted under unexpected fields and failures.
3. Candidate-only F-02 immutable OCI no-pull reference formation and client iidfile/hash/architecture tests; the accepted runner is *not* yet wired. Do not modify accepted runner, Dockerfile or lock without controlled rebaseline approval.
4. Independent review of third-party licenses and provenance, trust boundary, daemon cleanup failure modes and the earlier intermittent `HostHealth` test failure.
5. After explicit controlled-rebaseline approval and separate disposable database opt-in, PC/WSL Docker integration and cleanup evidence inspection **before closing B5-4-1**.

## Reproduce new tests later

From a **synchronized, clean** `sales-forecast-platform` checkout, after a conscious local test step, execute inside the previously validated restricted candidate-v2 container (read-only mount, no network, UID 1000):

```bash
docker run --rm --platform linux/amd64 --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --user 1000:1000 --pids-limit 64 --memory 1g \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --mount "type=bind,src=$(pwd),dst=/code,readonly" \
  --entrypoint python b5-4-1-client-candidate-v2:local \
  -m pytest tests/runtime/test_r7_05b5_4_1_f04_runner_negative.py \
  -q -x --tb=short -p no:cacheprovider
```

**R7-05B5-4-1 OPEN / NO-GO.** No PostgreSQL integration, runner rebaseline, merge, deployment or B5-4-2.
