"""B2 owned data only. Selection declarations and digests grant no execution."""
from dataclasses import dataclass
from typing import Literal
from pydantic import Field, StrictInt, model_validator
from src.data.contracts.backtesting import identity
from src.data.contracts.candidate_package import Digest
from src.data.contracts.model_representation import RepresentationContract
from src.data.contracts.runtime_snapshot import RuntimeSnapshotRequest
from src.data.contracts.training_adapter import Family, capability


class InputAdapterSelection(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    family: Family
    representation: Literal['tabular','recurrent','transformer']
    frozen_state_id: Digest
    compatibility_id: Digest
    adapter: Literal['canonical_m3_xgboost_inputs_v1','UNAVAILABLE']

    @model_validator(mode='after')
    def exact(self):
        if self.representation != capability(self.family).representation:
            raise ValueError('family representation mismatch')
        expected = 'canonical_m3_xgboost_inputs_v1' if self.family == 'xgboost' else 'UNAVAILABLE'
        if self.adapter != expected: raise ValueError('unsupported executable input adapter')
        return self


class InputPreparationLimits(RepresentationContract):
    max_elapsed_seconds: StrictInt = Field(default=30,ge=1,le=60)
    max_request_bytes: StrictInt = Field(default=256*1024,ge=1,le=256*1024)
    max_output_bytes: StrictInt = Field(default=128*1024,ge=1,le=512*1024)


class ExecutableInputRequest(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    purpose: Literal['controlled_offline_h1_input_preparation_v1'] = 'controlled_offline_h1_input_preparation_v1'
    candidate_id: Digest
    family: Family
    frozen_state_id: Digest
    compatibility_id: Digest
    snapshot_id: Digest
    snapshot_request: RuntimeSnapshotRequest
    limits: InputPreparationLimits = Field(default_factory=InputPreparationLimits)

    @property
    def request_id(self):
        return identity(['b2_executable_input_request_v1', self.model_dump(mode='json', warnings='error')])


class ExecutableInputBinding(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    classification: Literal['OWNED_INPUT_DATA_ONLY'] = 'OWNED_INPUT_DATA_ONLY'
    request: ExecutableInputRequest
    selection: InputAdapterSelection
    output_id: Digest
    representation_id: Digest
    native_ingress_dtype: Literal['float32'] = 'float32'
    runtime_permission: Literal[False] = False
    production_permission: Literal[False] = False

    @model_validator(mode='after')
    def exact(self):
        r, s = self.request, self.selection
        if (r.family != s.family or r.frozen_state_id != s.frozen_state_id
                or r.compatibility_id != s.compatibility_id or s.adapter == 'UNAVAILABLE'):
            raise ValueError('complete input binding required')
        return self

    @property
    def binding_id(self):
        return identity(['b2_owned_input_binding_v1', self.model_dump(mode='json', warnings='error')])


@dataclass(frozen=True, slots=True, eq=False)
class OwnedInputEnvelope:
    binding: ExecutableInputBinding
    output: RepresentationContract

    def __reduce__(self):
        raise TypeError('owned input envelopes are process-local')


@dataclass(frozen=True)
class InputPreparationOutcome:
    status: Literal['PREPARED_INPUTS','DENIED','UNAVAILABLE']
    reason: str
    inputs: OwnedInputEnvelope | None = None
    version: Literal['1.0'] = '1.0'

    def __post_init__(self):
        if self.version != '1.0' or self.status not in ('PREPARED_INPUTS','DENIED','UNAVAILABLE'):
            raise ValueError('invalid input outcome')
        if self.status == 'PREPARED_INPUTS':
            if type(self.inputs) is not OwnedInputEnvelope or self.reason != 'INPUT_DATA_ONLY':
                raise ValueError('complete owned input data required')
        elif self.inputs is not None:
            raise ValueError('failed input preparation has no admitted data')
