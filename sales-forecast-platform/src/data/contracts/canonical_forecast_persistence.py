"""R7-05A structural forecast declarations, not genuine forecasts or authority."""
from typing import Literal
from uuid import UUID
from pydantic import Field, model_validator
from src.data.contracts.backtesting import identity
from src.data.contracts.candidate_package import Digest
from src.data.contracts.canonical_h1_predictor import CanonicalH1PredictionRequest
from src.data.contracts.model_representation import RepresentationContract
from src.data.contracts.runtime_snapshot import _schema_from_json


class ForecastProvenance(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    prediction: CanonicalH1PredictionRequest
    transformation: Literal['identity_target_v1'] = 'identity_target_v1'
    native_artifact_requirement: Literal['original_owner_verified_native_artifact_required_v1'] = 'original_owner_verified_native_artifact_required_v1'
    loader_requirement: Literal['verified_native_loader_attestation_required_v1'] = 'verified_native_loader_attestation_required_v1'
    input_requirement: Literal['original_owner_verified_executable_inputs_required_v1'] = 'original_owner_verified_executable_inputs_required_v1'
    execution_requirement: Literal['independent_runtime_execution_authority_required_v1'] = 'independent_runtime_execution_authority_required_v1'
    producer_requirement: Literal['genuine_original_producer_execution_evidence_required_v1'] = 'genuine_original_producer_execution_evidence_required_v1'

    @property
    def lineage_id(self):
        return identity(['canonical_forecast_lineage_v1',self.model_dump(mode='json',warnings='error')])


class CanonicalH1ForecastDeclaration(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    state: Literal['DECLARED'] = 'DECLARED'
    provenance: ForecastProvenance
    series_id: UUID
    # There is no accepted genuine producer/attestation verifier. Even apparently
    # valid finite numbers or digest-shaped claims must not enter a record today.
    prediction_value: None = None
    physical_dtype: None = None
    native_artifact_id: None = None
    loader_attestation_id: None = None
    executable_input_id: None = None
    execution_authority_id: None = None
    execution_evidence_id: None = None
    supersedes: None = None

    @model_validator(mode='after')
    def scope(self):
        if self.series_id not in self.provenance.prediction.inspection.snapshot_request.dataset.series_ids:
            raise ValueError('declared series outside prediction scope')
        return self

    @property
    def logical_key_id(self):
        p=self.provenance.prediction;inner=p.inspection;r=inner.snapshot_request
        cfg=_schema_from_json(r.feature_schema_json).configuration
        # Candidate-specific streams coexist. Cutoff, publication and execution
        # lineage deliberately do NOT partition a key: changes there conflict.
        return identity(['canonical_candidate_h1_forecast_key_v1',str(r.dataset.reference.organization_id),
            str(r.dataset.reference.dataset_id),str(self.series_id),inner.candidate_id,inner.family,
            r.forecast_origin.isoformat(),p.target_period.isoformat(),p.target_period_end.isoformat(),p.horizon_step,
            cfg.target_metric,cfg.execution.measurement.model_dump(mode='json',warnings='error'),
            cfg.execution.temporal.model_dump(mode='json',warnings='error'),self.provenance.transformation])

    @property
    def declaration_id(self):
        return identity(['canonical_h1_forecast_declaration_v1',self.model_dump(mode='json',warnings='error')])


class ForecastDeclarationBatch(RepresentationContract):
    """Bounded structural collection only. Not a staging area or write request."""
    version: Literal['1.0'] = '1.0'
    declarations: tuple[CanonicalH1ForecastDeclaration,...] = Field(min_length=1,max_length=16)

    @model_validator(mode='after')
    def unique_keys(self):
        keys=tuple(d.logical_key_id for d in self.declarations)
        if len(set(keys))!=len(keys): raise ValueError('duplicate logical forecast key')
        if keys!=tuple(sorted(keys)): raise ValueError('canonical logical key order required')
        return self


def compare_forecast_declarations(left,right):
    """Pure proposed-metadata comparison; no stored replay/write authorization."""
    if type(left) is not CanonicalH1ForecastDeclaration or type(right) is not CanonicalH1ForecastDeclaration:
        raise ValueError('forecast declarations required')
    left=CanonicalH1ForecastDeclaration.model_validate_json(left.canonical_json)
    right=CanonicalH1ForecastDeclaration.model_validate_json(right.canonical_json)
    if left.logical_key_id!=right.logical_key_id: return 'DIFFERENT_LOGICAL_KEY'
    return 'IDENTICAL_DECLARATION' if left.declaration_id==right.declaration_id else 'CONFLICTING_DECLARATION'


class ForecastPersistenceInspectionOutcome(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    status: Literal['DENIED','UNAVAILABLE']
    reason: Literal['INVALID_DECLARATION_OR_DEPENDENCY','UNSUPPORTED_EXECUTION_OR_WRITE_AUTHORITY',
                    'CANCELLED_OR_EXPIRED','BUSY','EXECUTION_EVIDENCE_AND_WRITE_AUTHORITY_UNAVAILABLE']
    state: Literal['DECLARED'] = 'DECLARED'
    writes_performed: Literal[False] = False
    verified: Literal[False] = False
    persistable: Literal[False] = False
    executable_permission: Literal[False] = False
    classification: Literal['NON_WRITING_DECLARATION_INSPECTION_ONLY'] = 'NON_WRITING_DECLARATION_INSPECTION_ONLY'
    declaration_id: Digest | None = None
    logical_key_id: Digest | None = None
    lineage_id: Digest | None = None
    prediction_request_id: Digest | None = None

    @model_validator(mode='after')
    def complete(self):
        refs=(self.declaration_id,self.logical_key_id,self.lineage_id,self.prediction_request_id)
        if self.status=='UNAVAILABLE':
            if any(r is None for r in refs) or self.reason!='EXECUTION_EVIDENCE_AND_WRITE_AUTHORITY_UNAVAILABLE':
                raise ValueError('complete unavailable declaration context required')
        elif any(r is not None for r in refs) or self.reason=='EXECUTION_EVIDENCE_AND_WRITE_AUTHORITY_UNAVAILABLE':
            raise ValueError('denied inspection has no admitted references')
        return self

    @property
    def outcome_id(self):
        return identity(['forecast_persistence_inspection_outcome_v1',self.model_dump(mode='json',warnings='error')])
