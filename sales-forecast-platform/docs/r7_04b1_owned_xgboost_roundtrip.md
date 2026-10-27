# R7-04B1: original-owner XGBoost native round-trip

B1 permits genuine fitting and native reload only for explicitly authorized,
bounded synthetic controlled-offline validation. It does not authorize runtime
snapshot prediction, production use, forecast persistence or publication.

## Original-owner seam and trust

Accepted M6 is unchanged. CanonicalAdapterTrainingService.train selects the
accepted NativeBackend, fits training partitions only, runs final approval and
cancellation checks, and retains the tuple (original trainer, original opaque
CandidateReference, metadata, fitted native model, original frozen state).
OwnedXGBoostRoundTripService uses that same retained record, as M7's existing
owner bridge does, rather than reconstructing ownership from metadata/digests.

The constructor requires the exact M6 service and the original NativeBackend
factory. Export requires an original live reference, native_framework metadata,
XGBoost 3.2.0, XGBRegressor with completed fitting, the original M3 state, original
feature width, objective and fitted round count. Discard is revocation; no
reactivation API is added. Foreign/copied trainers/references, stubs and legacy
files/aliases are not admitted. No original model object leaves the service.

Protected host composition supplies authorize(metadata, stage, artifact_id,
validation_output_id). It must independently allow exactly the synthetic native
validation context. Stages are export, export_return, load_validation,
compare_validation and verify_return. Exactly True is required. This callback
is trusted Python, not cryptographic authentication or independent IAM. M6's
training approval remains separately required; no M9/R7 inspection receipt is
consumed, promoted or accepted as execution authority.

Host code, private dictionaries, callbacks and framework implementations must be
protected. A malicious host can alter Python internals; this is not an in-process
sandbox or proof against fabricated native backends installed by trusted code.

## Payload lifecycle

Export copies the genuine native Booster and binds original feature names and
candidate/state/training-input attributes to that private copy. It does not
mutate the original M6 model. Actual save_raw(raw_format='ubj') bytes form a
frozen process-local NativeArtifactEnvelope, with a versioned manifest including
complete M6 training metadata, frozen M3 state ID, ordered feature schema,
physical native input dtype, loader policy, attributes, payload size and SHA-256.
The artifact ID binds the canonical manifest and thus the payload digest.

The service retains the original envelope object, manifest encoding, bytes and
original owner record. verify requires all of them unchanged, rechecks the size
and digest, and never accepts caller-supplied files, byte strings, reconstructed
envelopes or coherent replacement manifests. Envelopes cannot be pickled/copied
through their serialization protocol. Structural construction of metadata alone
confers no ownership. Up to eight envelopes per service are retained; there is
no durable storage, registry, filesystem publication or restart restoration.

M7 is unchanged: its manifests remain TEST_ONLY_NON_DEPLOYABLE with
NO_MODEL_PAYLOAD. CandidatePackagingService returns UNAVAILABLE for the native
candidate even with test_only=True. B1 does not create an M7 package ID or a
parallel production registry. A native synthetic artifact is not deployable.

## Validation and numerical evidence

Only a validated M3 TabularOutput in the original validation partition, with the
exact original state, compatibility identity, family and ordered schema is
accepted. The host approval binds its complete output ID, including the actual
values and grid. M3 declarations are not ownership credentials: the host must
supply and authorize the actual synthetic provider-prepared output. This is
not the R7-02 snapshot-to-executable-input bridge.

No labels enter reload/parity verification. Accepted strict_float32 validates
raw cells before homogeneous conversion, including Boolean and precision/range
rejection. Validation is bounded to 32 rows, 1024 cells and 1 MiB native payload;
configurable ceilings can only be reduced. No missing-value imputation occurs.

The independently constructed XGBoost Booster reloads the actual owned UBJSON
bytes. It checks feature names/count, objective, round count and all bound native
attributes. Original XGBRegressor.predict and reloaded Booster.predict receive
the same ordered exact float32 matrix. Both results must be finite float32 of
shape (rows,), with absolute magnitude <=100,000,000.

Acceptance uses **zero tolerance and bitwise little-endian float32 equality**,
including signed zero. This is demonstrated for the pinned CPU synthetic fixture;
it is not a promise across framework versions, devices or arbitrary models.
The report contains prediction digests, not forecast records or quality claims.
Any numerical mismatch fails closed; no fallback tolerance or cast is used.

Cancellation/finite monotonic elapsed checks surround expensive operations and
follow every approval callback. All callbacks finish before the current owner
record is reread; that record must remain the original pinned record. Final
checks precede envelope/report return. Exact elapsed >= deadline rejects. Locks
reject concurrent/reentrant operations; native calls are indivisible. External
revocation after a completed check is not covered by an atomic lease.

