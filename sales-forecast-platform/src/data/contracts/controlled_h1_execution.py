"""B3 controlled synthetic execution evidence; no production or write authority."""
from typing import Literal
import math
from pydantic import Field, StrictInt, StrictFloat, model_validator
from src.data.contracts.backtesting import identity
from src.data.contracts.candidate_package import Digest
from src.data.contracts.executable_inputs import ExecutableInputBinding
from src.data.contracts.training_adapter import Family
from src.data.contracts.model_representation import RepresentationContract


class ExecutionLimits(RepresentationContract):
    max_rows: StrictInt = Field(default=32, ge=1, le=32)
    max_cells: StrictInt = Field(default=1024, ge=1, le=1024)
    max_artifact_bytes: StrictInt = Field(default=1048576, ge=1, le=1048576)
    max_elapsed_seconds: StrictInt = Field(default=30, ge=1, le=30)


class ControlledH1ExecutionRequest(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    purpose: Literal['CONTROLLED_OFFLINE_H1_VALIDATION'] = 'CONTROLLED_OFFLINE_H1_VALIDATION'
    family: Family
    candidate_id: Digest
    artifact_id: Digest
    payload_sha256: Digest
    inputs: ExecutableInputBinding | None
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)

    @model_validator(mode="after")
    def bound(self):
        if self.family=="xgboost" and self.inputs is None: raise ValueError("owned inputs required")
        if self.inputs is not None and (self.family!=self.inputs.selection.family
                or self.candidate_id!=self.inputs.request.candidate_id): raise ValueError("exact owner bindings required")
        return self

    @property
    def request_id(self):
        return identity(['b3_controlled_h1_request_v1', self.model_dump(mode='json')])


class ExecutionGrantDeclaration(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    request: ControlledH1ExecutionRequest
    issued_at: StrictFloat
    expires_at: StrictFloat
    execution_deadline: StrictFloat
    max_executions: Literal[1] = 1

    @model_validator(mode='after')
    def bounded(self):
        if (not all(math.isfinite(v) for v in (self.issued_at,self.expires_at,self.execution_deadline))
                or not self.issued_at < self.execution_deadline <= self.expires_at <= self.issued_at+60):
            raise ValueError('bounded execution interval required')
        return self

    @property
    def grant_id(self):
        return identity(['b3_execution_grant_declaration_v1', self.model_dump(mode='json')])


class ControlledH1ExecutionResult(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    classification: Literal['CONTROLLED_OFFLINE_SYNTHETIC_NATIVE_H1'] = 'CONTROLLED_OFFLINE_SYNTHETIC_NATIVE_H1'
    request: ControlledH1ExecutionRequest
    grant: ExecutionGrantDeclaration
    adapter: Literal['owned_xgboost_3_2_0_h1_v1']
    prediction_dtype: Literal['float32'] = 'float32'
    predictions: tuple[StrictFloat, ...] = Field(min_length=1, max_length=32)
    prediction_sha256: Digest
    production_permission: Literal[False] = False
    persistable: Literal[False] = False

    @model_validator(mode='after')
    def bound(self):
        if (self.request.family!='xgboost' or self.request.inputs is None
                or len(self.predictions)!=len(self.request.inputs.request.snapshot_request.dataset.series_ids)
                or len(self.predictions)>self.request.limits.max_rows or self.grant.request != self.request
                or not all(math.isfinite(v) and abs(v)<=100000000 for v in self.predictions)):
            raise ValueError('exact finite evidence required')
        import struct, hashlib
        if any(struct.unpack('<f',struct.pack('<f',v))[0]!=v for v in self.predictions):
            raise ValueError('exact float32 predictions required')
        if hashlib.sha256(b''.join(struct.pack('<f', v) for v in self.predictions)).hexdigest()!=self.prediction_sha256:
            raise ValueError('exact prediction digest required')
        return self

    @property
    def result_id(self):
        return identity(['b3_controlled_h1_result_v1', self.model_dump(mode='json')])


class ExecutionOutcome(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    status: Literal['EXECUTED','DENIED','UNAVAILABLE']
    reason: Literal['CONTROLLED_VALIDATION_ONLY','INVALID_UNAUTHORIZED_OR_INTERRUPTED','ADAPTER_UNAVAILABLE','NATIVE_DEPENDENCY_UNAVAILABLE']
    result: ControlledH1ExecutionResult | None = None

    @model_validator(mode='after')
    def bound(self):
        if self.status=='EXECUTED':
            if self.result is None or self.reason!='CONTROLLED_VALIDATION_ONLY': raise ValueError('complete result required')
        elif self.result is not None: raise ValueError('no partial evidence on failure')
        return self
