"""Read-only original B1/B2 seams. No catalog, model parser, registry or writer.

These retained Python identities attest original issuance within a trusted host,
not cryptographic ownership. No accepted B1/B2 implementation is modified.
"""
from dataclasses import dataclass
import hashlib
from src.data.contracts.native_xgboost_roundtrip import NativeArtifactEnvelope, NativeXGBoostManifest
from src.data.contracts.executable_inputs import OwnedInputEnvelope
from src.data.contracts.controlled_h1_execution import ControlledH1ExecutionRequest
from src.services.canonical_adapter_training_service import CanonicalAdapterTrainingService
from src.services.owned_xgboost_roundtrip_service import OwnedXGBoostRoundTripService
from src.services.owned_input_preparation_service import OwnedInputPreparationService


@dataclass(frozen=True)
class ExecutionEvidence:
    record: object
    metadata: object
    state: object
    native: object
    inputs: object
    output: object


class OwnedExecutionProvider:
    def __init__(self, trainer, native_owner, input_owner):
        if (type(trainer) is not CanonicalAdapterTrainingService
                or type(native_owner) is not OwnedXGBoostRoundTripService
                or type(input_owner) is not OwnedInputPreparationService
                or native_owner._trainer is not trainer or input_owner._trainer is not trainer):
            raise ValueError('original execution composition required')
        self._trainer=trainer; self._native=native_owner; self._inputs=input_owner; self._self=self

    def metadata(self, reference):
        if self._self is not self: raise ValueError()
        return self._inputs._owned(reference)[1]

    def require(self, reference, native, inputs, request):
        if self._self is not self or type(request) is not ControlledH1ExecutionRequest: raise ValueError()
        req=ControlledH1ExecutionRequest.model_validate_json(request.canonical_json)
        record, metadata, selection, state=self._inputs._owned(reference)
        if (type(native) is not NativeArtifactEnvelope or type(inputs) is not OwnedInputEnvelope
                or self._native._self is not self._native or self._inputs._self is not self._inputs): raise ValueError()
        issued=self._native._issued.get(id(native)); data=self._inputs._issued.get(id(inputs))
        if (issued is None or issued[0] is not native or issued[1] is not record
                or data is None or data[0] is not inputs or data[1] is not reference or data[2] is not record
                or self._native._owned(reference)[0] is not record
                or native.manifest.canonical_json!=issued[2] or native.payload!=issued[3]): raise ValueError()
        manifest=NativeXGBoostManifest.model_validate_json(native.manifest.canonical_json)
        output=self._inputs.require(inputs)
        if (type(native.payload) is not bytes or not 1<=len(native.payload)<=req.limits.max_artifact_bytes
                or len(native.payload)!=manifest.payload_size or hashlib.sha256(native.payload).hexdigest()!=manifest.payload_sha256
                or manifest.artifact_id!=req.artifact_id or manifest.payload_sha256!=req.payload_sha256
                or req.inputs!=inputs.binding or inputs.binding.selection!=selection
                or manifest.training!=metadata or manifest.frozen_state_id!=state.state_id
                or manifest.feature_schema!=state.feature_schema
                or inputs.binding.request.candidate_id!=metadata.candidate_id
                or inputs.binding.request.frozen_state_id!=state.state_id
                or inputs.binding.request.compatibility_id!=metadata.representation_compatibility_id
                or inputs.binding.output_id!=output.output_id
                or inputs.binding.representation_id!=output.representation.representation_id): raise ValueError()
        return ExecutionEvidence(record,metadata,state,native,inputs,output)
