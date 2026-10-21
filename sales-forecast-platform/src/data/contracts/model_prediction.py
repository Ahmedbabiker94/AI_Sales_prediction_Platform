"""D3-B label-free prediction declarations, never model-use credentials."""
import math
import struct
from typing import Literal

from pydantic import Field, model_validator

from .backtesting import Digest, PredictionPoint
from .model_evaluation import _Declaration, _declaration_identity
from .temporal_preparation import TemporalDatasetBinding


class FittedH1Prediction(_Declaration):
    point: PredictionPoint
    request_id: Digest
    history_identity: Digest
    representation_schema_id: Digest
    native_float32_hex: str = Field(pattern=r'^[0-9a-f]{8}$')

    @model_validator(mode='after')
    def exact_h1(self):
        value = struct.unpack('<f', bytes.fromhex(self.native_float32_hex))[0]
        if (self.point.scope.horizon_step != 1
                or self.point.scope.target_period != self.point.scope.forecast_origin
                or not math.isfinite(value) or abs(value) > 100_000_000
                or struct.pack('>d', value) != struct.pack('>d', self.point.value)):
            raise ValueError('exact widened H1 native prediction required')
        return self


class FittedH1PredictionResult(_Declaration):
    version: Literal['1.0'] = '1.0'
    implementation_kind: Literal['fitted_candidate'] = 'fitted_candidate'
    status: Literal['PREDICTED_NOT_SCORED'] = 'PREDICTED_NOT_SCORED'
    eligibility_id: Digest
    evaluation_use_id: Digest
    relationship_approval_id: Digest
    candidate_bundle_id: Digest
    artifact_digest: Digest
    training_invocation_digest: Digest
    training_plan_digest: Digest
    fitted_state_id: Digest
    fitted_state_digest: Digest
    representation_schema_id: Digest
    configuration_digest: Digest
    runtime_digest: Digest
    evaluation_publication: TemporalDatasetBinding
    numeric_policy_id: Digest
    events: tuple[FittedH1Prediction, ...] = Field(min_length=1, max_length=512)

    @model_validator(mode='after')
    def ordered_complete_scope(self):
        keys = []
        for event in self.events:
            scope = event.point.scope
            if (scope.dataset_version != self.evaluation_publication.reference.version_id
                    or scope.organization_id != self.evaluation_publication.reference.organization_id
                    or scope.series_id not in self.evaluation_publication.series_ids
                    or scope.plan_digest != self.training_plan_digest
                    or event.representation_schema_id != self.representation_schema_id):
                raise ValueError('prediction provenance mismatch')
            keys.append((scope.forecast_origin, str(scope.organization_id), str(scope.series_id)))
        if keys != sorted(set(keys)):
            raise ValueError('ordered unique prediction events required')
        return self

    @property
    def prediction_id(self):
        return _declaration_identity(self)
