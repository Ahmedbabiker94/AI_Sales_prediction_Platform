"""Authorized read-only FINALIZED versions; not wired into active training."""
from datetime import datetime, timezone
from time import monotonic
from contextlib import contextmanager, closing
from dataclasses import dataclass
import hashlib
import json
from uuid import UUID

from src.data.contracts.dataset import StagingBudgets, StoredDatasetReference
from src.data.contracts.asset import StructuredDataset
from src.data.contracts.mapping import SourceMapping
from src.data.contracts.series import SeriesDefinition
from src.data.processing.canonical_mapper import _identity
from src.providers.dataset_store_provider import StagingError
from src.services.dataset_finalization_service import trusted_callback, validate_admission_binding, verify_dataset_quality_evidence


@dataclass(frozen=True)
class VerifiedTrainingMetadata:
    """Provider-issued integrity/authorization context, not a caller credential.

    Nested legacy contracts are serialized; callers receive fresh copies.
    Trust comes from the configured provider, not constructing this declaration.
    """
    reference: StoredDatasetReference
    manifest_digest: str
    admission_digest: str
    purpose: str
    mapping_json: str
    source_id: UUID
    policy_id: str
    policy_version: str
    consistency_level: str
    series_json: tuple[str, ...]
    rows: int

    @property
    def mapping(self):
        return SourceMapping.model_validate_json(self.mapping_json)

    @property
    def series(self):
        return tuple(SeriesDefinition.model_validate_json(value) for value in self.series_json)


class _ReadSession:
    """Single-use trusted in-process lifecycle; not an adversarial Python sandbox."""
    def __init__(self, metadata, batches):
        self.metadata=metadata;self._batches=batches;self._started=False;self.completed=False

    def batches(self):
        if self._started: raise StagingError('read session is single-use')
        self._started=True
        count=0
        with closing(self._batches()) as stream:
            for frame in stream:
                count+=len(frame)
                yield frame
        if count!=self.metadata.rows: raise StagingError('read session count mismatch')
        self.completed=True


class CanonicalTrainingDataProvider:
    def __init__(self, store, *, authorize_read, clock=lambda:datetime.now(timezone.utc), elapsed_clock=monotonic):
        self.store=store;self.authorize_read=authorize_read;self.clock=clock;self.elapsed_clock=elapsed_clock

    def _metadata(self, candidate, data, mapping, admission, purpose):
        series={}
        for serialized in candidate.manifest.envelopes:
            dataset=StructuredDataset.model_validate(json.loads(serialized)['canonical_dataset'])
            if (dataset.asset.organization_id!=candidate.reference.organization_id
                    or dataset.asset.source_id!=candidate.manifest.source_id
                    or dataset.canonical_frequency!=mapping.frequency):
                raise StagingError('verified metadata scope mismatch')
            for definition in dataset.series:
                if (set(definition.dimensions)!=set(mapping.identity_dimensions)
                        or definition.attributes.get('organization_id')!=str(candidate.reference.organization_id)
                        or _identity(candidate.reference.organization_id,definition.dimensions)!=definition.series_id):
                    raise StagingError('verified series identity mismatch')
                body=definition.model_dump_json()
                if definition.series_id in series and series[definition.series_id]!=body:
                    raise StagingError('conflicting verified series definitions')
                series[definition.series_id]=body
        if not series: raise StagingError('verified series definitions missing')
        return VerifiedTrainingMetadata(candidate.reference,candidate.manifest.version_id,
            hashlib.sha256(admission.encode()).hexdigest(),purpose,mapping.model_dump_json(),
            candidate.manifest.source_id,data['quality_policy']['policy_id'],data['quality_policy']['version'],
            data['consistency_level'],tuple(series[k] for k in sorted(series,key=str)),candidate.manifest.rows)

    @contextmanager
    def open_read(self, reference, *, purpose, budgets=None):
        """Verify before metadata visibility; require complete consumption on normal exit."""
        budgets=budgets or StagingBudgets()
        try:
            start=self.elapsed_clock()
            def checkpoint():
                if self.elapsed_clock()-start>=budgets.max_elapsed_seconds:raise StagingError('read elapsed budget exceeded')
                trusted_callback(self.authorize_read,'dataset read is not authorized',reference,purpose)
            checkpoint()
            with self.store._publication_session(budgets):
                candidate=self.store._candidate(reference,budgets)
                if candidate.state!='finalized':raise StagingError('dataset is not finalized')
                admission=self.store._publication(reference,budgets)
                if admission is None:raise StagingError('finalized admission evidence missing')
                data,mapping=validate_admission_binding(admission,candidate,purpose,self.clock())
                candidate.mapping_json=mapping.model_dump_json()
                # All payloads and lineage verified before the first visible batch.
                with self.store._read_snapshot(candidate,checkpoint) as batches:
                    with self.store._quality_key_index(budgets) as record_key:
                        verify_dataset_quality_evidence(batches(),mapping,record_key,data)
                    metadata=self._metadata(candidate,data,mapping,admission,purpose)
                    def authorized_batches():
                        for frame in batches():
                            checkpoint()
                            validate_admission_binding(admission,candidate,purpose,self.clock())
                            yield frame
                        checkpoint()
                        validate_admission_binding(admission,candidate,purpose,self.clock())
                    session=_ReadSession(metadata,authorized_batches)
                    yield session
                    if not session.completed:raise StagingError('read session not completely consumed')
        except GeneratorExit:
            raise
        except Exception:
            raise StagingError('finalized dataset access failed') from None

    def read_batches(self, reference, *, purpose, budgets=None):
        """Backward-compatible generator; close() releases snapshot and lock."""
        with self.open_read(reference,purpose=purpose,budgets=budgets) as session:
            with closing(session.batches()) as stream:
                yield from stream

    def require_current(self, metadata, *, budgets=None):
        """Fresh authorization/evidence check; retained detached data is not re-read."""
        try:
            if type(metadata) is not VerifiedTrainingMetadata:raise ValueError()
            trusted_callback(self.authorize_read,'dataset read is not authorized',metadata.reference,metadata.purpose)
            budgets=budgets or StagingBudgets()
            with self.store._publication_session(budgets):
                candidate=self.store._candidate(metadata.reference,budgets)
                if candidate.state!='finalized':raise ValueError()
                admission=self.store._publication(metadata.reference,budgets)
                data,mapping=validate_admission_binding(admission,candidate,metadata.purpose,self.clock())
                if self._metadata(candidate,data,mapping,admission,metadata.purpose)!=metadata:raise ValueError()
        except Exception:
            raise StagingError('dataset authorization or publication binding failed') from None
