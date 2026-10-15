"""Capture and publication declarations; none authenticates a caller by itself."""
from dataclasses import dataclass
from enum import Enum
import re
from .versioning import ContractVersion
from uuid import UUID


class StagingState(str, Enum):
    STAGING = 'staging'
    SEALED = 'sealed'
    ABORTED = 'aborted'


@dataclass(frozen=True)
class StagingBudgets:
    max_rows: int = 100000
    max_batches: int = 1000
    max_columns: int = 256
    max_raw_bytes: int = 64 * 1024 * 1024
    max_canonical_bytes: int = 64 * 1024 * 1024
    max_chunk_bytes: int = 8 * 1024 * 1024
    max_index_bytes: int = 32 * 1024 * 1024
    max_unique_keys: int = 200000
    max_metadata_bytes: int = 2 * 1024 * 1024
    max_cell_bytes: int = 131072
    max_nesting_depth: int = 16
    max_elapsed_seconds: int = 300
    max_attempts: int = 1000
    max_concurrent_attempts: int = 1

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in vars(self).values()):
            raise ValueError('staging budgets require positive integers')
        if self.max_concurrent_attempts != 1:
            raise ValueError('local foundation supports one active writer per store')


@dataclass(frozen=True)
class ConsistencyEvidence:
    kind: str = 'upstream_unverified'
    reference: str | None = None

    def __post_init__(self):
        if self.kind not in ('upstream_unverified', 'transaction_scoped', 'external_snapshot', 'verified_reconciliation'):
            raise ValueError('unknown consistency evidence kind')
        if self.reference is not None:
            if type(self.reference) is not str or not self.reference.strip() or len(self.reference) > 512:
                raise ValueError('invalid consistency reference')
            self.reference.encode('utf-8', errors='strict')
        if self.kind != 'upstream_unverified' and self.reference is None:
            raise ValueError('verified evidence requires a trusted reference')


@dataclass(frozen=True)
class StoredDatasetReference:
    organization_id: UUID
    dataset_id: UUID
    attempt_id: UUID
    version_id: str

    def __post_init__(self):
        if any(type(v) is not UUID for v in (self.organization_id, self.dataset_id, self.attempt_id)):
            raise ValueError('dataset scope requires UUID identities')
        if type(self.version_id) is not str or not re.fullmatch(r'[0-9a-f]{64}', self.version_id):
            raise ValueError('invalid immutable version identity')


@dataclass(frozen=True)
class DatasetManifest:
    organization_id: UUID
    dataset_id: UUID
    attempt_id: UUID
    state: StagingState
    mapping_id: str
    mapping_version: str
    mapping_digest: str
    schema_version: str
    source_id: UUID
    ingestion_batch_id: UUID
    consistency: ConsistencyEvidence
    rows: int
    batches: int
    raw_bytes: int
    canonical_bytes: int
    # Immutable serialized canonical envelopes reuse StructuredDataset/lineage.
    envelopes: tuple[str, ...]
    # (ordinal, raw checksum, canonical checksum, rows)
    chunks: tuple[tuple[int, str, str, int], ...]
    version_id: str | None = None

    def __post_init__(self):
        if type(self.state) is not StagingState or type(self.consistency) is not ConsistencyEvidence:
            raise ValueError('unknown dataset state or consistency evidence')
        if any(type(v) is not UUID for v in (self.organization_id, self.dataset_id, self.attempt_id, self.source_id, self.ingestion_batch_id)):
            raise ValueError('dataset scope requires UUID identities')
        for value in (self.mapping_id, self.mapping_version, self.schema_version):
            if type(value) is not str or not value.strip():
                raise ValueError('dataset declarations must be nonblank')
            value.encode('utf-8', errors='strict')
        ContractVersion.parse(self.mapping_version)
        ContractVersion.parse(self.schema_version)
        if not re.fullmatch(r'[0-9a-f]{64}', self.mapping_digest):
            raise ValueError('invalid mapping digest')
        if any(type(v) is not int or v < 0 for v in (self.rows, self.batches, self.raw_bytes, self.canonical_bytes)):
            raise ValueError('invalid dataset counts')
        if type(self.envelopes) is not tuple or type(self.chunks) is not tuple:
            raise ValueError('manifest collections must be immutable')
        for chunk in self.chunks:
            if type(chunk) is not tuple or len(chunk) != 4 or type(chunk[0]) is not int or type(chunk[3]) is not int or chunk[3] <= 0:
                raise ValueError('invalid chunk declaration')
            if any(type(v) is not str or not re.fullmatch(r'[0-9a-f]{64}', v) for v in chunk[1:3]):
                raise ValueError('invalid chunk checksum')
        if self.batches != len(self.chunks) or self.batches != len(self.envelopes) or self.rows != sum(c[3] for c in self.chunks):
            raise ValueError('manifest count mismatch')
        if any(type(v) is not str for v in self.envelopes):
            raise ValueError('invalid serialized lineage envelope')
        for value in self.envelopes: value.encode('utf-8', errors='strict')
        if self.state is StagingState.SEALED:
            if not self.rows or self.version_id is None:
                raise ValueError('sealed dataset requires content and version')
            StoredDatasetReference(self.organization_id, self.dataset_id, self.attempt_id, self.version_id)


