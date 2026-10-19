"""D1 preparation declarations. No declaration/digest authenticates model fitting.

The trusted service owns capture, authority selection and issuance. These frozen
contracts are audit data, never credentials or inputs to a fitting permission API.
"""
import hashlib
import json
import re
import struct
import math

import pandas as pd
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictInt, model_validator

from .dataset import operational_json, _opaque
from .features import FeatureState
from .temporal_preparation import (
    _TemporalContract, TemporalBudgets, TemporalDatasetBinding,
    TemporalPreparationPlan, TemporalTimestamp,
)


def _digest(value):
    return hashlib.sha256(operational_json(value).encode()).hexdigest()


class TrainingHandoffScope(_TemporalContract):
    """Operator-owned publication/configuration scope, not caller authorization."""
    version: Literal['1.0'] = '1.0'
    scope_id: str
    purpose: Literal['production_training'] = 'production_training'
    datasets: tuple[TemporalDatasetBinding, ...] = Field(min_length=1, max_length=8)
    configuration_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    label_authority_id: str
    label_authority_version: str
    budgets: TemporalBudgets = Field(default_factory=TemporalBudgets)
    max_verification_bytes: StrictInt = Field(default=128*1024*1024, ge=1)
    max_verification_calls: StrictInt = Field(default=10000, ge=1, le=10000)
    max_verification_claims: StrictInt = Field(default=200000, ge=1)
    max_handoff_bytes: StrictInt = Field(default=32*1024*1024, ge=1)

    @model_validator(mode='after')
    def scoped(self):
        for v in (self.scope_id, self.label_authority_id, self.label_authority_version): _opaque(v)
        orgs=tuple(d.reference.organization_id for d in self.datasets)
        if tuple(sorted(set(orgs),key=str)) != orgs:
            raise ValueError('one ordered publication per organization required')
        if len(self.model_dump_json().encode()) > 256*1024:
            raise ValueError('bounded operator scope required')
        return self

    @property
    def digest(self):
        return _digest(self.model_dump(mode='json'))


class TrainingLabelClaim(_TemporalContract):
    """Exact captured target revision requiring independent attestation at M."""
    version: Literal['1.0'] = '1.0'
    protocol: Literal['captured_value_as_of_M_v1'] = 'captured_value_as_of_M_v1'
    purpose: Literal['production_training'] = 'production_training'
    dataset: TemporalDatasetBinding
    plan_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    fold_id: str
    series_id: UUID
    dimensions: tuple[tuple[str,str], ...] = Field(max_length=128)
    metric: str
    unit: str
    currency: str | None
    frequency: Literal['daily','weekly']
    timezone: str
    forecast_origin: TemporalTimestamp
    period_start: TemporalTimestamp
    period_end: TemporalTimestamp
    available_at: TemporalTimestamp
    model_fit_cutoff: TemporalTimestamp
    source_record_id: str
    lineage_id: UUID
    evidence_reference: str
    original_value_identity_json: str = Field(max_length=16384)

    @model_validator(mode='after')
    def aligned(self):
        for v in (self.fold_id,self.metric,self.unit,self.timezone,self.source_record_id,self.evidence_reference): _opaque(v)
        if self.currency is not None: _opaque(self.currency)
        if (self.series_id not in self.dataset.series_ids
                or tuple(sorted(self.dimensions)) != self.dimensions
                or len(dict(self.dimensions)) != len(self.dimensions)):
            raise ValueError('label series/measurement scope mismatch')
        for k,v in self.dimensions: _opaque(k); _opaque(v)
        dates=(self.forecast_origin,self.period_start,self.period_end,self.available_at,self.model_fit_cutoff)
        if (any(v.tzinfo is None for v in dates) or self.forecast_origin!=self.period_start
                or not self.period_start<self.period_end<=self.available_at<=self.model_fit_cutoff):
            raise ValueError('training label must be eligible by M, not necessarily O')
        body=json.loads(self.original_value_identity_json)
        if operational_json(body)!=self.original_value_identity_json or not isinstance(body,list) or not body:
            raise ValueError('canonical captured value identity required')
        if body[0]=='integer':
            valid=len(body)==2 and type(body[1]) is str and len(body[1])<=5000 and re.fullmatch(r'0|-?[1-9][0-9]*',body[1])
        elif body[0]=='decimal':
            valid=(len(body)==4 and type(body[1]) is int and body[1] in (0,1)
                and type(body[2]) is list and 1<=len(body[2])<=4096
                and all(type(d) is int and 0<=d<=9 for d in body[2])
                and type(body[3]) is int and abs(body[3])<=10000)
        elif body[0]=='float64':
            valid=(len(body)==2 and type(body[1]) is str and re.fullmatch(r'[0-9a-f]{16}',body[1])
                and math.isfinite(struct.unpack('>d',bytes.fromhex(body[1]))[0]))
        else: valid=False
        if not valid: raise ValueError('bounded exact numeric identity required')
        return self

    @property
    def claim_id(self):
        return _digest(self.model_dump(mode='json'))


