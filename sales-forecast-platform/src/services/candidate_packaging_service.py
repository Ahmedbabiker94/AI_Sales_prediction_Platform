"""Trusted operator composition of original M6 ownership and candidate storage.

No native serializer/loader, model prediction or production registry is called.
The host pins the original trainer, private store and approval callback; these
are not request parameters. Python encapsulation is not host authentication.
"""
import hashlib
from threading import Lock
from src.data.contracts.candidate_package import CandidatePackageManifest, PreprocessingReference, payload_capability
from src.data.contracts.dataset import operational_json
from src.data.contracts.training_adapter import TrainingCandidateMetadata
from src.preprocessing.canonical_tabular import TabularPreprocessingState
from src.preprocessing.canonical_recurrent import RecurrentPreprocessingState
from src.preprocessing.canonical_transformer import TransformerPreprocessingState
from src.services.canonical_adapter_training_service import CanonicalAdapterTrainingService, CandidateReference
from src.providers.candidate_package_provider import CandidatePackageProvider
from src.repositories.candidate_package_repository import reject_store


def _summary(metadata, state):
    family = metadata.specification.family
    cls = (TabularPreprocessingState if family in ('xgboost', 'lightgbm') else
           TransformerPreprocessingState if family == 'transformer' else RecurrentPreprocessingState)
    if type(state) is not cls:
        raise ValueError()
    state = cls.model_validate_json(state.canonical_json)
    base = state if cls is TabularPreprocessingState else state.encoder.base if cls is TransformerPreprocessingState else state.base
    if base.plan.digest != metadata.plan_digest or state.state_id != metadata.fitted_state_id:
        raise ValueError()
    fold = base.plan.folds[0]
    origins = fold.training_origins if cls is TabularPreprocessingState else tuple(o for o in fold.training_origins if o in state.request.origins)
    groups = ((state.feature_schema,) if cls is not TransformerPreprocessingState else
              (state.encoder.feature_schema, state.static_schema, state.future_schema))
    summary = PreprocessingReference(state_id=state.state_id, policy=state.policy,
        canonical_state_id=base.canonical_state.state_id, canonical_schema_id=base.canonical_schema.schema_id,
        ordered_groups=groups)
    return base.plan, base.fold_id, origins, summary


class CandidatePackagingService:
    def __init__(self, trainer, provider, *, authorize_packaging):
        if (type(trainer) is not CanonicalAdapterTrainingService or type(provider) is not CandidatePackageProvider
                or not callable(authorize_packaging)):
            reject_store()
        self._trainer = trainer
        self._provider = provider
        self._authorize = authorize_packaging
        self._self = self
        self._lock = Lock()

    def _owned(self, reference):
        if self._self is not self or type(reference) is not CandidateReference:
            raise ValueError()
        record = self._trainer._candidates.get(id(reference))
        if record is None or record[0] is not self._trainer or record[1] is not reference:
            raise ValueError()
        metadata = record[2]
        if type(metadata) is not TrainingCandidateMetadata:
            raise ValueError()
        metadata = TrainingCandidateMetadata.model_validate_json(metadata.canonical_json)
        return record, metadata

    def package(self, reference, *, test_only=False):
        # Explicit test opt-in is NOT authority. Original ownership + trusted
        # callback are independently required, for test and unavailable paths.
        if not self._lock.acquire(blocking=False):
            reject_store()
        try:
            record, metadata = self._owned(reference)
            if type(test_only) is not bool:
                raise ValueError()
            if self._authorize(metadata) is not True:
                raise ValueError()
            if self._owned(reference)[0] is not record:
                raise ValueError()
            if metadata.backend != 'test_stub' or not test_only:
                return self._provider.unavailable(metadata.candidate_id)
            plan, fold, origins, summary = _summary(metadata, record[4])
            payload = operational_json({'version': '1.0', 'classification': 'TEST_ONLY_NON_DEPLOYABLE',
                'candidate_id': metadata.candidate_id, 'family': metadata.specification.family,
                'content': 'NO_MODEL_PAYLOAD'}).encode('utf-8')
            manifest = CandidatePackageManifest(candidate_id=metadata.candidate_id, training=metadata, plan=plan, fold_id=fold,
                training_origins=origins, preprocessing=summary, payload_capability=payload_capability(metadata.specification.family),
                payload_size=len(payload), payload_sha256=hashlib.sha256(payload).hexdigest())
            # No callbacks after this final ownership/admission boundary.
            if self._owned(reference)[0] is not record:
                raise ValueError()
            return self._provider.publish(manifest, payload)
        except Exception:
            pass
        finally:
            self._lock.release()
        reject_store()

    def verify(self, package_id, *, candidate):
        """Read-only bytes verification plus original live M6 reference binding."""
        try:
            record, metadata = self._owned(candidate)
            if self._provider.lookup(package_id).status != 'FINALIZED':
                raise ValueError()
            manifest = self._provider._repository.verify(package_id)
            plan, fold, origins, summary = _summary(metadata, record[4])
            if (manifest.training != metadata or manifest.preprocessing != summary or manifest.plan != plan
                    or manifest.fold_id != fold or manifest.training_origins != origins):
                raise ValueError()
            if self._authorize(metadata) is not True or self._owned(candidate)[0] is not record:
                raise ValueError()
            return manifest
        except Exception:
            pass
        reject_store()
