"""R7-04A non-executable H1 prediction contracts; no declaration grants use."""
from typing import Literal
from pydantic import Field, StrictInt, model_validator
from src.data.contracts.backtesting import identity
from src.data.contracts.candidate_package import Digest
from src.data.contracts.model_representation import RepresentationContract, RepresentationTimestamp
from src.data.contracts.runtime_model_use import RuntimeModelUseRequest, RuntimeUseLimits
from src.data.contracts.runtime_snapshot import _schema_from_json
from src.data.contracts.training_adapter import Family
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline


class CanonicalH1PredictionRequest(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    # Reuse exact candidate/package/owner, representation/state, tenant/series,
    # publication/origin/cutoff and snapshot bindings rather than duplicate them.
    inspection: RuntimeModelUseRequest
    execution_purpose: Literal['controlled_canonical_h1_prediction_v1'] = 'controlled_canonical_h1_prediction_v1'
    execution_authority_requirement: Literal['independent_runtime_execution_authority_required_v1'] = 'independent_runtime_execution_authority_required_v1'
    executable_input_requirement: Literal['original_owner_bound_m2_m3_m4_m5_outputs_required_v1'] = 'original_owner_bound_m2_m3_m4_m5_outputs_required_v1'
    horizon_step: StrictInt = Field(default=1, ge=1, le=1)
    target_period: RepresentationTimestamp
    target_period_end: RepresentationTimestamp
    limits: RuntimeUseLimits = Field(default_factory=RuntimeUseLimits)

    @model_validator(mode='after')
    def target(self):
        r=self.inspection.snapshot_request
        engine=CanonicalFeaturePipeline(_schema_from_json(r.feature_schema_json).configuration)
        origin=engine._time(r.forecast_origin)
        if self.target_period!=origin or self.target_period_end!=engine._advance(origin,1):
            raise ValueError('exact canonical H1 target required')
        return self

    @property
    def request_id(self):
        return identity(['canonical_h1_prediction_request_v1',self.model_dump(mode='json',warnings='error')])

    @property
    def scope_id(self):
        return identity(['canonical_h1_prediction_scope_v1',self.inspection.snapshot_id,
            self.inspection.snapshot_request.model_dump(mode='json',warnings='error'),
            self.target_period.isoformat(),self.target_period_end.isoformat(),self.horizon_step])

    @property
    def purpose_id(self):
        return identity([self.version,self.execution_purpose,self.execution_authority_requirement,self.executable_input_requirement])


class CanonicalH1PredictionOutcome(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    classification: Literal['NON_EXECUTABLE_PREDICTOR_OUTCOME_ONLY'] = 'NON_EXECUTABLE_PREDICTOR_OUTCOME_ONLY'
    status: Literal['DENIED','UNAVAILABLE']
    reason: Literal['INVALID_REQUEST_OR_DEPENDENCY','INSPECTION_DENIED','EXECUTION_AUTHORITY_UNSUPPORTED',
                    'CANCELLED_OR_EXPIRED','BUSY','EXECUTION_DEPENDENCIES_UNAVAILABLE']
    request_id: Digest | None = None
    scope_id: Digest | None = None
    purpose_id: Digest | None = None
    inspection_decision_id: Digest | None = None
    candidate_id: Digest | None = None
    package_id: Digest | None = None
    snapshot_id: Digest | None = None
    family: Family | None = None
    execution_performed: Literal[False] = False
    executable_permission: Literal[False] = False
    production_permission: Literal[False] = False
    execution_authority: Literal['UNAVAILABLE'] = 'UNAVAILABLE'
    native_payload: Literal['UNAVAILABLE'] = 'UNAVAILABLE'
    native_loader: Literal['UNAVAILABLE'] = 'UNAVAILABLE'
    executable_inputs: Literal['UNAVAILABLE'] = 'UNAVAILABLE'
    source_compatibility: Literal['SOURCE_CONTEXT_ONLY'] = 'SOURCE_CONTEXT_ONLY'
    evidence_classification: Literal['TEST_ONLY_NON_DEPLOYABLE'] = 'TEST_ONLY_NON_DEPLOYABLE'

    @model_validator(mode='after')
    def complete(self):
        refs=(self.request_id,self.scope_id,self.purpose_id,self.inspection_decision_id,
              self.candidate_id,self.package_id,self.snapshot_id,self.family)
        if self.status=='UNAVAILABLE':
            if any(v is None for v in refs) or self.reason!='EXECUTION_DEPENDENCIES_UNAVAILABLE':
                raise ValueError('complete unavailable context required')
        elif any(v is not None for v in refs) or self.reason=='EXECUTION_DEPENDENCIES_UNAVAILABLE':
            raise ValueError('denied outcome has no admitted references')
        return self

    @property
    def outcome_id(self):
        return identity(['canonical_h1_prediction_outcome_v1',self.model_dump(mode='json',warnings='error')])