class TrainingFittedState(_TemporalContract):
    """The actual feature state fitted during preparation, not a fitted model."""
    fold_id: str
    dataset: TemporalDatasetBinding
    state_json: str = Field(max_length=4*1024*1024)
    representation_json: str = Field(max_length=128*1024)

    @property
    def state(self):
        return FeatureState.model_validate_json(self.state_json)

    @model_validator(mode='after')
    def local(self):
        _opaque(self.fold_id)
        if self.state.organization_ids != (self.dataset.reference.organization_id,):
            raise ValueError('one organization per fitted feature state required')
        return self


class TrainingHandoffProvenance(_TemporalContract):
    version: Literal['1.0'] = '1.0'
    status: Literal['PREPARED'] = 'PREPARED'
    model_status: Literal['NOT_MODEL_FITTED'] = 'NOT_MODEL_FITTED'
    purpose: Literal['production_training'] = 'production_training'
    scope_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    plan: TemporalPreparationPlan
    configuration_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    preparation_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    states: tuple[TrainingFittedState, ...] = Field(min_length=1, max_length=256)
    labels: tuple[TrainingLabelClaim, ...] = Field(min_length=1, max_length=10000)
    origin_ids: tuple[str, ...] = Field(min_length=1, max_length=8192)

    @model_validator(mode='after')
    def complete(self):
        p=self.plan
        if p.configuration_digest!=self.configuration_digest or p.forecast_horizon!=1 or p.capability!='supervised_h1':
            raise ValueError('H1 training configuration required')
        state_keys=tuple((f.fold_id,d.reference.organization_id) for f in p.folds for d in p.datasets)
        if tuple((s.fold_id,s.dataset.reference.organization_id) for s in self.states)!=state_keys:
            raise ValueError('complete ordered fold/organization states required')
        for s in self.states:
            fold=next(f for f in p.folds if f.fold_id==s.fold_id)
            if s.dataset not in p.datasets: raise ValueError('foreign state publication')
            state=s.state
            from src.features.feature_schema_compatibility import FeatureSchemaCompatibility
            schema=FeatureSchemaCompatibility.model_validate_json(s.representation_json)
            zone=schema.configuration.execution.temporal.timezone
            begin=pd.Timestamp(fold.calibration_start).tz_convert(zone).isoformat()
            cutoff=pd.Timestamp(fold.categorical_cutoff).tz_convert(zone).isoformat()
            partition=_digest([p.digest,fold.fold_id,s.dataset.model_dump(mode='json'),self.configuration_digest,begin,cutoff])
            if (state.configuration_digest!=self.configuration_digest or state.training_cutoff!=fold.categorical_cutoff
                    or state.training_partition_reference!=partition or schema.fitted_state_id!=state.state_id
                    or schema.configuration_digest!=self.configuration_digest or schema.forecast_horizon!=1
                    or schema.training_schema_digest!=state.training_schema_digest):
                raise ValueError('original fitted state and representation provenance required')
        expected={(f.fold_id,d.reference.organization_id,s,o) for f in p.folds for d in p.datasets
                  for o in f.training_origins for s in d.series_ids}
        actual=[(c.fold_id,c.dataset.reference.organization_id,c.series_id,c.forecast_origin) for c in self.labels]
        if len(actual)!=len(expected) or set(actual)!=expected:
            raise ValueError('exact training-only label coverage required')
        for c in self.labels:
            f=next(f for f in p.folds if f.fold_id==c.fold_id)
            if c.plan_digest!=p.digest or c.dataset not in p.datasets or c.model_fit_cutoff!=f.model_fit_cutoff:
                raise ValueError('training label provenance mismatch')
        count=sum(len(f.training_origins) for f in p.folds)*len(p.datasets)
        if len(self.origin_ids)!=count or len(set(self.origin_ids))!=count:
            raise ValueError('complete unique training origin identities required')
        for v in self.origin_ids:
            if len(v)!=64 or any(c not in '0123456789abcdef' for c in v):raise ValueError('invalid origin identity')
        return self

    @property
    def handoff_id(self):
        return _digest(self.model_dump(mode='json'))
