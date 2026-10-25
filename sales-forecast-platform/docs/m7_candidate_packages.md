# M7 candidate packaging and tracking

Scope: same-process, host-operator-controlled, local offline candidate evidence.
No production approval, model use, registry stage, synchronization or deployment.
No genuine fitting or native serialization/loading has been verified in M7.

## Dependency inventory and composition

- M6 `CanonicalAdapterTrainingService` retains `(owner, exact reference,
  metadata, private model, original frozen state)` records. Its public metadata
  and candidate ID do not authenticate ownership. M7 pins that original trainer
  and checks the exact record and reference, rejecting copied trainers/references.
  This narrow read-only adapter depends on the current private M6 record layout;
  it neither changes the M6 registry nor extracts or calls the private model.
- M2 `RepresentationContract` supplies strict immutable contracts, guarded
  constructor/Python/JSON/string validation, recursive duplicate-key rejection,
  canonical serialization and identities. Unsupported raw TypeAdapter/compiled
  validators and deprecated parse APIs are not untrusted ingress.
- M3/M4/M5 original frozen states are privately revalidated. M7 preserves state,
  canonical schema, ordered group schemas, original plan/publication, fold and
  actual fitted-origin references. It does not export raw state or training data.
- M6 retains M2 compatibility identity and exact fit-input digest/geometry, not
  each transient M2 output representation ID. M7 records those retained identities
  and a distinctly named `m6_retained_input_binding_v1` derived binding; it does
  not fabricate a historical M2 representation ID. Native portable packages
  needing additional bindings/state content remain unavailable.
- Existing `src/training/registry.py`, `src/ml/model_registry.py`,
  `src/ml/mlflow_logger.py` and production artifact sync use MLflow registration,
  mutable aliases or production paths. None is imported/reused. The existing
  local dataset repository supplies a useful no-follow/dir-fd pattern but its
  dataset/SQL lifecycle is not a candidate artifact store. Existing metadata
  repositories handle store/jobs/forecasts, not immutable candidate packages.
- B04's legacy save/load quarantine stays unchanged. M7 does not unpickle,
  joblib-load, torch-load or invoke legacy native loading.

Trusted host composition creates an owner-only dedicated directory, outside all
production/model-runtime/network/synchronized storage, then supplies its absolute
path to `CandidatePackageRepository`. Compose repository ->
`CandidatePackageProvider` -> `CandidatePackagingService(original_trainer,
provider, authorize_packaging=operator_callback)`. The destination, provider,
trainer and callback must never come from request data. The repository creates
only its `candidate-packages-v1` namespace. There are no default destinations.
The host must protect and select the root; path names alone cannot authenticate
whether an arbitrary location is production storage.

`service.package(original_reference)` reports UNAVAILABLE. Explicit
`test_only=True`, an original test-stub candidate and a fresh trusted approval
are all required for stub evidence publication. The opt-in Boolean is not
approval. Callback exceptions/denial/discard and reentrant requests fail closed.
`service.verify(package_id, candidate=original_reference)` verifies integrity and
compares the complete manifest against the original private owner evidence and
fresh approval, without native operations. Repository/provider verification by
ID alone proves byte integrity, not original ownership or model-use authority.

## Manifest and identity

`CandidatePackageManifest` 1.0 is CANDIDATE_ONLY and
TEST_ONLY_NON_DEPLOYABLE. It includes immutable M6 training metadata (family,
adapter, configuration/seed, input geometry/dtype/digest, preparation and M2
compatibility), original temporal plan/publications, fold and exact fitted-origin
scope, label-free frozen-state/schema references, family payload capability,
fixed creation context, payload filename/format/size/SHA-256.

Candidate ID is the original M6 metadata identity; it is not trained-weight
integrity. Package ID is SHA-256 of canonical manifest bytes. The filename is
`<package_id>.candidate`. Equivalent content yields identical bytes and IDs;
no wall clock, random issuance ID or filesystem path changes content identity.
Changing a payload changes its digest and hence package identity. Manifest
substitution under an existing ID rejects. A coherently rewritten manifest under
its own new ID can be structurally valid; original-owner verification still
rejects substituted state/schema/compatibility/plan evidence.

