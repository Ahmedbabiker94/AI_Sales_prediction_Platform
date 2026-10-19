"""Offline candidate data, not authentication, registry entries or production aliases."""
import base64
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import math
import struct
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import Field, StrictInt, model_validator

from .dataset import operational_json, _opaque
from .temporal_preparation import _TemporalContract
from .model_training import TrainingHandoffProvenance, TrainingHandoffScope


def digest(data: bytes):
    return hashlib.sha256(data).hexdigest()


class OfflineXGBoostConfiguration(_TemporalContract):
    version: Literal['1.0'] = '1.0'
    model_family: Literal['xgboost'] = 'xgboost'
    horizon: StrictInt = Field(default=1,ge=1,le=1)
    serialization: Literal['ubj'] = 'ubj'
    input_policy: Literal['strict_exact_float32_v1'] = 'strict_exact_float32_v1'
    comparison_policy: Literal['bitwise_float32_v1'] = 'bitwise_float32_v1'
    estimators: StrictInt = Field(default=8,ge=1,le=16)
    max_depth: StrictInt = Field(default=2,ge=1,le=3)
    seed: StrictInt = Field(default=42,ge=0,le=2**31-1)
    threads: StrictInt = Field(default=1,ge=1,le=1)
    max_rows: StrictInt = Field(default=512,ge=2,le=512)
    max_columns: StrictInt = Field(default=32,ge=1,le=32)
    max_cells: StrictInt = Field(default=8192,ge=1,le=8192)
    max_artifact_bytes: StrictInt = Field(default=1024*1024,ge=1,le=1024*1024)
    max_bundle_bytes: StrictInt = Field(default=2*1024*1024,ge=1,le=2*1024*1024)
    max_elapsed_seconds: StrictInt = Field(default=60,ge=1,le=60)
    max_evidence_records: StrictInt = Field(default=512,ge=1,le=512)
    max_evidence_bytes: StrictInt = Field(default=16*1024*1024,ge=1,le=16*1024*1024)

    @property
    def parameters(self):
        return dict(objective='reg:squarederror',booster='gbtree',tree_method='hist',device='cpu',
            nthread=self.threads,seed=self.seed,max_depth=self.max_depth,eta=0.25,
            subsample=1.0,colsample_bytree=1.0,min_child_weight=1,max_bin=64,verbosity=0)


class TrainingInvocation(_TemporalContract):
    version: Literal['1.0'] = '1.0'
    invocation_id: str
    fixture_approval_id: str
    environment: Literal['offline_approved_fixture'] = 'offline_approved_fixture'
    # Operator declarations identify authorities; they do not authenticate them.
    release_authority: str
    historical_value_authority: str
    category_authority: str
    preparation_authority: str
    training_authority: str
    external_authority: str | None = None

    @model_validator(mode='after')
    def identifiers(self):
        for k,v in self.model_dump().items():
            if k not in ('version','environment') and v is not None:_opaque(v)
        return self


class RuntimeIdentity(_TemporalContract):
    python: str
    xgboost: Literal['3.2.0']
    numpy: str
    pandas: str
    platform: str
    source_commit: str = Field(pattern=r'^[0-9a-f]{40}$')
    source_files: tuple[tuple[str,str],...]
    source_matches_commit: bool

    @model_validator(mode='after')
    def bounded(self):
        if not 1<=len(self.source_files)<=16:raise ValueError('bounded source snapshot required')
        if tuple(sorted(self.source_files))!=self.source_files or len(dict(self.source_files))!=len(self.source_files):raise ValueError('unique source snapshot required')
        for path,sha in self.source_files:
            if path.startswith('/') or '..' in path.split('/') or len(sha)!=64 or any(v not in '0123456789abcdef' for v in sha):raise ValueError('invalid source identity')
        return self


def original_value(encoded):
    data=json.loads(encoded)
    if data[0]=='decimal':return Decimal((data[1],tuple(data[2]),data[3]))
    if data[0]=='integer':return int(data[1])
    return struct.unpack('>d',bytes.fromhex(data[1]))[0]


