"""R7-03 readiness inspection declarations. No declaration is a capability."""
from typing import Literal
from pydantic import Field, StrictInt, StrictStr, model_validator
from src.data.contracts.backtesting import identity
from src.data.contracts.candidate_package import Digest
from src.data.contracts.model_representation import RepresentationContract, RepresentationTimestamp
from src.data.contracts.runtime_snapshot import RuntimeSnapshotRequest
from src.data.contracts.training_adapter import Family


class RuntimeUseLimits(RepresentationContract):
    max_elapsed_seconds: StrictInt = Field(default=30, ge=1, le=60)
    max_request_bytes: StrictInt = Field(default=256*1024, ge=1, le=256*1024)
    max_output_bytes: StrictInt = Field(default=16384, ge=1, le=16384)


class RuntimeModelUseRequest(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    purpose: Literal['controlled_h1_runtime_readiness_inspection_v1'] = 'controlled_h1_runtime_readiness_inspection_v1'
    ownership: Literal['original_retained_m6_reference_required'] = 'original_retained_m6_reference_required'
    candidate_id: Digest
    package_id: Digest
    family: Family
    representation_binding_id: Digest
    representation_compatibility_id: Digest
    frozen_state_id: Digest
    canonical_state_id: Digest
    canonical_schema_id: Digest
    lifecycle_id: Digest
    lifecycle_transition_id: Digest
    evaluation_report_id: Digest
    ensemble_evaluation_id: Digest
    snapshot_id: Digest
    snapshot_request: RuntimeSnapshotRequest
    host_context_id: Digest
    actor_id: StrictStr = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.:-]+$')
    limits: RuntimeUseLimits = Field(default_factory=RuntimeUseLimits)

    @property
    def request_id(self):
        return identity(['r7_03_runtime_inspection_request_v1', self.model_dump(mode='json', warnings='error')])


class RuntimeInspectionGrant(RepresentationContract):
    """Public receipt only; the original private handle is separately required."""
    version: Literal['1.0'] = '1.0'
    action: Literal['INSPECT_READINESS_ONLY'] = 'INSPECT_READINESS_ONLY'
    request_id: Digest
    host_context_id: Digest
    actor_id: StrictStr = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.:-]+$')
    valid_from: RepresentationTimestamp
    expires_at: RepresentationTimestamp

    @model_validator(mode='after')
    def interval(self):
        if not self.valid_from < self.expires_at: raise ValueError('invalid interval')
        if (self.expires_at-self.valid_from).total_seconds() > 3600: raise ValueError('bounded interval required')
        return self

    @property
    def grant_id(self):
        return identity(['r7_03_inspection_grant_v1', self.model_dump(mode='json', warnings='error')])


class RuntimeModelUseDecision(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    classification: Literal['NON_EXECUTABLE_READINESS_DECISION_ONLY'] = 'NON_EXECUTABLE_READINESS_DECISION_ONLY'
    status: Literal['DENIED', 'UNAVAILABLE']
    reason: Literal['INVALID_AUTHORITY_OR_EVIDENCE', 'CANCELLED_OR_EXPIRED', 'BUSY',
                    'NATIVE_PAYLOAD_INPUT_AND_MODEL_USE_AUTHORITY_UNAVAILABLE']
    request_id: Digest | None = None
    candidate_id: Digest | None = None
    package_id: Digest | None = None
    snapshot_id: Digest | None = None
    family: Family | None = None
    inspection_grant_id: Digest | None = None
    verified_evidence_id: Digest | None = None
    executable_permission: Literal[False] = False
    production_permission: Literal[False] = False
    native_payload: Literal['UNAVAILABLE'] = 'UNAVAILABLE'
    model_input_compatibility: Literal['SOURCE_CONTEXT_ONLY'] = 'SOURCE_CONTEXT_ONLY'
    evidence_classification: Literal['TEST_ONLY_NON_DEPLOYABLE'] = 'TEST_ONLY_NON_DEPLOYABLE'

    @model_validator(mode='after')
    def complete(self):
        refs=(self.request_id,self.candidate_id,self.package_id,self.snapshot_id,self.family,
              self.inspection_grant_id,self.verified_evidence_id)
        if self.status == 'UNAVAILABLE':
            if any(x is None for x in refs) or self.reason != 'NATIVE_PAYLOAD_INPUT_AND_MODEL_USE_AUTHORITY_UNAVAILABLE':
                raise ValueError('complete unavailable evidence required')
        elif self.reason == 'NATIVE_PAYLOAD_INPUT_AND_MODEL_USE_AUTHORITY_UNAVAILABLE' or any(x is not None for x in refs):
            raise ValueError('denial exposes no admitted evidence')
        return self

    @property
    def decision_id(self):
        return identity(['r7_03_non_executable_decision_v1', self.model_dump(mode='json', warnings='error')])
