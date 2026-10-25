"""Frozen Transformer stream assembly over provider-authenticated canonical buffers.

Reuses M4 arithmetic and encoder execution, never another feature algorithm.
Declarations and detached buffers are data, not publication/model authorization.
"""
from typing import Any, Literal
import numpy as np
from pydantic import Field, StrictInt, StrictStr, model_validator

from src.data.contracts.backtesting import identity
from src.data.contracts.model_evaluation import _reject_declaration
from src.data.contracts.model_representation import (
    RepresentationContract, TransformerRepresentation, OrderedFeatures, RepresentationTimestamp,
)
from src.preprocessing.canonical_recurrent import (
    RecurrentPreparationRequest, RecurrentPreprocessingState, RecurrentOutput,
    FrozenRecurrentPreprocessor, fit_recurrent_state, _pool, _number, _bits,
)
from src.preprocessing.canonical_tabular import TabularPreprocessingState
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline


class TransformerPreparationRequest(RecurrentPreparationRequest):
    model_family: Literal['transformer'] = 'transformer'
    static_names: tuple[StrictStr, ...] = Field(min_length=1, max_length=32)
    known_future_names: tuple[StrictStr, ...] = Field(min_length=1, max_length=32)
    decoder_horizon: StrictInt = Field(default=1, ge=1, le=1)
    future_offsets: tuple[StrictInt, ...] = (0,)

    @model_validator(mode='after')
    def groups(self):
        names = self.feature_names + self.static_names + self.known_future_names
        if len(set(names)) != len(names) or self.future_offsets != (0,):
            raise ValueError('disjoint ordered streams and exact H1 decoder required')
        return self

    def numerical_request(self, names, *, context=False):
        # Internal M4 numeric-buffer policy, never a model/predictor capability.
        data = self.model_dump(exclude={'static_names','known_future_names','decoder_horizon','future_offsets'})
        data.update(model_family='gru', feature_names=names)
        if context: data['missing'] = 'reject'
        return RecurrentPreparationRequest.model_validate(data)


def _roles(base, request):
    cfg = base.canonical_schema.configuration
    if base.plan.forecast_horizon != request.decoder_horizon:
        raise ValueError('original H1 plan required')
    direct = set(cfg.canonical_inputs) - {c.output for c in cfg.calculations}
    if not set(request.static_names) <= set(cfg.execution.dimension_fields) & direct & set(cfg.category_encoding):
        raise ValueError('static streams require original dimension fields')
    rules = {r.field:r for r in cfg.execution.exogenous}
    for name in request.known_future_names:
        rule = rules.get(name)
        if (name not in direct or rule is None or rule.availability not in ('known_in_advance','external_forecast')
                or rule.offset is not None or rule.missing != 'reject'
                or not rule.availability_field or not rule.evidence_field):
            raise ValueError('direct proven known-future covariate required')


