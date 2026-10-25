"""M6 candidate-only declarations; no declaration authenticates training or loading."""
from typing import Literal, Annotated
from pydantic import Field, StrictInt, BeforeValidator, model_validator
from src.data.contracts.model_representation import RepresentationContract
from src.data.contracts.backtesting import identity
from src.data.contracts.model_representation import _finite_float
FiniteFloat=Annotated[float,BeforeValidator(_finite_float)]

Family=Literal['xgboost','lightgbm','lstm','gru','transformer']


class AdapterCapability(RepresentationContract):
    version: Literal['1.0']='1.0'
    family: Family
    adapter_version: Literal['canonical_adapter_v1']='canonical_adapter_v1'
    representation: Literal['tabular','recurrent','transformer']
    input_dtypes: tuple[Literal['float32','float64'],...]
    target_policy: Literal['strict_exact_float32_v1','same_as_input_v1']
    horizon: StrictInt=Field(default=1,ge=1,le=1)
    classification: Literal['CANDIDATE_ONLY']='CANDIDATE_ONLY'
    mask_policy: Literal['reject_missing_v1','explicit_missing_padding_offsets_v1']

    @model_validator(mode='after')
    def exact_family(self):
        kind='tabular' if self.family in ('xgboost','lightgbm') else 'transformer' if self.family=='transformer' else 'recurrent'
        dtypes=('float32',) if self.family=='xgboost' else ('float32','float64')
        target='strict_exact_float32_v1' if kind=='tabular' else 'same_as_input_v1'
        mask='reject_missing_v1' if kind=='tabular' else 'explicit_missing_padding_offsets_v1'
        if (self.representation,self.input_dtypes,self.target_policy,self.mask_policy)!=(kind,dtypes,target,mask):raise ValueError('exact family policy required')
        return self


def capability(family):
    # Exact immutable allowlist; metadata never serves as an authorization token.
    if type(family) is not str or family not in ('xgboost','lightgbm','lstm','gru','transformer'):
        from src.data.contracts.model_evaluation import _reject_declaration
        _reject_declaration()
    kind='tabular' if family in ('xgboost','lightgbm') else 'transformer' if family=='transformer' else 'recurrent'
    return AdapterCapability(family=family,representation=kind,mask_policy='reject_missing_v1' if kind=='tabular' else 'explicit_missing_padding_offsets_v1',
        input_dtypes=('float32',) if family=='xgboost' else ('float32','float64'),
        target_policy='strict_exact_float32_v1' if kind=='tabular' else 'same_as_input_v1')


class AdapterResources(RepresentationContract):
    max_rows: StrictInt=Field(default=1024,ge=1,le=2048)
    max_cells: StrictInt=Field(default=32768,ge=1,le=65536)
    max_bytes: StrictInt=Field(default=8*1024*1024,ge=1,le=16*1024*1024)
    max_elapsed_seconds: StrictInt=Field(default=30,ge=1,le=60)
    max_work_units: StrictInt=Field(default=10000000,ge=1,le=100000000)
    max_parameters: StrictInt=Field(default=100000,ge=1,le=1000000,description='Neural scalar parameters')
    max_tree_nodes: StrictInt=Field(default=256,ge=1,le=4096)


class TrainingSpecification(RepresentationContract):
    version: Literal['1.0']='1.0'
    family: Family
    adapter_version: Literal['canonical_adapter_v1']='canonical_adapter_v1'
    input_dtype: Literal['float32','float64']='float32'
    seed: StrictInt=Field(default=42,ge=0,le=2147483647)
    steps: StrictInt=Field(default=2,ge=1,le=16)
    hidden_size: StrictInt=Field(default=8,ge=1,le=64)
    learning_rate: FiniteFloat=Field(default=0.05,gt=0,le=1)
    resources: AdapterResources=Field(default_factory=AdapterResources)

    @model_validator(mode='after')
    def supported(self):
        cap=capability(self.family)
        if self.input_dtype not in cap.input_dtypes:raise ValueError('unsupported adapter precision')
        if cap.representation=='tabular' and self.hidden_size!=8:raise ValueError('neural-only parameter unsupported for trees')
        return self

    @property
    def specification_id(self):return identity(self.model_dump(mode='json',warnings='error'))


class TrainingCandidateMetadata(RepresentationContract):
    version: Literal['1.0']='1.0'
    classification: Literal['CANDIDATE_ONLY']='CANDIDATE_ONLY'
    candidate_kind: Literal['m6_adapter_execution_v1']='m6_adapter_execution_v1'
    specification: TrainingSpecification
    capability: AdapterCapability
    plan_digest: str=Field(pattern=r'^[0-9a-f]{64}$')
    preparation_id: str=Field(pattern=r'^[0-9a-f]{64}$')
    fitted_state_id: str=Field(pattern=r'^[0-9a-f]{64}$')
    representation_compatibility_id: str=Field(pattern=r'^[0-9a-f]{64}$')
    training_rows: StrictInt=Field(ge=1,le=2048)
    input_geometry: tuple[Annotated[tuple[StrictInt,...],Field(min_length=2,max_length=3)],...]=Field(min_length=1,max_length=3)
    input_digest: str=Field(pattern=r'^[0-9a-f]{64}$')
    backend: Literal['native_framework','test_stub']
    backend_version: str=Field(min_length=1,max_length=128)
    target_shape: tuple[StrictInt,StrictInt]
    target_dtype: Literal['float32','float64']
    fit_partition: Literal['training']='training'

    @model_validator(mode='after')
    def bound(self):
        cap=capability(self.specification.family)
        if self.capability!=cap:raise ValueError('exact adapter capability required')
        expected='float32' if cap.target_policy=='strict_exact_float32_v1' else self.specification.input_dtype
        if self.target_dtype!=expected or self.target_shape!=(self.training_rows,1):raise ValueError('exact H1 target precision/shape required')
        ranks=(2,) if cap.representation=='tabular' else (3,) if cap.representation=='recurrent' else (3,2,3)
        if (len(self.input_geometry)!=len(ranks) or self.training_rows>self.specification.resources.max_rows
                or any(len(shape)!=rank or shape[0]!=self.training_rows or any(v<=0 for v in shape)
                       for shape,rank in zip(self.input_geometry,ranks))):raise ValueError('exact H1 input geometry required')
        if cap.representation=='transformer' and self.input_geometry[2][1]!=1:raise ValueError('H1 decoder required')
        return self

    @property
    def candidate_id(self):return identity(self.model_dump(mode='json',warnings='error'))


TrainingStatus=Literal['SUCCESS','UNSUPPORTED_FAMILY','BUSY','FRAMEWORK_UNAVAILABLE',
    'CANCELLED_OR_EXPIRED','DENIED','RESOURCE_LIMIT','FAILED']


class TrainingReport(RepresentationContract):
    version: Literal['1.0']='1.0'
    classification: Literal['CANDIDATE_ONLY']='CANDIDATE_ONLY'
    status: TrainingStatus
    metadata: TrainingCandidateMetadata | None=None

    @model_validator(mode='after')
    def outcome(self):
        if (self.status=='SUCCESS')!=(self.metadata is not None):raise ValueError('complete success metadata required')
        return self
