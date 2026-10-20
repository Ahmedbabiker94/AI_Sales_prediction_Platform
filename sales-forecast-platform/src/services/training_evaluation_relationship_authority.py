"""Process-local relationship approval, never model-use authorization.

Trusted application bootstrap retains the operator object and pins the verifier
object in its service composition. Neither constructing another authority nor
matching its names/hashes establishes trust. Requests receive only the verifier
and an opaque original handle. Trusted Python is not a security sandbox.
"""
from dataclasses import dataclass, replace
import math
from threading import Lock, RLock
from time import monotonic

from src.data.contracts.dataset import StagingBudgets, _opaque, operational_json
from src.data.contracts.model_evaluation import ModelEvaluationEligibility
from src.data.contracts.backtesting import identity
from src.providers.canonical_training_data_provider import CanonicalTrainingDataProvider, VerifiedTrainingMetadata
from src.services.canonical_training_preparation_service import CanonicalTrainingPreparationService


class RelationshipAuthorityError(ValueError):
    """Fixed diagnostics; no request, publication or callback contents."""


def _guard(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except Exception:
        pass
    error = RelationshipAuthorityError('relationship approval unavailable or invalid')
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


def _not_cancelled():
    return False


@dataclass(frozen=True)
class RelationshipAuthorityBudgets:
    max_records: int = 16
    max_total_record_bytes: int = 4 * 1024 * 1024
    max_declaration_bytes: int = 256 * 1024
    max_verifications: int = 256
    max_elapsed_seconds: int = 10
    max_validity_seconds: int = 3600


class RelationshipApproval:
    """Opaque original object. Public construction/copying grants nothing."""
    __slots__ = ()

    def __reduce__(self):
        raise TypeError('relationship handles are process-local')


@dataclass(frozen=True, slots=True)
class RelationshipApprovalEvidence:
    """Label-free audit receipt, NOT a credential or model-use approval."""
    approval_id: str
    eligibility_id: str
    authority_id: str
    authority_version: str
    issuance_context: str
    approval_reference: str
    issued_at_monotonic: float
    expires_at_monotonic: float
    version: str = '1.0'
    status: str = 'RELATIONSHIP_ONLY'


@dataclass(frozen=True, slots=True)
class _Record:
    declaration_json: str
    training: VerifiedTrainingMetadata
    evaluation: VerifiedTrainingMetadata
    evidence: RelationshipApprovalEvidence
    size: int


class _Registry:
    def __init__(self, training_provider, evaluation_provider, authority_id,
                 authority_version, issuance_context, budgets, elapsed_clock):
        if (type(training_provider) is not CanonicalTrainingDataProvider
                or type(evaluation_provider) is not CanonicalTrainingDataProvider
                or type(budgets) is not RelationshipAuthorityBudgets or not callable(elapsed_clock)):
            raise ValueError()
        for text in (authority_id, authority_version, issuance_context):
            _opaque(text)
        ceilings = RelationshipAuthorityBudgets()
        for name, value in vars(budgets).items():
            if type(value) is not int or not 1 <= value <= getattr(ceilings, name):
                raise ValueError()
        self.training_provider = training_provider
        self.evaluation_provider = evaluation_provider
        self.authority_id = authority_id
        self.authority_version = authority_version
        self.issuance_context = issuance_context
        self.budgets = RelationshipAuthorityBudgets(**vars(budgets))
        self.clock = elapsed_clock
        self.lock = RLock()
        self.busy = Lock()
        self.records = {}
        self.used_declarations = set()
        self.revoked = set()
        self.total_bytes = 0
        self.verifications = 0
        self.previous_time = None

    def time(self):
        now = self.clock()
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError()
        with self.lock:
            if self.previous_time is not None and now < self.previous_time:
                raise ValueError()
            self.previous_time = now
        return float(now)

    def checkpoint(self, start, cancelled):
        stop = cancelled()
        now = self.time()
        if type(stop) is not bool or stop or now - start >= self.budgets.max_elapsed_seconds:
            raise ValueError()
        return now

    def declaration(self, value):
        if type(value) is not ModelEvaluationEligibility:
            raise ValueError()
        # Revalidation precedes serialization: unchecked copies cannot drop extras.
        result = ModelEvaluationEligibility.model_validate(value)
        encoded = result.model_dump_json(warnings='error')
        if len(encoded.encode()) > self.budgets.max_declaration_bytes:
            raise ValueError()
        return result, encoded

    def publications(self, declaration, training, evaluation, start, cancelled):
        relation = declaration.relationship
        if (type(training) is not VerifiedTrainingMetadata or type(evaluation) is not VerifiedTrainingMetadata
                or training.purpose != 'production_training'
                or evaluation.purpose != 'offline_forecast_evaluation'):
            raise ValueError()
        for provider, metadata, binding in (
                (self.training_provider, training, relation.training),
                (self.evaluation_provider, evaluation, relation.evaluation)):
            self.checkpoint(start, cancelled)
            # Existing authoritative FINALIZED/admission/purpose verifier. No row
            # capture, label access, duplicate admission engine or model loading.
            provider.require_current(metadata, budgets=StagingBudgets(
                max_elapsed_seconds=self.budgets.max_elapsed_seconds))
            self.checkpoint(start, cancelled)
            actual = CanonicalTrainingPreparationService._temporal_binding(metadata, binding.series_ids)
            if actual != binding:
                raise ValueError()
            mapping = metadata.mapping
            if ((mapping.target_metric, mapping.measurement.unit, mapping.measurement.currency,
                 mapping.frequency.value, mapping.timezone) !=
                    (relation.metric, relation.unit, relation.currency, relation.frequency, relation.timezone)):
                raise ValueError()
            definitions = {s.series_id: s for s in metadata.series}
            for series in relation.series:
                if series.series_id not in definitions or tuple(sorted(definitions[series.series_id].dimensions.items())) != series.dimensions:
                    raise ValueError()

    def active(self, handle, record, now):
        if (self.records.get(handle) is not record or handle in self.revoked
                or now >= record.evidence.expires_at_monotonic):
            raise ValueError()

    def approve(self, declaration, *, training_metadata, evaluation_metadata,
                approval_reference, valid_for_seconds=300, cancelled=_not_cancelled):
        if not self.busy.acquire(blocking=False):
            raise ValueError()
        try:
            start = self.time()
            if not callable(cancelled) or type(valid_for_seconds) is not int or not 1 <= valid_for_seconds <= self.budgets.max_validity_seconds:
                raise ValueError()
            self.checkpoint(start, cancelled)
            _opaque(approval_reference)
            checked, encoded = self.declaration(declaration)
            relation = checked.relationship
            if ((relation.authority_id, relation.authority_version) != (self.authority_id, self.authority_version)
                    or approval_reference not in relation.evidence_references):
                raise ValueError()
            key = checked.eligibility_id
            if type(training_metadata) is not VerifiedTrainingMetadata or type(evaluation_metadata) is not VerifiedTrainingMetadata:
                raise ValueError()
            size = (len(encoded.encode()) + len(repr((training_metadata, evaluation_metadata)).encode())
                    + len(operational_json([self.authority_id, self.authority_version,
                        self.issuance_context, approval_reference]).encode()) + 512)
            with self.lock:
                if (key in self.used_declarations or len(self.records) >= self.budgets.max_records
                        or self.total_bytes + size > self.budgets.max_total_record_bytes):
                    raise ValueError()
            self.publications(checked, training_metadata, evaluation_metadata, start, cancelled)
            record_id = identity(['relationship_approval_v1', self.authority_id, self.authority_version,
                                  self.issuance_context, approval_reference, encoded, start, start + valid_for_seconds])
            receipt = RelationshipApprovalEvidence(record_id, key, self.authority_id, self.authority_version,
                self.issuance_context, approval_reference, start, start + valid_for_seconds)
            record = _Record(encoded,
                replace(training_metadata, reference=replace(training_metadata.reference)),
                replace(evaluation_metadata, reference=replace(evaluation_metadata.reference)), receipt, size)
            handle = RelationshipApproval()
            now = self.checkpoint(start, cancelled)
            if now >= receipt.expires_at_monotonic:
                raise ValueError()
            with self.lock:
                self.records[handle] = record
                self.used_declarations.add(key)
                self.total_bytes += size
            return handle
        finally:
            self.busy.release()

    def revoke(self, handle):
        if type(handle) is not RelationshipApproval:
            raise ValueError()
        with self.lock:
            if handle not in self.records:
                raise ValueError()
            self.revoked.add(handle)  # Permanent tombstone; no reactivation API.

    def verify(self, declaration, approval, *, cancelled=_not_cancelled):
        if not self.busy.acquire(blocking=False):
            raise ValueError()
        try:
            start = self.time()
            if type(approval) is not RelationshipApproval or not callable(cancelled):
                raise ValueError()
            with self.lock:
                record = self.records.get(approval)
                if record is None or self.verifications >= self.budgets.max_verifications:
                    raise ValueError()
                self.active(approval, record, start)
                self.verifications += 1  # Failed admitted attempts also consume budget.
            self.checkpoint(start, cancelled)
            checked, encoded = self.declaration(declaration)
            if encoded != record.declaration_json:
                raise ValueError()
            self.publications(checked, record.training, record.evaluation, start, cancelled)
            result = replace(record.evidence)  # No public alias into private expiry/provenance.
            # Last effectful collaborators are the clock and cancellation checks.
            # A callback can revoke the record: inspect ledger AFTER those calls.
            now = self.checkpoint(start, cancelled)
            with self.lock:
                self.active(approval, record, now)
                return result
        finally:
            self.busy.release()


def _require_registry(registry):
    if type(registry) is not _Registry:
        raise ValueError()
    return registry


class RelationshipApprovalOperator:
    """Keep private to trusted bootstrap/operator; never expose to requests."""
    __slots__ = ('__registry',)

    def __init__(self, registry):
        self.__registry = _guard(_require_registry, registry)

    def approve(self, *args, **kwargs):
        return _guard(self.__registry.approve, *args, **kwargs)

    def revoke(self, *args, **kwargs):
        return _guard(self.__registry.revoke, *args, **kwargs)


class RelationshipApprovalVerifier:
    """Pin THIS bootstrap-owned instance. Cannot issue, replace or revive records."""
    __slots__ = ('__registry',)

    def __init__(self, registry):
        self.__registry = _guard(_require_registry, registry)

    def verify(self, *args, **kwargs):
        return _guard(self.__registry.verify, *args, **kwargs)


def _bootstrap(training_provider, evaluation_provider, *, authority_id, authority_version,
               issuance_context, budgets=RelationshipAuthorityBudgets(), elapsed_clock=monotonic):
    registry = _Registry(training_provider, evaluation_provider, authority_id, authority_version,
                         issuance_context, budgets, elapsed_clock)
    return RelationshipApprovalOperator(registry), RelationshipApprovalVerifier(registry)


def bootstrap_relationship_authority(*args, **kwargs):
    """Trusted bootstrap only; creation does not register global trust anywhere.

    The operator's explicit approve() invocation is the approval decision and
    issuance provenance. This process-local registry is the independent ledger;
    request declarations and read permissions never add or approve entries.
    """
    return _guard(_bootstrap, *args, **kwargs)