def strict_float32(values):
    """A declared model-specific restriction, never a relaxed D1 policy."""
    # Check raw sequence leaves before NumPy can erase Boolean identity. Numeric
    # array-like inputs are typed views; their earlier construction is not attested
    # here. Keep the existing output shape and numeric conversion unchanged.
    pending=[(iter((values,)),None)];active=set()
    while pending:
        try:item=next(pending[-1][0])
        except StopIteration:
            _,identity=pending.pop()
            if identity is not None:active.remove(identity)
            continue
        if isinstance(item,(bool,np.bool_)):
            raise ValueError('Boolean model inputs are unsupported')
        array_view=isinstance(item,memoryview) or hasattr(item,'__array__') or hasattr(item,'__array_interface__')
        if array_view or not isinstance(item,(Sequence,int,float,complex,np.number)):
            if np.asarray(item).dtype.kind=='b':raise ValueError('Boolean model inputs are unsupported')
        if isinstance(item,Sequence) and not isinstance(item,(str,bytes,bytearray)) and not array_view:
            identity=id(item)
            if identity in active:raise ValueError('cyclic model input sequences are unsupported')
            active.add(identity);pending.append((iter(item),identity))
    raw=np.asarray(values)
    if raw.dtype.kind not in ('i','u','f') or raw.dtype.itemsize>8:
        raise ValueError('explicit integer/float model inputs required')
    a=raw.astype(np.float64)
    if not np.isfinite(a).all() or np.any(np.abs(a)>1_000_000):raise ValueError('bounded finite model inputs required')
    b=a.astype(np.float32)
    if not np.array_equal(b.astype(np.float64),a) or not np.array_equal(np.signbit(b),np.signbit(a)):
        raise ValueError('model input loses precision in float32')
    return b


