"""B1 synthetic native verification evidence; never runtime or production authority."""
from dataclasses import dataclass
from typing import Literal
from pydantic import Field, StrictInt, model_validator
from src.data.contracts.backtesting import identity
from src.data.contracts.candidate_package import Digest
from src.data.contracts.model_representation import RepresentationContract, OrderedFeatures
from src.data.contracts.training_adapter import TrainingCandidateMetadata


class NativeRoundTripLimits(RepresentationContract):
    max_rows: StrictInt = Field(default=32, ge=1, le=32)
    max_cells: StrictInt = Field(default=1024, ge=1, le=1024)
    max_artifact_bytes: StrictInt = Field(default=1048576, ge=1, le=1048576)
    max_elapsed_seconds: StrictInt = Field(default=30, ge=1, le=30)


class NativeXGBoostManifest(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    classification: Literal['SYNTHETIC_NATIVE_ROUNDTRIP_ONLY'] = 'SYNTHETIC_NATIVE_ROUNDTRIP_ONLY'
    format: Literal['xgboost_ubjson_v1'] = 'xgboost_ubjson_v1'
    framework_version: Literal['3.2.0'] = '3.2.0'
    loader_policy: Literal['owned_xgboost_3_2_0_cpu_v1'] = 'owned_xgboost_3_2_0_cpu_v1'
    training: TrainingCandidateMetadata
    frozen_state_id: Digest
    feature_schema: OrderedFeatures
    native_input_dtype: Literal['float32'] = 'float32'
    native_attributes: tuple[tuple[str, str], ...]
    payload_size: StrictInt = Field(ge=1, le=1048576)
    payload_sha256: Digest
    runtime_permission: Literal[False] = False
    production_permission: Literal[False] = False

    @model_validator(mode="after")
    def bound(self):
        t = self.training
        attrs = dict(self.native_attributes)
        if (t.backend != "native_framework" or t.backend_version != self.framework_version
                or t.specification.family != "xgboost" or t.specification.input_dtype != "float32"
                or t.fitted_state_id != self.frozen_state_id
                or t.input_geometry[0][1] != len(self.feature_schema.features)
                or self.native_attributes != tuple(sorted(attrs.items()))
                or attrs.get("b1_candidate") != t.candidate_id
                or attrs.get("b1_state") != self.frozen_state_id
                or attrs.get("b1_training_input") != t.input_digest):
            raise ValueError("original native bindings required")
        return self

    @property
    def artifact_id(self):
        return identity(['b1_owned_native_xgboost_v1', self.model_dump(mode='json', warnings='error')])


@dataclass(frozen=True, slots=True, eq=False)
class NativeArtifactEnvelope:
    """Only the original owner-issued object is admitted; bytes/hashes are not handles."""
    manifest: NativeXGBoostManifest
    payload: bytes

    def __reduce__(self):
        raise TypeError('process-local native envelope cannot be serialized')


class NativeRoundTripReport(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    classification: Literal['SYNTHETIC_NATIVE_ROUNDTRIP_ONLY'] = 'SYNTHETIC_NATIVE_ROUNDTRIP_ONLY'
    artifact_id: Digest
    candidate_id: Digest
    validation_output_id: Digest
    validation_rows: StrictInt = Field(ge=1, le=32)
    prediction_dtype: Literal['float32'] = 'float32'
    original_prediction_sha256: Digest
    reloaded_prediction_sha256: Digest
    comparison: Literal['bitwise_float32_v1'] = 'bitwise_float32_v1'
    runtime_permission: Literal[False] = False
    production_permission: Literal[False] = False

    @model_validator(mode="after")
    def exact_parity(self):
        if self.original_prediction_sha256 != self.reloaded_prediction_sha256:
            raise ValueError("exact native parity required")
        return self
