"""M4 sequence/scaler kernel over authenticated canonical M3 feature buffers.

No feature formulas, model frameworks or publication credentials live here.
Metadata and reconstructed buffers are data, not authorization; services own
provider admission. Slots retain their original temporal positions when masked.
"""
import math
import struct
from fractions import Fraction
from typing import Annotated, Any, Literal

import numpy as np
import pandas as pd
from pydantic import BeforeValidator, Field, StrictInt, StrictStr, model_validator

from src.data.contracts.backtesting import identity
from src.data.contracts.model_evaluation import _reject_declaration
from src.data.contracts.model_representation import (
    RepresentationContract, RepresentationTimestamp, OrderedFeatures,
    RecurrentRepresentation, ScalerStateReference, WindowDeclaration, _finite_float,
)
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
from src.preprocessing.canonical_tabular import TabularPreprocessingState, TabularOutput

FiniteFloat = Annotated[float, BeforeValidator(_finite_float)]


class RecurrentPreparationRequest(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    model_family: Literal['lstm', 'gru']
    fold_id: StrictStr = Field(min_length=1, max_length=128)
    origins: tuple[RepresentationTimestamp, ...] = Field(min_length=1, max_length=512)
    feature_names: tuple[StrictStr, ...] = Field(min_length=1, max_length=64)
    window: WindowDeclaration
    tensor_dtype: Literal['float64', 'float32'] = 'float64'
    scaling: Literal['identity_v1', 'standard_v1'] = 'standard_v1'
    missing: Literal['reject', 'mask'] = 'reject'

    @model_validator(mode='after')
    def ordered(self):
        instants = tuple(pd.Timestamp(v).tz_convert('UTC') for v in self.origins)
        if instants != tuple(sorted(set(instants))) or len(set(self.feature_names)) != len(self.feature_names):
            raise ValueError('unique ordered origins and features required')
        if self.tensor_dtype == 'float32': _number(self.window.fill_value, 'float32')
        return self


def _number(value, dtype='float64'):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('finite non-Boolean number required')
    widened = float(value)
    if type(value) is int and int(widened) != value:
        raise ValueError('exact float64 widening required')
    if dtype == 'float32':
        from src.data.contracts.model_candidate import strict_float32
        strict_float32([value])
    return widened


def _bits(value):
    return struct.pack('>d', value).hex()


class RecurrentPreprocessingState(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    policy: Literal['canonical_recurrent_frozen_v1'] = 'canonical_recurrent_frozen_v1'
    arithmetic: Literal['binary64_population_standard_v1'] = 'binary64_population_standard_v1'
    base: TabularPreprocessingState
    request: RecurrentPreparationRequest
    feature_schema: OrderedFeatures
    centers: tuple[FiniteFloat, ...]
    scales: tuple[FiniteFloat, ...]
    counts: tuple[StrictInt, ...]
    fit_rows_digest: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    fit_row_count: StrictInt = Field(ge=1)
    fit_partition: Literal['training'] = 'training'

    @model_validator(mode='after')
    def bound(self):
        base, req = self.base, self.request
        if base.fold_id != req.fold_id: raise ValueError('original fold required')
        fold = next(f for f in base.plan.folds if f.fold_id == req.fold_id)
        if any(o not in fold.origins for o in req.origins): raise ValueError('origin outside original plan')
        expected = len(fold.training_origins)*len(base.dataset.series_ids)
        if self.fit_row_count != expected: raise ValueError('complete training-origin fit required')
        features = self.feature_schema.features
        if (tuple(f.name for f in features) != req.feature_names
                or len(self.centers) != len(features) or len(self.scales) != len(features)
                or len(self.counts) != len(features)):
            raise ValueError('complete ordered scaler parameters required')
        original = {f.name:f for f in base.feature_schema.features}
        for f, center, scale, count in zip(features, self.centers, self.scales, self.counts):
            source = original[f.name]
            if (source.dtype == 'bool' or f.dtype != req.tensor_dtype
                    or f.numeric_policy != 'strict_exact_'+req.tensor_dtype+'_v1'
                    or f.categorical_policy != source.categorical_policy or f.vocabulary_field != source.vocabulary_field
                    or f.missing != req.missing or not 1 <= count <= expected or scale <= 0):
                raise ValueError('incompatible scaler/schema policy')
            # Ordinal category codes retain their original vocabulary meaning.
            if req.scaling == 'identity_v1' or f.categorical_policy != 'none':
                if center != 0.0 or scale != 1.0: raise ValueError('identity parameters required')
        return self

    @property
    def state_id(self):
        return identity(self.model_dump(mode='json', warnings='error'))

    @property
    def scaler_reference(self):
        fold = next(f for f in self.base.plan.folds if f.fold_id == self.base.fold_id)
        return ScalerStateReference(policy=self.request.scaling, state_digest=self.state_id,
            training_plan_digest=self.base.plan.digest, fold_id=fold.fold_id,
            training_origins_digest=identity([o.isoformat() for o in fold.training_origins]),
            training_cutoff=fold.model_fit_cutoff, canonical_state_id=self.base.canonical_state.state_id,
            source_schema_id=self.base.canonical_schema.schema_id,
            organization_ids=self.base.canonical_state.organization_ids)


def _pool(base, batches, *, training_only=False):
    """Validate detached canonical buffers. This does not authenticate callers."""
    if type(batches) is not tuple or not batches or len(batches) > 8192: raise ValueError()
    rows = {}; used = 0; encoded_bytes = 0
    for candidate in batches:
        if training_only:
            if type(candidate) is not TabularOutput:raise ValueError('canonical feature buffer required')
            # Inspect partition metadata only. Held-out value payloads are never
            # serialized, converted or visited by the numeric scaler fitter.
            if candidate.representation.binding.partition!='training':continue
        encoded=candidate.canonical_json
        encoded_bytes+=len(encoded.encode('utf-8'))
        if encoded_bytes>min(base.plan.budgets.max_assembly_bytes,32*1024*1024):raise ValueError('feature representation budget exceeded')
        output = TabularOutput.model_validate_json(encoded)
        if output.state != base: raise ValueError('foreign frozen feature state')
        for grid, values, masks, release in zip(output.representation.samples, output.values,
                                                output.representation.missing_mask, output.representation.available_at):
            point = grid[0]; key = (point.organization_id, point.series_id, point.forecast_origin)
            if key in rows: raise ValueError('duplicate feature-origin row')
            rows[key] = (point, values, masks, release, output.output_id)
            used += len(values)
            if used > min(base.plan.budgets.max_output_cells, 100000): raise ValueError('feature buffer budget exceeded')
    return rows


def fit_recurrent_state(base, batches, request):
    """Fit once, only on complete original training-origin feature rows, not y."""
    try:
        base = TabularPreprocessingState.model_validate_json(base.canonical_json)
        req = RecurrentPreparationRequest.model_validate_json(request.canonical_json)
        rows = _pool(base, batches, training_only=True)
        fold = next(f for f in base.plan.folds if f.fold_id == req.fold_id)
        names = tuple(f.name for f in base.feature_schema.features)
        positions = tuple(names.index(name) for name in req.feature_names)
        features = OrderedFeatures(features=tuple(dict(name=name, dtype=req.tensor_dtype,
            numeric_policy='strict_exact_'+req.tensor_dtype+'_v1', missing=req.missing,
            categorical_policy=base.feature_schema.features[p].categorical_policy,
            vocabulary_field=base.feature_schema.features[p].vocabulary_field) for name,p in zip(req.feature_names,positions)))
        columns = [[] for _ in positions]; evidence = []
        for series in base.dataset.series_ids:
            for origin in fold.training_origins:
                point, values, masks, release, _ = rows[(base.dataset.reference.organization_id, series, origin)]
                if release != origin: raise ValueError('canonical feature release required')
                encoded = []
                for col, p in zip(columns, positions):
                    if masks[p]:
                        if req.missing != 'mask': raise ValueError('missing training feature')
                        encoded.append(None)
                    else:
                        v = _number(values[p]); col.append(v); encoded.append(_bits(v))
                evidence.append([str(point.organization_id), str(series), origin.isoformat(), encoded])
        centers=[]; scales=[]; counts=[]
        for feature, column in zip(features.features, columns):
            if not column: raise ValueError('no genuine training observations')
            counts.append(len(column))
            if req.scaling == 'identity_v1' or feature.categorical_policy != 'none':
                center, scale = 0.0, 1.0
            else:
                exact = tuple(Fraction.from_float(v) for v in column)
                mean = sum(exact)/len(exact)
                variance = sum((v-mean)**2 for v in exact)/len(exact)
                center = float(mean)
                scale = 1.0 if variance == 0 else math.sqrt(float(variance))
                if not math.isfinite(center) or not math.isfinite(scale) or scale <= 0:
                    raise ValueError('unrepresentable scaler parameters')
            centers.append(center); scales.append(scale)
        return RecurrentPreprocessingState(base=base, request=req, feature_schema=features,
            centers=tuple(centers), scales=tuple(scales), counts=tuple(counts),
            fit_rows_digest=identity(evidence), fit_row_count=len(evidence))
    except Exception:
        pass
    _reject_declaration()


class RecurrentOutput(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    model_family: Literal['lstm', 'gru']
    state: RecurrentPreprocessingState
    representation: RecurrentRepresentation
    periods: tuple[tuple[RepresentationTimestamp, ...], ...]
    values: tuple[tuple[tuple[Any, ...], ...], ...]

    @model_validator(mode='after')
    def actual(self):
        state, r = self.state, self.representation; base = state.base; req = state.request
        if (self.model_family != req.model_family or r.binding.scaler != state.scaler_reference
                or r.binding.plan != base.plan or r.binding.fold_id != base.fold_id
                or r.binding.canonical_state != base.canonical_state or r.binding.canonical_schema != base.canonical_schema
                or r.binding.historical_revision != base.dataset.manifest_digest
                or r.window != req.window or r.tensor_dtype != req.tensor_dtype or r.feature_schema != state.feature_schema
                or len(self.values) != r.shape[0] or len(self.periods) != r.shape[0]):
            raise ValueError('original recurrent execution semantics required')
        engine = CanonicalFeaturePipeline(base.canonical_schema.configuration)
        for grid, periods, steps, missing, releases in zip(r.samples,self.periods,self.values,r.masks.missing,r.available_at):
            point = grid[0]
            if (point.forecast_origin not in req.origins or point.organization_id != base.dataset.reference.organization_id
                    or periods != tuple(engine._advance(pd.Timestamp(point.forecast_origin),off) for off in req.window.past_offsets)
                    or len(steps) != req.window.window): raise ValueError('exact sample positions required')
            if releases != tuple(None if all(mask) else at for mask,at in zip(missing,periods)):
                raise ValueError('exact canonical feature release positions required')
            for row, mask in zip(steps,missing):
                if len(row) != len(req.feature_names): raise ValueError('feature width mismatch')
                for value, bit in zip(row,mask):
                    _number(value,req.tensor_dtype)
                    if type(value) is not float or bit and _bits(value) != _bits(req.window.fill_value):
                        raise ValueError('explicit finite values and masked fill required')
        return self

    @property
    def output_id(self):
        return identity(self.model_dump(mode='json',warnings='error'))

    @property
    def tensor(self):
        return np.array(self.values,dtype=self.representation.tensor_dtype)


class FrozenRecurrentPreprocessor:
    """Pure load-only kernel. The owning service authenticates feature buffers."""
    def __init__(self,state):
        try:
            self.state=RecurrentPreprocessingState.model_validate_json(state.canonical_json)
            return
        except Exception:
            pass
        _reject_declaration()

    def transform(self,batches):
        try:
            state=self.state; base=state.base; req=state.request
            cells=len(base.dataset.series_ids)*len(req.origins)*req.window.window*len(req.feature_names)
            if cells>min(base.plan.budgets.max_output_cells,100000):raise ValueError('aggregate sequence budget exceeded')
            rows=_pool(base,batches); engine=CanonicalFeaturePipeline(base.canonical_schema.configuration)
            positions=tuple(tuple(f.name for f in base.feature_schema.features).index(n) for n in req.feature_names)
            fold=next(f for f in base.plan.folds if f.fold_id==base.fold_id)
            groups={'training':[], 'validation':[], 'test':[]}
            for series in base.dataset.series_ids:
                for origin in req.origins:
                    key=(base.dataset.reference.organization_id,series,origin)
                    # Target/grid metadata must be originally prepared; no label access.
                    point=rows[key][0]
                    partition=next(name for name in groups if origin in getattr(fold,name+'_origins'))
                    slots=[]; missing=[]; releases=[]; absent=[]; evidence=[]
                    periods=tuple(engine._advance(pd.Timestamp(origin),off) for off in req.window.past_offsets)
                    for at in periods:
                        item=rows.get((point.organization_id,series,at))
                        absent.append(item is None)
                        vector=[]; mask=[]
                        if item is not None:
                            old,raw,bits,release,oid=item
                            if (release!=at or release>origin or old.dimensions!=point.dimensions):
                                raise ValueError('unavailable or incompatible historical feature')
                            evidence.append([at.isoformat(),oid])
                        for j,p in enumerate(positions):
                            bit=item is None or bits[p]
                            if bit: value=req.window.fill_value
                            else:
                                value=_number(raw[p])
                                if req.scaling!='identity_v1' and state.feature_schema.features[j].categorical_policy=='none':
                                    value=(value-state.centers[j])/state.scales[j]
                                value=_number(value,req.tensor_dtype)
                            if bit and item is not None and req.missing!='mask': raise ValueError('missing feature rejected')
                            vector.append(value);mask.append(bit)
                        slots.append(tuple(vector));missing.append(tuple(mask))
                        releases.append(None if all(mask) else at)
                    real=[i for i,v in enumerate(absent) if not v]
                    if not real: raise ValueError('all-padded sequence is unsupported')
                    # Padding never shifts observations away from their declared offsets.
                    pads=tuple((i<real[0] if req.window.padding=='left' else i>real[-1]) for i in range(len(absent)))
                    if any(a and not p for a,p in zip(absent,pads)) and req.missing!='mask':
                        raise ValueError('interior or opposite-side gaps require explicit masks')
                    length=len(pads)-sum(pads)
                    groups[partition].append((point,tuple(periods),tuple(slots),tuple(missing),tuple(releases),pads,length,evidence))
            outputs=[]; total=0
            size=min(32,65536//(req.window.window*len(positions)))
            for partition,examples in groups.items():
                for start in range(0,len(examples),size):
                    chunk=examples[start:start+size]
                    representation=RecurrentRepresentation(binding=dict(plan=base.plan,fold_id=base.fold_id,partition=partition,
                        canonical_schema=base.canonical_schema,canonical_state=base.canonical_state,
                        historical_revision=base.dataset.manifest_digest,historical_evidence_digest=identity([e[7] for e in chunk]),
                        scaler=state.scaler_reference),feature_schema=state.feature_schema,tensor_dtype=req.tensor_dtype,
                        window=req.window,samples=tuple((e[0],) for e in chunk),shape=(len(chunk),req.window.window,len(positions)),
                        masks=dict(lengths=tuple(e[6] for e in chunk),padding=tuple(e[5] for e in chunk),missing=tuple(e[3] for e in chunk)),
                        available_at=tuple(e[4] for e in chunk))
                    output=RecurrentOutput(model_family=req.model_family,state=state,representation=representation,
                        periods=tuple(e[1] for e in chunk),values=tuple(e[2] for e in chunk))
                    encoded_size=len(output.canonical_bytes)
                    if encoded_size>1024*1024:raise ValueError('sequence wire budget exceeded')
                    total+=encoded_size
                    if total>min(base.plan.budgets.max_assembly_bytes,32*1024*1024):raise ValueError('sequence output budget exceeded')
                    outputs.append(output)
            return tuple(outputs)
        except Exception:
            pass
        _reject_declaration()