## Disposable pinned environment and runner

No packages are installed into shared host environments. tests/native/Dockerfile
pins the Python base image by digest and installs requirements-b1.lock, including
xgboost-cpu==3.2.0, NumPy 2.3.5, pandas 2.3.3, SciPy 1.16.3 and sklearn 1.7.2.
All resolved Python dependencies are version-pinned. This is an environment lock,
not supply-chain signature attestation or an untrusted-payload sandbox.

Build with the managed local Docker daemon and the configured CA/proxy policy:

```sh
BUILDX_CONFIG=/tmp/r7-04b1-buildx docker --host=unix:///var/run/docker.sock build \
  --secret id=system_ca,src=/etc/ssl/certs/ca-certificates.crt \
  -t r7-04b1-test:local tests/native
python tests/native/run_b1.py
```

The runner resolves the image tag to an immutable local image ID, creates an
isolated subprocess container, then starts it with a 240-second host timeout.
It forcibly removes that exact container in finally, including on timeout.
Network is disabled; only loopback exists. Root filesystem and src/tests mounts
are read-only; host models/artifacts/data/home/.git/credentials/Docker socket are
not mounted. Inner environment is cleared, except explicit test variables.
Non-root UID 1000, dropped capabilities, no-new-privileges, 1 GiB memory/no extra
swap, one CPU, 64 PIDs, 180-second CPU rlimit and a 256 MiB temporary filesystem
bound execution. BLAS/OpenMP threads are one. Native tests require the runner's
explicit flag, non-root UID, loopback-only network and memory cgroup ceiling.

Docker kernel/container isolation is trusted, not a hardened multi-tenant model
parser sandbox. Memory/CPU/timeout controls can kill work; cleanup and fixed
errors do not prove power-loss durability. Host unshare/bwrap UID mappings are
unavailable here; no permissive host fallback exists.

## Verification scope and future work

The focused module performs genuine M6 native fitting on small finalized local
synthetic publications, with real canonical providers and preprocessing. It also
fits a second distinct seed candidate to test cross-candidate rejection. Other
negative cases reuse those real models; the explicit stub-rejection case alone
uses a fake estimator. No real dataset, production model or runtime snapshot is
used. Existing accepted M6/M7 tests remain stub-only where originally designed.

R7-04B2 must establish genuinely read-only source capture and original-owner
snapshot-to-M3 executable inputs without refitting. Existing SQLite catalog reads
can initialize schema/PRAGMAs; B1 does not change that accepted code. Synthetic
fixture catalogs are created only in container temporary storage.

R7-04B3 needs separately issued runtime load/execute permission, independent of
training, further-validation approval and readiness inspection; scope/expiry/
revocation/consumption and genuine end-to-end numerical acceptance are required.
B1 grants no runtime or production permission and does not wire APIs/jobs.
Durable native publication, all other model families, R7-05B persistence,
consumer migration, production lifecycle and deployment remain out of scope.

## Recorded candidate verification

The final image was sha256:8adc380a27f6dfde49066ac0ed323678783ca58e553272d3409e0fe9d04cf7c8
(Python 3.11.18; Pydantic 2.13.3/core 2.46.3, matching accepted serialization).
The full B1 module passed 53 cases in one invocation, with two genuine native
M6 fits (primary fixture and second-seed substitution candidate). Original and
reloaded prediction bytes on two validation rows had SHA-256
24be7b58d90fff229b6fbb049fca3e92e58fd42270504a5b9fc6a657e81b2c45.
The genuine UBJSON payload was 4,190 bytes. Its identity includes the particular
publication/candidate lineage; fresh fixture publications need not have the same
artifact identity. No cross-run artifact-byte determinism is claimed.

Accepted regressions: complete M2 representation module 235 passed; complete
M6 adapter module 98 passed; selected M3 frozen execution/scaler and M7
owner/no-payload/provider cases 25 passed. Total 411 distinct repository cases.
Reruns overlap and are not added. All final results used the pinned image above.

A combined regression invocation was killed at its CPU limit (exit 137), not
counted as a pass; separate bounded invocations completed. An initial image with
Pydantic 2.12.5 failed one accepted raw-serialization byte pin while canonical
digests matched; the identical failure reproduced on the unchanged baseline.
Pinning the accepted Pydantic version restored all 235 cases without changing
accepted code, fixture bytes or expected identities. Earlier B1 fixture assertion
failures were corrected before the final full-module run.

Independent environment probes confirmed loopback-only networking, the memory
cgroup ceiling, allocation-limit rejection, CPU-limit termination (exit 137),
and host timeout (124) followed by removal of the exact created container.