class TransformerPreprocessingState(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    policy: Literal['canonical_transformer_frozen_v1'] = 'canonical_transformer_frozen_v1'
    request: TransformerPreparationRequest
    # Original M4 contracts retain the exact training statistics and evidence.
    encoder: RecurrentPreprocessingState
    context: RecurrentPreprocessingState

    @model_validator(mode='after')
    def bound(self):
        req = self.request
        if (self.encoder.base != self.context.base
                or self.encoder.request != req.numerical_request(req.feature_names)
                or self.context.request != req.numerical_request(req.static_names+req.known_future_names,context=True)):
            raise ValueError('original ordered group/scaler bindings required')
        _roles(self.encoder.base,req)
        return self

    @property
    def state_id(self):
        return identity(self.model_dump(mode='json',warnings='error'))

    @property
    def scaler_reference(self):
        data = self.encoder.scaler_reference.model_dump()
        data['state_digest'] = self.state_id
        return type(self.encoder.scaler_reference).model_validate(data)

    @property
    def static_schema(self):
        return OrderedFeatures(features=self.context.feature_schema.features[:len(self.request.static_names)])

    @property
    def future_schema(self):
        return OrderedFeatures(features=self.context.feature_schema.features[len(self.request.static_names):])


def fit_transformer_state(base,batches,request):
    try:
        base = TabularPreprocessingState.model_validate_json(base.canonical_json)
        req = TransformerPreparationRequest.model_validate_json(request.canonical_json)
        _roles(base,req)
        # M4 excludes held-out numeric buffers before touching their payloads.
        encoder = fit_recurrent_state(base,batches,req.numerical_request(req.feature_names))
        context = fit_recurrent_state(base,batches,req.numerical_request(req.static_names+req.known_future_names,context=True))
        return TransformerPreprocessingState(request=req,encoder=encoder,context=context)
    except Exception:
        pass
    _reject_declaration()


def _future_evidence(state, field, context_ids):
    return identity(['canonical_transformer_h1_availability_v1',state.state_id,field,list(context_ids)])


def _check_category(value, feature, base):
    if feature.categorical_policy == 'none':return
    rule=next(r for r in base.canonical_schema.configuration.execution.categories if r.field==feature.name)
    allowed=set(range(len(base.canonical_state.vocabularies[feature.name])))
    allowed.update(code for code in (rule.unknown_code,rule.missing_code) if code is not None)
    if value not in allowed:raise ValueError('original categorical code required')


class TransformerOutput(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    model_family: Literal['transformer'] = 'transformer'
    state: TransformerPreprocessingState
    representation: TransformerRepresentation
    encoder_periods: tuple[tuple[RepresentationTimestamp, ...], ...]
    decoder_periods: tuple[tuple[RepresentationTimestamp, ...], ...]
    encoder_values: tuple[tuple[tuple[Any, ...], ...], ...]
    static_values: tuple[tuple[Any, ...], ...]
    known_future_values: tuple[tuple[tuple[Any, ...], ...], ...]
    context_evidence: tuple[StrictStr, ...]

    @model_validator(mode='after')
    def actual(self):
        s,r = self.state,self.representation
        req=s.request;base=s.encoder.base
        if (r.binding.scaler!=s.scaler_reference or r.static!=s.static_schema or r.known_future!=s.future_schema
                or r.future_offsets!=req.future_offsets or len(self.context_evidence)!=len(r.samples)
                or any(len(v)!=64 or any(c not in '0123456789abcdef' for c in v) for v in self.context_evidence)):
            raise ValueError('exact frozen stream state required')
        # Reuse actual M4 encoder validation, including fill bits and release positions.
        body=r.model_dump(exclude={'family','past_role','static','static_axes','static_shape','static_missing_mask',
            'static_available_at','known_future','known_future_axes','known_future_shape','known_future_masks',
            'known_future_available_at','future_offsets','future_availability'})
        body['binding']['scaler']=s.encoder.scaler_reference.model_dump()
        RecurrentOutput(model_family=s.encoder.request.model_family,state=s.encoder,representation=body,
                        periods=self.encoder_periods,values=self.encoder_values)
        for steps,masks in zip(self.encoder_values,r.masks.missing):
            for row,bits in zip(steps,masks):
                for value,missing,feature in zip(row,bits,s.encoder.feature_schema.features):
                    if not missing:_check_category(value,feature,base)
        if (len(self.static_values)!=len(r.samples) or len(self.known_future_values)!=len(r.samples)
                or len(self.decoder_periods)!=len(r.samples)):
            raise ValueError('complete actual streams required')
        for i,grid in enumerate(r.samples):
            origin=grid[0].forecast_origin
            if self.decoder_periods[i]!=(origin,):raise ValueError('exact H1 decoder position required')
            if len(self.static_values[i])!=len(req.static_names) or len(self.known_future_values[i])!=1:
                raise ValueError('actual context shape mismatch')
            if len(self.known_future_values[i][0])!=len(req.known_future_names):raise ValueError('actual decoder width mismatch')
            # Context missing/substitution is unsupported: never invent known values.
            if (r.static_missing_mask[i]!=(False,)*len(req.static_names)
                    or r.known_future_masks.missing[i]!=((False,)*len(req.known_future_names),)
                    or r.static_available_at[i]!=origin or r.known_future_available_at[i]!=(origin,)):
                raise ValueError('exact context availability/masks required')
            for value,feature in zip(self.static_values[i]+self.known_future_values[i][0],s.context.feature_schema.features):
                _check_category(value,feature,base)
                _number(value,req.tensor_dtype)
                if type(value) is not float:raise ValueError('explicit context floats required')
            # Static fields must represent these actual point dimensions, using
            # the original vocabulary rather than a caller-selected category code.
            dimensions=dict(grid[0].dimensions)
            for name,value in zip(req.static_names,self.static_values[i]):
                engine=CanonicalFeaturePipeline(base.canonical_schema.configuration)
                expected=engine._category(name,dimensions[name],base.canonical_state)
                if _bits(value)!=_bits(float(expected)):raise ValueError('static identity mismatch')
        rules={rule.field:rule for rule in base.canonical_schema.configuration.execution.exogenous}
        if tuple((v.field,v.rule,v.evidence_identity) for v in r.future_availability)!=tuple(
                (name,rules[name].availability,_future_evidence(s,name,self.context_evidence)) for name in req.known_future_names):
            raise ValueError('original future availability evidence required')
        return self

    @property
    def output_id(self):return identity(self.model_dump(mode='json',warnings='error'))

    @property
    def encoder_tensor(self):return np.array(self.encoder_values,dtype=self.state.request.tensor_dtype)

    @property
    def static_tensor(self):return np.array(self.static_values,dtype=self.state.request.tensor_dtype)

    @property
    def decoder_tensor(self):return np.array(self.known_future_values,dtype=self.state.request.tensor_dtype)


class FrozenTransformerPreprocessor:
    """Load-only assembly; authenticated feature buffers must come from the owner."""
    def __init__(self,state):
        try:
            self.state=TransformerPreprocessingState.model_validate_json(state.canonical_json)
            return
        except Exception:
            pass
        _reject_declaration()

    def transform(self,batches):
        try:
            s=self.state;req=s.request;base=s.encoder.base
            cells=len(base.dataset.series_ids)*len(req.origins)*(req.window.window*len(req.feature_names)+len(req.static_names)+len(req.known_future_names))
            if cells>min(base.plan.budgets.max_output_cells,100000):raise ValueError('aggregate Transformer admission exceeded')
            rows=_pool(base,batches)
            # M4 supplies the exact encoder geometry and numeric execution.
            encoders=FrozenRecurrentPreprocessor(s.encoder).transform(batches)
            positions=tuple(tuple(f.name for f in base.feature_schema.features).index(n)
                            for n in req.static_names+req.known_future_names)
            outputs=[];total=0
            for encoder in encoders:
                static=[];future=[];evidence=[]
                for grid in encoder.representation.samples:
                    p=grid[0];old,raw,mask,release,oid=rows[(p.organization_id,p.series_id,p.forecast_origin)]
                    if old!=p or release!=p.forecast_origin:raise ValueError('original current feature row required')
                    values=[]
                    for j,pos in enumerate(positions):
                        if mask[pos]:raise ValueError('context values must be published and present')
                        value=_number(raw[pos])
                        if req.scaling!='identity_v1' and s.context.feature_schema.features[j].categorical_policy=='none':
                            value=(value-s.context.centers[j])/s.context.scales[j]
                        values.append(_number(value,req.tensor_dtype))
                    n=len(req.static_names);static.append(tuple(values[:n]));future.append((tuple(values[n:]),));evidence.append(oid)
                r=encoder.representation.model_dump();r['family']='transformer';r['binding']['scaler']=s.scaler_reference.model_dump()
                count=len(static);ns=len(req.static_names);nf=len(req.known_future_names)
                rules={rule.field:rule for rule in base.canonical_schema.configuration.execution.exogenous}
                r.update(static=s.static_schema.model_dump(),static_shape=(count,ns),static_missing_mask=((False,)*ns,)*count,
                    static_available_at=tuple(g[0].forecast_origin for g in encoder.representation.samples),
                    known_future=s.future_schema.model_dump(),known_future_shape=(count,1,nf),
                    known_future_masks=dict(lengths=(1,)*count,padding=((False,),)*count,missing=(((False,)*nf,),)*count),
                    known_future_available_at=tuple((g[0].forecast_origin,) for g in encoder.representation.samples),
                    future_offsets=(0,),future_availability=tuple(dict(field=n,rule=rules[n].availability,
                        evidence_identity=_future_evidence(s,n,evidence)) for n in req.known_future_names))
                output=TransformerOutput(state=s,representation=r,encoder_periods=encoder.periods,
                    decoder_periods=tuple((g[0].forecast_origin,) for g in encoder.representation.samples),
                    encoder_values=encoder.values,static_values=tuple(static),known_future_values=tuple(future),context_evidence=tuple(evidence))
                encoded=len(output.canonical_bytes);total+=encoded
                if encoded>1024*1024 or total>min(base.plan.budgets.max_assembly_bytes,32*1024*1024):raise ValueError('Transformer wire budget exceeded')
                outputs.append(output)
            return tuple(outputs)
        except Exception:
            pass
        _reject_declaration()