The fixed envelope is magic `M7CAND1\n`, four-byte big-endian manifest length,
canonical UTF-8 JSON manifest, then exact payload bytes. Manifest/schema/size/
digest validation is separate from native loading. Test payload vocabulary is
closed: version, TEST_ONLY_NON_DEPLOYABLE, candidate ID, family, and the literal
NO_MODEL_PAYLOAD. No serialized stub model, fake weights or arbitrary objects.
Opaque input/state digests may depend on sensitive/low-entropy data; hashes are
integrity references, not encryption or confidentiality guarantees.

| Family | Prospective native format | Native publication/loading | Test payload |
|---|---|---|---|
| XGBoost | UBJSON | UNAVAILABLE | test_evidence_json_v1 |
| LightGBM | native text | UNAVAILABLE | test_evidence_json_v1 |
| LSTM | tensor state dictionary | UNAVAILABLE | test_evidence_json_v1 |
| GRU | tensor state dictionary | UNAVAILABLE | test_evidence_json_v1 |
| Transformer | tensor state dictionary | UNAVAILABLE | test_evidence_json_v1 |

These prospective format declarations do not enable a loader. In particular,
no generic torch state-dictionary deserialization is implemented or certified.
Real native metadata cannot be relabeled as a test payload by the service.

## Storage, tracking and limits

All path components are opened as directories without following symlinks. Final
keys accept only lowercase SHA-256. File reads use no-follow/nonblocking opens,
regular-file/owner/size/mode checks and bounded reads. Verification never imports
a framework and rejects noncanonical manifests, corrupt/missing payloads,
unknown formats and extra bytes.

Publication exclusively stages in the pinned directory, writes bounded bytes,
fsyncs and makes the file read-only, then creates a hard link to the final key.
This is an atomic create-only publication: even a racing empty destination or
symlink cannot be overwritten. Final directory fsync follows; staging is removed.
Failures clean staging and, after a link, attempt to remove the failed final
publication. Storage failures can defeat cleanup/durability; they never produce
a reported success. A crash may leave a private orphan stage or a complete
untracked final blob; operators must reconcile these offline, without treating
files as authority. No automatic crash recovery or deletion job is introduced.

One envelope is at most 1 MiB; its payload at most 64 KiB. The store admits at
most 64 directory entries; the provider at most 32 candidate records. These are
cooperative local bounds, not hard OS/disk quotas, and trusted-host concurrent
store instances must be coordinated. No unbounded listing or production scan.
Final files are owner-read-only. Modes/digests protect accidents, not a malicious
same-OS-user process, root, or a hostile filesystem. Linux local filesystems with
hard links/fsync are required; network filesystems/Windows are not certified.

The provider stores immutable tracking snapshots, separates STAGED, FINALIZED,
INVALID and UNAVAILABLE, and refuses identity replacement/revival of an invalid
record. Repeated finalized publication is an idempotent verified lookup, not an
overwrite. Valid files can be looked up by exact ID after restart; staged/error/
unavailable snapshots are process-local and cannot restore M6 ownership. There
is no model name/latest pointer, production registry or production version.

Fresh ownership/approval checks are sequential, not atomic revocation leases.
A host callback is a trusted operator boundary, not cryptographic identity or
independent durable approval evidence. M7 grants no prediction permission.
Portable native models, safe complete preprocessing packaging, actual loader
certification, durable multi-host tracking, M8 evaluation/ensembles, M9 approval/
promotion and Phase 7 runtime migration are deferred. Accepted standalone
configuration_digest diagnostics and legacy cleanup are unchanged/deferred.

Supported untrusted ingress is the service `package`/`verify` request arguments,
repository `publish`/`verify` under trusted provider composition, exact-ID provider
lookup/status, and inherited guarded M7 contract constructor/validate/JSON/string
methods. Fixed errors detach exception context/cause and never log payloads.
Underscore helpers and private registry mutation are not request APIs.
UNAVAILABLE is a capability observation: it can transition to explicitly opted-in
test evidence; INVALID and interrupted STAGED records cannot be replayed.
