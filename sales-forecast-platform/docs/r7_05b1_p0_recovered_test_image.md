# R7-05B1-P0 — explicit recovered-image test compatibility

P0 adds a host-side test launcher, host stdlib unit tests and non-model worker
smokes. It does not implement producer issuance, change runtime composition or
run native model experiments. All accepted source and runners remain unchanged.

## Protected selection

`tests/native/run_recovered_image.py` is disabled by default. Trusted host startup
must explicitly pass `--use-validated-recovered-image` (or the exact Python boolean
`use_recovered_image=True`) plus explicit test file/node selections. No environment
variable enables it. There are no public image-ID, tag, manifest-digest or evidence
path overrides, no pull/build/retag operation and no fallback.

The source pins operator-reviewed identities:

- Recovered image: `sha256:417063b71e35c7a9889c10ac2516c328f16a483e065ec858df101eb9728c2df2`.
- Fresh manifest: `d199a85c61ed01e3ac8da447ae32a16cd606d2e029b5c42d76aa0d8d0cb0b3f5`.
- Evidence location: `/workspace/r7-recovery-evidence/fresh-20261028T045146Z-uc5_ii2b/fresh-manifest.json`.

A manifest digest proves agreement with the reviewed content; it does not
authenticate a caller or make filesystem storage durable. This is trusted-host
composition, not a Python sandbox or model-use capability. A host able to modify
Python globals or the Docker daemon is outside this boundary. The readiness dict
is not accepted as execution authority. Explicit test selection is not permission
to run native experiments; those still require separate user authorization.

## Evidence and invocation

The launcher bounds reads, rejects symlinks/nonregular files, duplicate JSON keys,
malformed encodings and incorrect digests. It validates schema/classification,
accepted Git baseline/tree, image/platform, Python/XGBoost versions and recorded
provenance. Eight essential supporting evidence files are rehashed against the
pinned manifest: base/distributions/embedded lock, successful installed-byte smoke,
worker configurations/outcomes, image discovery and container preservation.
Committed Dockerfile/version-lock hashes must also match. Wheel binaries and
historical build authenticity are not revalidated by P0; the fresh report's scope
and provenance limitations remain intact. Missing evidence fails closed.

Docker inspection must return the exact image ID, linux/amd64, no entrypoint and
the verified base-layer prefix. The launcher creates the worker by immutable ID,
inspects its actual configuration, and revalidates evidence/image immediately
before start. Mutable tags are never selected. Sequential host checks are not
atomic filesystem/daemon leases or cryptographic supply-chain attestation.

Only the current checkout's src/tests directories are mounted read-only. Network
is none; root is read-only; UID/GID is 1000; capabilities are dropped;
no-new-privileges, 1 GiB memory/no swap, one CPU, 64 PIDs, CPU ulimit 180 seconds,
256 MiB private tmpfs and a parent timeout of at most 240 seconds are enforced.
Proxy defaults are explicitly emptied on the container and the test process uses
env -i; host Docker credential configuration is preserved and never mounted.
Unexpected mounts, writable mounts, host namespaces or environment keys reject
before start. Cleanup and absence of the exact worker are checked after success,
nonzero exit, timeout or interruption. Failure to verify cleanup fails closed.

## Default guard and authority preservation

Accepted `run_b3.py` still requires the original
`sha256:8adc380a27f6dfde49066ac0ed323678783ca58e553272d3409e0fe9d04cf7c8`.
It neither delegates to this launcher nor accepts the recovered image. B1's
launcher, B1/B2 ownership and B3 execution authority are unchanged. No M7 payload
upgrade, production registry, current.json fallback or Predictor change exists.

## Non-native verification commands

From the project directory, host stdlib tests use actual pinned evidence and
mock every Docker invocation:

    PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests/native -p test_recovered_image_launcher.py -v

Real isolated non-model worker smokes and two accepted non-native worker checks:

    python tests/native/run_recovered_image.py --use-validated-recovered-image tests/native/test_recovered_worker_smoke.py tests/runtime/test_controlled_h1_execution.py::test_actual_disposable_worker_boundary tests/runtime/test_controlled_h1_execution.py::test_worker_timeout_and_cancellation_cleanup_contract

Only -q/-s and bounded explicit tests/*.py node selections are supported. Use --
before selections if passing pytest flags through the CLI. No default test suite
is selected. Host unit tests do not run inside the worker: recovery evidence is
intentionally not mounted there.

P0 verification performs no model fitting/loading/prediction, producer issuance,
application database writes, persistence, registry operations or deployment.
Next requires independent review of this commit and explicit authorization of
bounded genuine native revalidation; it does not begin R7-05B1 producer work.

## Recorded P0 evidence

Final host module: 55 unittest cases passed in one invocation. Real recovered-image
worker: 7 new smoke cases and 2 accepted B3 non-native worker cases passed together.
Total: 64 distinct cases. Earlier overlapping reruns are excluded. The first
worker trial selected a file before its creation; the next exposed pytest's own
environment keys, which were added to the smoke assertion. Neither trial changed
isolation, application code or accepted runners; their logs remain separate.

Additional real-worker probes returned 124 for a 0.25-second parent timeout and
4 for an intentionally nonexistent pytest node. Both removed their exact workers;
these probes are not added as repository cases. Unit tests cover interruption,
creation/cleanup failures, bad manifests/proofs, substituted/missing images,
unsafe arguments, disabled defaults, host namespace/mount/credential rejection,
and original-default-guard rejection of the recovered ID. No framework/model or
database operations were executed by P0 verification.