class PublicationState(str, Enum):
    """Publication state is separate from the immutable SEALED capture manifest."""
    REJECTED = 'rejected'
    FINALIZED = 'finalized'


def _opaque(value):
    from urllib.parse import urlsplit
    if (type(value) is not str or not value.strip() or len(value) > 512
            or urlsplit(value).scheme or '://' in value or re.search(r'(?i)(?:^|\s)(?:bearer|basic)\s+\S+',value) or any(ord(c) < 32 for c in value)):
        raise ValueError('bounded opaque reference required')
    value.encode('utf-8', errors='strict')


# Purpose vocabulary v1: exact publication scope, never a cross-purpose grant.
PUBLICATION_PURPOSES_V1 = ('production_training', 'offline_forecast_evaluation')


@dataclass(frozen=True)
class DatasetAccessContext:
    reference: StoredDatasetReference
    source_id: UUID
    ingestion_batch_id: UUID
    mapping_id: str
    mapping_version: str
    mapping_digest: str
    metric: str
    business_scope: str
    source_scope: str
    connection_reference: str
    purpose: str

    def __post_init__(self):
        if type(self.reference) is not StoredDatasetReference or type(self.source_id) is not UUID or type(self.ingestion_batch_id) is not UUID:
            raise ValueError('invalid dataset access identities')
        for value in (self.mapping_id, self.metric, self.business_scope, self.source_scope, self.connection_reference, self.purpose):
            _opaque(value)
        ContractVersion.parse(self.mapping_version)
        if not re.fullmatch(r'[0-9a-f]{64}', self.mapping_digest):
            raise ValueError('invalid mapping digest')
        if self.purpose not in PUBLICATION_PURPOSES_V1:
            raise ValueError('unsupported explicit publication purpose')


@dataclass(frozen=True)
class VerifiedConsistencyEvidence:
    """Declaration requiring a trusted verifier; it contains no self-authorizing tier.

    snapshot_transaction_v1: source-supported transaction snapshot for extraction.
    snapshot_external_v1: independently attested immutable source snapshot.
    reconciliation_authoritative_v1: authoritative source reconciliation over
    explicit bounds/change tracking, with documented method evidence. Not a snapshot.
    No method is verified by counts, ordering, hashes or pagination alone.
    """
    scope: DatasetAccessContext
    reference: str
    method: str
    method_reference: str
    policy_version: str
    window_start: object
    window_end: object
    verified_at: object
    expires_at: object

    def __post_init__(self):
        from datetime import datetime
        if type(self.scope) is not DatasetAccessContext:
            raise ValueError('invalid evidence scope')
        _opaque(self.reference); _opaque(self.method_reference)
        if type(self.method) is not str or self.method not in ('snapshot_transaction_v1', 'snapshot_external_v1', 'reconciliation_authoritative_v1'):
            raise ValueError('unsupported consistency verification method')
        if type(self.policy_version) is not str or self.policy_version != '1.0':
            raise ValueError('unsupported consistency policy version')
        dates = (self.window_start, self.window_end, self.verified_at, self.expires_at)
        if any(type(d) is not datetime or d.tzinfo is None or d.utcoffset() is None for d in dates):
            raise ValueError('evidence requires timezone-aware bounds')
        if not self.window_start <= self.window_end <= self.verified_at < self.expires_at:
            raise ValueError('invalid evidence validity window')


