"""M7 immutable candidate evidence. Integrity identities never confer authority."""
from typing import Annotated, Literal
from pydantic import Field, StrictInt, model_validator
from src.data.contracts.backtesting import identity
from src.data.contracts.model_representation import RepresentationContract, OrderedFeatures, RepresentationTimestamp
from src.data.contracts.temporal_preparation import TemporalPreparationPlan
from src.data.contracts.training_adapter import TrainingCandidateMetadata, Family, capability

Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


class PayloadCapability(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    family: Family
    # Prospective formats only: none has an M7 verified native publisher/loader.
    native_format: Literal['xgboost_ubjson', 'lightgbm_text', 'torch_state_dict']
    native_publication: Literal['UNAVAILABLE'] = 'UNAVAILABLE'
    native_loading: Literal['UNAVAILABLE'] = 'UNAVAILABLE'
    test_format: Literal['test_evidence_json_v1'] = 'test_evidence_json_v1'

    @model_validator(mode='after')
    def exact(self):
        expected = ('xgboost_ubjson' if self.family == 'xgboost' else
                    'lightgbm_text' if self.family == 'lightgbm' else 'torch_state_dict')
        if self.native_format != expected:
            raise ValueError('unsupported family format')
        return self


def payload_capability(family):
    capability(family)  # accepted strict allowlist, before any framework or I/O
    return PayloadCapability(family=family, native_format=(
        'xgboost_ubjson' if family == 'xgboost' else 'lightgbm_text' if family == 'lightgbm' else 'torch_state_dict'))


class PreprocessingReference(RepresentationContract):
    """Label-free references, not a portable executable frozen-state payload."""
    state_id: Digest
    schema_version: Literal['1.0'] = '1.0'
    policy: Literal['canonical_tabular_frozen_v1', 'canonical_recurrent_frozen_v1', 'canonical_transformer_frozen_v1']
    canonical_state_id: Digest
    canonical_schema_id: Digest
    ordered_groups: tuple[OrderedFeatures, ...] = Field(min_length=1, max_length=3)

    @property
    def schema_id(self):
        return identity([self.schema_version, self.policy,
                         [s.model_dump(mode='json', warnings='error') for s in self.ordered_groups]])


class CandidatePackageManifest(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    classification: Literal['CANDIDATE_ONLY'] = 'CANDIDATE_ONLY'
    deployability: Literal['TEST_ONLY_NON_DEPLOYABLE'] = 'TEST_ONLY_NON_DEPLOYABLE'
    creation_context: Literal['controlled_offline_m7_stub_evidence_v1'] = 'controlled_offline_m7_stub_evidence_v1'
    candidate_id: Digest
    training: TrainingCandidateMetadata
    plan: TemporalPreparationPlan
    fold_id: str = Field(min_length=1, max_length=128)
    training_origins: tuple[RepresentationTimestamp, ...] = Field(min_length=1, max_length=1024)
    preprocessing: PreprocessingReference
    payload_capability: PayloadCapability
    payload_name: Literal['test-evidence.json'] = 'test-evidence.json'
    payload_format: Literal['test_evidence_json_v1'] = 'test_evidence_json_v1'
    payload_size: StrictInt = Field(ge=1, le=65536)
    payload_sha256: Digest

    @model_validator(mode='after')
    def bound(self):
        t, p, s = self.training, self.plan, self.preprocessing
        cap = capability(t.specification.family)
        if (self.candidate_id != t.candidate_id or t.backend != 'test_stub' or t.plan_digest != p.digest or len(p.datasets) != 1
                or len(p.folds) != 1 or p.forecast_horizon != 1 or p.capability != 'supervised_h1'
                or t.fitted_state_id != s.state_id or self.fold_id != p.folds[0].fold_id
                or self.payload_capability != payload_capability(t.specification.family)):
            raise ValueError('exact candidate bindings required')
        fold = p.folds[0]
        selected = tuple(o for o in fold.training_origins if o in self.training_origins)
        if (selected != self.training_origins or t.training_rows != len(selected)*len(p.datasets[0].series_ids)):
            raise ValueError('exact fitted origin scope required')
        kinds = {'tabular': 'canonical_tabular_frozen_v1', 'recurrent': 'canonical_recurrent_frozen_v1',
                 'transformer': 'canonical_transformer_frozen_v1'}
        if s.policy != kinds[cap.representation] or len(s.ordered_groups) != len(t.input_geometry):
            raise ValueError('exact preprocessing schema required')
        if any(shape[-1] != len(group.features) for shape, group in zip(t.input_geometry, s.ordered_groups)):
            raise ValueError('exact ordered input widths required')
        return self

    @property
    def representation_binding_id(self):
        # M6 retains compatibility + exact input digest, not per-output M2 IDs.
        # This is explicitly an execution binding, not a reconstructed M2 ID.
        return identity(['m6_retained_input_binding_v1', self.training.representation_compatibility_id,
                         self.training.input_digest, self.training.input_geometry, self.preprocessing.schema_id])

    @property
    def package_id(self):
        return identity(self.model_dump(mode='json', warnings='error'))


class PackageTrackingRecord(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    candidate_id: Digest
    package_id: Digest | None = None
    status: Literal['STAGED', 'FINALIZED', 'INVALID', 'UNAVAILABLE']
    loader: Literal['UNAVAILABLE'] = 'UNAVAILABLE'

    @model_validator(mode='after')
    def bound(self):
        if self.status != 'UNAVAILABLE' and self.package_id is None:
            raise ValueError('package identity required')
        return self
