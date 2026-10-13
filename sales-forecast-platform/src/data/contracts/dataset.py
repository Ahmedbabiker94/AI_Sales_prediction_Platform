"""Private staging envelopes. None is an admission or publication credential."""
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
        if self.kind not in ('upstream_unverified', 'transaction_scoped', 'external_snapshot'):
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