@dataclass(frozen=True)
class ConsistencyPolicy:
    scope: DatasetAccessContext
    quality_policy_id: str
    quality_policy_version: str
    policy_id: str = 'canonical_consistency'
    version: str = '1.0'
    allow_reconciliation: bool = False
    reconciliation_approval_reference: str | None = None
    # B has exactly this quality condition: existing fail-closed admission passes,
    # including only independently authorized warnings and no unexecuted actions.
    quality_condition: str = 'fail_closed_quality_admission'

    def __post_init__(self):
        if type(self.scope) is not DatasetAccessContext or type(self.allow_reconciliation) is not bool:
            raise ValueError('invalid consistency policy scope')
        for value in (self.policy_id, self.quality_policy_id): _opaque(value)
        ContractVersion.parse(self.quality_policy_version)
        if type(self.version) is not str or type(self.quality_condition) is not str or self.version != '1.0' or self.quality_condition != 'fail_closed_quality_admission':
            raise ValueError('unsupported consistency policy')
        if self.allow_reconciliation:
            _opaque(self.reconciliation_approval_reference)
        elif self.reconciliation_approval_reference is not None:
            raise ValueError('unexpected reconciliation approval')


@dataclass(frozen=True)
class ScopedWarningAuthorization:
    """Candidate evidence, never permission without an independent trusted verifier."""
    scope: DatasetAccessContext
    issue_code: str
    field: str
    action: str
    reference: str
    issued_at: object
    expires_at: object

    def __post_init__(self):
        from datetime import datetime
        if type(self.scope) is not DatasetAccessContext or type(self.action) is not str or self.action != 'keep':
            raise ValueError('only scoped KEEP warning authorizations are supported')
        for value in (self.issue_code, self.field, self.reference): _opaque(value)
        if any(type(d) is not datetime or d.tzinfo is None or d.utcoffset() is None for d in (self.issued_at, self.expires_at)):
            raise ValueError('warning authorization requires aware timestamps')
        if not self.issued_at < self.expires_at:
            raise ValueError('invalid warning authorization validity')


def operational_json(value):
    """Deterministic closed operational serialization; never object deserialization."""
    import json
    from dataclasses import asdict, is_dataclass
    from datetime import datetime
    def encode(item):
        if type(item) is UUID: return str(item)
        if type(item) is datetime: return item.isoformat()
        if is_dataclass(item): return asdict(item)
        raise TypeError('unsupported operational evidence representation')
    return json.dumps(value,default=encode,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def access_context_from_dict(value):
    data=dict(value);ref=dict(data.pop('reference'))
    for name in ('organization_id','dataset_id','attempt_id'):ref[name]=UUID(ref[name])
    data['reference']=StoredDatasetReference(**ref)
    for name in ('source_id','ingestion_batch_id'):data[name]=UUID(data[name])
    return DatasetAccessContext(**data)


def consistency_evidence_from_dict(value):
    from datetime import datetime
    data=dict(value);data['scope']=access_context_from_dict(data['scope'])
    for name in ('window_start','window_end','verified_at','expires_at'):data[name]=datetime.fromisoformat(data[name])
    return VerifiedConsistencyEvidence(**data)


def warning_authorization_from_dict(value):
    from datetime import datetime
    data=dict(value);data['scope']=access_context_from_dict(data['scope'])
    for name in ('issued_at','expires_at'):data[name]=datetime.fromisoformat(data[name])
    return ScopedWarningAuthorization(**data)