class CandidateManifest(_TemporalContract):
    version: Literal['1.0'] = '1.0'
    status: Literal['CANDIDATE'] = 'CANDIDATE'
    production_status: Literal['NOT_APPROVED_FOR_PRODUCTION'] = 'NOT_APPROVED_FOR_PRODUCTION'
    invocation: TrainingInvocation
    training: OfflineXGBoostConfiguration
    runtime: RuntimeIdentity
    scope: TrainingHandoffScope
    provenance_json: str = Field(max_length=1024*1024)
    parameters_json: str = Field(max_length=8192)
    feature_names: tuple[str,...]
    feature_dtypes: tuple[str,...]
    row_index: tuple[tuple[str,...],...]
    matrix_float32_hex: str = Field(max_length=8192*8)
    labels_float32_hex: str = Field(max_length=512*8)
    predictions_float32_hex: str = Field(max_length=512*8)
    state_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    artifact_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    artifact_size: StrictInt = Field(ge=1,le=1024*1024)

    @property
    def provenance(self):return TrainingHandoffProvenance.model_validate_json(self.provenance_json)

    @property
    def matrix(self):
        rows=len(self.row_index);cols=len(self.feature_names)
        data=np.frombuffer(bytes.fromhex(self.matrix_float32_hex),dtype='<f4').copy().reshape(rows,cols)
        frame=pd.DataFrame(data,columns=self.feature_names,index=pd.MultiIndex.from_tuples(self.row_index))
        typed=frame.astype(dict(zip(self.feature_names,self.feature_dtypes)))
        back=typed.to_numpy(dtype=np.float64)
        if not np.array_equal(back,data.astype(np.float64)) or not np.array_equal(np.signbit(back),np.signbit(data)):
            raise ValueError('representation dtype conversion is lossy')
        return typed

    @property
    def native_binding(self):
        p=self.provenance
        return dict(d1_handoff=p.handoff_id,state=self.state_digest,configuration=p.configuration_digest,
            invocation=digest(self.invocation.model_dump_json().encode()),
            training=digest(operational_json([self.training.model_dump(mode='json'),json.loads(self.parameters_json)]).encode()),
            representation=digest(p.states[0].representation_json.encode()),
            matrix=digest(bytes.fromhex(self.matrix_float32_hex)),labels=digest(bytes.fromhex(self.labels_float32_hex)),
            runtime=digest(self.runtime.model_dump_json().encode()))

    @model_validator(mode='after')
    def binding(self):
        p=self.provenance;c=self.training
        if self.parameters_json!=operational_json(c.parameters):raise ValueError('native training parameters mismatch')
        if len(p.plan.folds)!=1 or len(p.plan.datasets)!=1 or len(p.states)!=1:raise ValueError('one fold and organization required')
        if p.scope_digest!=self.scope.digest or p.plan.datasets!=self.scope.datasets or p.configuration_digest!=self.scope.configuration_digest:
            raise ValueError('owned D1 binding mismatch')
        state=p.states[0];schema=__import__('src.features.feature_schema_compatibility',fromlist=['FeatureSchemaCompatibility']).FeatureSchemaCompatibility.model_validate_json(state.representation_json)
        if (digest(state.state_json.encode())!=self.state_digest or self.feature_names!=schema.configuration.ordered_outputs
                or self.feature_dtypes!=tuple(schema.configuration.output_dtypes[n] for n in self.feature_names)):
            raise ValueError('frozen representation mismatch')
        count=len(self.row_index);width=len(self.feature_names)
        if not 2<=count<=c.max_rows or not 1<=width<=c.max_columns or count*width>c.max_cells:raise ValueError('candidate size budget')
        for field,n in ((self.matrix_float32_hex,count*width),(self.labels_float32_hex,count),(self.predictions_float32_hex,count)):
            raw=bytes.fromhex(field)
            if len(raw)!=n*4 or raw.hex()!=field or not np.isfinite(np.frombuffer(raw,dtype='<f4')).all():raise ValueError('invalid native numeric payload')
        expected=[];label_values=[];origin_ids=[];matrix=self.matrix
        from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
        from src.services.canonical_training_preparation_service import _value_identity
        engine=CanonicalFeaturePipeline(schema.configuration);fold=p.plan.folds[0];ds=p.plan.datasets[0]
        for origin in fold.training_origins:
            local=engine._time(origin);originals=[]
            for sid in ds.series_ids:
                expected.append((fold.fold_id,str(ds.reference.organization_id),str(sid),schema.configuration.target_metric,local.isoformat(),'1',local.isoformat()))
                claim=next(x for x in p.labels if x.series_id==sid and x.forecast_origin==origin)
                v=original_value(claim.original_value_identity_json);originals.append(v);label_values.append(engine.convert_numeric(v))
            start=len(expected)-len(ds.series_ids)
            feature_rows=tuple(matrix.iloc[start:len(expected)].itertuples(index=False,name=None))
            body=[p.plan.digest,fold.fold_id,'training',ds.model_dump(mode='json'),schema.schema_id,local.isoformat(),
                [[_value_identity(v) for v in row] for row in feature_rows],[_value_identity(v) for v in originals]]
            origin_ids.append(digest(operational_json(body).encode()))
        if tuple(expected)!=self.row_index or tuple(origin_ids)!=p.origin_ids:raise ValueError('training event/feature provenance mismatch')
        actual=np.frombuffer(bytes.fromhex(self.labels_float32_hex),dtype='<f4')
        if strict_float32(label_values).tobytes()!=actual.tobytes():raise ValueError('training label conversion mismatch')
        strict_float32(matrix.to_numpy())
        return self


@dataclass(frozen=True)
class CandidateBundle:
    """Immutable canonical bytes. Public construction grants no loading authority."""
    payload: bytes

    @property
    def bundle_id(self):return digest(self.payload)

    @property
    def manifest(self):return self.validate()[0]

    def validate(self):
        if type(self.payload) is not bytes or len(self.payload)>2*1024*1024:raise ValueError('bounded bundle bytes required')
        body=json.loads(self.payload)
        if set(body)!={'manifest','artifact_base64'} or operational_json(body).encode()!=self.payload:raise ValueError('canonical bundle encoding required')
        manifest=CandidateManifest.model_validate(body['manifest'])
        artifact=base64.b64decode(body['artifact_base64'],validate=True)
        if (len(self.payload)>manifest.training.max_bundle_bytes or len(artifact)>manifest.training.max_artifact_bytes
                or len(artifact)!=manifest.artifact_size or digest(artifact)!=manifest.artifact_digest
                or base64.b64encode(artifact).decode()!=body['artifact_base64']):raise ValueError('artifact binding mismatch')
        return manifest,artifact


def assemble_candidate(manifest,artifact):
    """Pure complete assembly; no file writes, aliases or partial publication."""
    bundle=CandidateBundle(operational_json(dict(manifest=manifest.model_dump(mode='json'),artifact_base64=base64.b64encode(artifact).decode())).encode())
    bundle.validate()
    return bundle
