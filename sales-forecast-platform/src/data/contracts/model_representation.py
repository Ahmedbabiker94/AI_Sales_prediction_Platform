"""Versioned, framework-independent input declarations; no preprocessing or fitting.

References, masks and availability claims are structural metadata, not attested
values or authority. Future providers/services must verify them against owned
buffers, releases and original fitted state. Existing v1 identities are untouched.
"""
import json
import math
import struct
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BeforeValidator, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

from .backtesting import Digest, ForecastScope, identity
from .dataset import operational_json
from .features import FeatureState
from .model_evaluation import _Declaration, _declaration_identity
from .temporal_preparation import TemporalPreparationPlan, _exact_timestamp
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
from src.features.feature_schema_compatibility import FeatureSchemaCompatibility


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON object key')
        result[key] = value
    return result


def _reject_json_constant(_):
    raise ValueError('invalid JSON numeric constant')


def _strict_json(value):
    """One bounded UTF-8 preflight, including objects inside nested arrays.

    Keep the original text for Pydantic's existing JSON type/number semantics;
    never round-trip through a Python dictionary before typed validation.
    """
    try:
        if type(value) not in (str, bytes, bytearray):
            raise ValueError()
        text = value if type(value) is str else value.decode('utf-8', errors='strict')
        if len(text.encode('utf-8')) > 1024 * 1024:
            raise ValueError()
        decoded = json.loads(text, object_pairs_hook=_unique_json_object,
                             parse_constant=_reject_json_constant)
        return text, decoded
    except Exception:
        pass
    from .model_evaluation import _reject_declaration
    _reject_declaration()


def _timestamp(value):
    # Reuse canonical ISO/precision admission, then explicitly decode the wire
    # timestamp so strict Python and JSON validation agree (no epoch coercion).
    value = _exact_timestamp(value)
    if type(value) is str:
        value = datetime.fromisoformat(value)
    elif type(value) is not datetime:
        value = value.to_pydatetime()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('aware timestamp required')
    return value


RepresentationTimestamp = Annotated[datetime, BeforeValidator(_timestamp)]


class RepresentationContract(_Declaration):
    """Reuse the accepted guarded declaration ingress; strict new scalar fields."""
    model_config = ConfigDict(frozen=True, strict=True, validate_default=True, revalidate_instances='always', hide_input_in_errors=True)

    @property
    def canonical_json(self):
        try:
            encoded = operational_json(self.model_dump(mode='json', warnings='error'))
            encoded.encode('utf-8')
            return encoded
        except Exception:
            pass
        from .model_evaluation import _reject_declaration
        _reject_declaration(identity=True)

    @property
    def canonical_bytes(self):
        return self.canonical_json.encode('utf-8')

    @classmethod
    def model_validate(cls, *args, **kwargs):
        cls._no_validation_override(kwargs)
        return super().model_validate(*args, **kwargs)

    @classmethod
    def model_validate_json(cls, json_data, *args, **kwargs):
        cls._no_validation_override(kwargs)
        text, _ = _strict_json(json_data)
        return super().model_validate_json(text, *args, **kwargs)

    @classmethod
    def model_validate_strings(cls, *args, **kwargs):
        cls._no_validation_override(kwargs)
        return super().model_validate_strings(*args, **kwargs)

    @classmethod
    def _no_validation_override(cls, options):
        # Keep new strict fields and existing foundation wire rules together;
        # callers cannot override the declared validation policy globally.
        if options.get('strict') is not None:
            from .model_evaluation import _reject_declaration
            _reject_declaration()


class RepresentationPoint(RepresentationContract, ForecastScope):
    forecast_origin: RepresentationTimestamp
    target_period: RepresentationTimestamp
    target_period_end: RepresentationTimestamp


class InputFeature(RepresentationContract):
    name: StrictStr = Field(min_length=1, max_length=128)
    dtype: Literal['float32', 'float64', 'int8', 'int16', 'int32', 'int64', 'bool']
    numeric_policy: Literal['strict_exact_float32_v1', 'strict_exact_float64_v1',
                            'strict_integer_v1', 'strict_boolean_v1']
    categorical_policy: Literal['none', 'frozen_vocabulary_v1'] = 'none'
    vocabulary_field: StrictStr | None = None
    missing: Literal['reject', 'mask'] = 'reject'

    @model_validator(mode='after')
    def policies(self):
        expected = ('strict_exact_' + self.dtype + '_v1' if self.dtype.startswith('float')
                    else 'strict_boolean_v1' if self.dtype == 'bool' else 'strict_integer_v1')
        if self.numeric_policy != expected or not self.name.strip():
            raise ValueError('incompatible dtype policy')
        if ((self.categorical_policy == 'none') != (self.vocabulary_field is None)
                or self.categorical_policy != 'none' and self.dtype == 'bool'):
            raise ValueError('explicit frozen vocabulary required')
        return self


class OrderedFeatures(RepresentationContract):
    features: tuple[InputFeature, ...] = Field(max_length=64)

    @model_validator(mode='after')
    def unique(self):
        if len({f.name for f in self.features}) != len(self.features):
            raise ValueError('unique ordered features required')
        return self

    @property
    def schema_id(self):
        return _declaration_identity(self)


class ScalerStateReference(RepresentationContract):
    """Opaque train-only identity, not learned parameters or proof of fitting."""
    version: Literal['1.0'] = '1.0'
    policy: Literal['identity_v1', 'standard_v1', 'minmax_v1']
    state_digest: Digest
    fit_partition: Literal['training'] = 'training'
    training_plan_digest: Digest
    fold_id: StrictStr = Field(min_length=1, max_length=128)
    training_origins_digest: Digest
    training_cutoff: RepresentationTimestamp
    canonical_state_id: Digest
    source_schema_id: Digest
    organization_ids: tuple[UUID, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode='after')
    def ordered_scope(self):
        if (not self.fold_id.strip()
                or self.organization_ids != tuple(sorted(set(self.organization_ids), key=str))):
            raise ValueError('explicit ordered scaler scope required')
        return self


class RepresentationBinding(RepresentationContract):
    """Preserves the original plan/state/schema; does not authenticate references."""
    plan: TemporalPreparationPlan
    fold_id: StrictStr = Field(min_length=1, max_length=128)
    partition: Literal['training', 'validation', 'test']
    canonical_schema: FeatureSchemaCompatibility
    canonical_state: FeatureState
    historical_revision: StrictStr = Field(min_length=1, max_length=256)
    historical_evidence_digest: Digest
    scaler: ScalerStateReference | None = None

    @model_validator(mode='after')
    def original_bindings(self):
        if not self.historical_revision.strip():
            raise ValueError('explicit historical revision required')
        schema, state, plan = self.canonical_schema, self.canonical_state, self.plan
        folds = [f for f in plan.folds if f.fold_id == self.fold_id]
        if len(folds) != 1:
            raise ValueError('unknown original fold')
        if (schema.configuration_digest != plan.configuration_digest
                or schema.configuration.configuration_id != plan.configuration_id
                or schema.configuration.version != plan.configuration_version
                or state.state_id != schema.fitted_state_id
                or state.training_schema_digest != schema.training_schema_digest
                or state.configuration_digest != schema.configuration_digest
                or state.configuration_id != plan.configuration_id
                or state.configuration_version != plan.configuration_version
                or state.executor_version != schema.executor_version
                or schema.forecast_horizon != plan.forecast_horizon):
            raise ValueError('original state/schema/plan mismatch')
        rules = schema.configuration.execution
        if rules is None or (rules.temporal.frequency.value, rules.temporal.calendar,
                rules.temporal.timezone, rules.temporal.period_anchor) != (
                plan.frequency, plan.calendar, plan.timezone, plan.period_anchor):
            raise ValueError('calendar mismatch')
        fold = folds[0]
        cutoff = state.training_cutoff
        if (cutoff.tzinfo is None or cutoff.astimezone(timezone.utc) > fold.categorical_cutoff.astimezone(timezone.utc)
                or tuple(state.organization_ids) != tuple(d.reference.organization_id for d in plan.datasets
                    if d.reference.organization_id in state.organization_ids)):
            raise ValueError('invalid canonical fit scope')
        if self.scaler is not None:
            s = self.scaler
            if (s.training_plan_digest != plan.digest or s.fold_id != self.fold_id
                    or s.training_origins_digest != identity([o.isoformat() for o in fold.training_origins])
                    or s.training_cutoff.astimezone(timezone.utc) != fold.model_fit_cutoff.astimezone(timezone.utc)
                    or s.canonical_state_id != state.state_id
                    or s.source_schema_id != schema.schema_id
                    or s.organization_ids != state.organization_ids):
                raise ValueError('scaler must bind original training partition and cutoff')
        return self


def _finite_float(value):
    if type(value) is not float or not math.isfinite(value):
        raise ValueError('finite explicit float required')
    return value


class WindowDeclaration(RepresentationContract):
    lookback: StrictInt = Field(ge=1, le=4096)
    window: StrictInt = Field(ge=1, le=512)
    stride: StrictInt = Field(ge=1, le=128)
    past_offsets: tuple[StrictInt, ...] = Field(min_length=1, max_length=512)
    padding: Literal['left', 'right']
    fill_value: Annotated[float, BeforeValidator(_finite_float)] = 0.0

    @model_validator(mode='after')
    def geometry(self):
        # Stride samples historical periods; it is not stride between examples.
        if (self.lookback != 1 + (self.window - 1) * self.stride
                or self.past_offsets != tuple(range(-self.lookback, 0, self.stride))
                or type(self.fill_value) is not float or not math.isfinite(self.fill_value)):
            raise ValueError('invalid historical window declaration')
        return self


class SequenceMasks(RepresentationContract):
    """True padding/missing means unavailable; no cell-value payloads are stored."""
    lengths: tuple[StrictInt, ...] = Field(min_length=1, max_length=32)
    padding: tuple[tuple[StrictBool, ...], ...] = Field(min_length=1, max_length=32)
    missing: tuple[tuple[tuple[StrictBool, ...], ...], ...] = Field(min_length=1, max_length=32)

    def check(self, batch, time, features, side):
        if (len(self.lengths) != batch or len(self.padding) != batch or len(self.missing) != batch
                or batch * time * max(len(features), 1) > 65536):
            raise ValueError('mask dimensions exceeded')
        for length, pads, rows in zip(self.lengths, self.padding, self.missing):
            if not 1 <= length <= time or len(pads) != time or len(rows) != time:
                raise ValueError('invalid sequence lengths')
            expected = ((True,) * (time-length) + (False,) * length if side == 'left'
                        else (False,) * length + (True,) * (time-length))
            if pads != expected:
                raise ValueError('padding and lengths disagree')
            for pad, cells in zip(pads, rows):
                if len(cells) != len(features):
                    raise ValueError('missing-mask shape mismatch')
                for bit, feature in zip(cells, features):
                    if (pad and not bit) or (not pad and bit and feature.missing == 'reject'):
                        raise ValueError('missing-mask policy mismatch')


class _Representation(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    status: Literal['DECLARATION_ONLY'] = 'DECLARATION_ONLY'
    binding: RepresentationBinding
    # One ordered horizon grid per batch example, reusing accepted point fields.
    samples: tuple[tuple[RepresentationPoint, ...], ...] = Field(min_length=1, max_length=32)
    target_alignment: Literal['origin_consecutive_periods'] = 'origin_consecutive_periods'

    @property
    def representation_id(self):
        return _declaration_identity(self)

    @property
    def compatibility_id(self):
        try:
            return self._compatibility_identity()
        except Exception:
            pass
        from .model_evaluation import _reject_declaration
        _reject_declaration(identity=True)

    def _compatibility_identity(self):
        # Batch-specific shapes, lengths, masks, releases and origins are excluded.
        # Original configuration/state/plan/fold remain pinned across partitions.
        body = self.model_dump(mode='json', warnings='error')
        keys = ('version', 'family', 'axes', 'feature_schema', 'tensor_dtype', 'window', 'past_role', 'static', 'known_future',
                'past_axes', 'static_axes', 'known_future_axes', 'future_availability')
        schema = {k: body[k] for k in keys if k in body}
        if 'future_availability' in schema:
            schema['future_availability'] = [
                {k: rule[k] for k in ('field', 'rule')} for rule in schema['future_availability']]
        schema['binding'] = {k: body['binding'][k] for k in
                             ('plan', 'fold_id', 'canonical_schema', 'canonical_state', 'scaler')}
        schema['target_alignment'] = self.target_alignment
        return identity(schema)

    def _common(self, groups):
        b = self.binding
        pipeline = CanonicalFeaturePipeline(b.canonical_schema.configuration)
        pipeline.validate_fitted_state(b.canonical_state)
        fold = next(f for f in b.plan.folds if f.fold_id == b.fold_id)
        allowed = getattr(fold, b.partition + '_origins')
        keys = []
        for grid in self.samples:
            if len(grid) != b.plan.forecast_horizon:
                raise ValueError('complete target horizon required')
            first = grid[0]
            origin = pipeline._time(first.forecast_origin)
            pipeline._aligned(origin)
            if (first.forecast_origin.astimezone(timezone.utc) not in
                    tuple(o.astimezone(timezone.utc) for o in allowed)
                    or first.organization_id not in b.canonical_state.organization_ids):
                raise ValueError('origin or organization outside original scope')
            if origin < pipeline._time(b.canonical_state.training_cutoff):
                raise ValueError('state unavailable at origin')
            dataset = next((d for d in b.plan.datasets if d.reference.organization_id == first.organization_id), None)
            measurement = b.canonical_schema.configuration.execution.measurement
            if (dataset is None or first.series_id not in dataset.series_ids
                    or first.dataset_version != dataset.reference.version_id or first.plan_digest != b.plan.digest
                    or first.fold_id != b.fold_id or (first.metric, first.unit, first.currency) !=
                       (measurement.metric, measurement.unit, measurement.currency)
                    or first.frequency != b.plan.frequency or first.timezone != b.plan.timezone):
                raise ValueError('point provenance mismatch')
            if set(dict(first.dimensions)) != set(b.canonical_schema.configuration.execution.dimension_fields):
                raise ValueError('point dimension schema mismatch')
            base = first.model_dump(exclude={'horizon_step', 'target_period', 'target_period_end'})
            for step, point in enumerate(grid):
                if (point.model_dump(exclude={'horizon_step', 'target_period', 'target_period_end'}) != base
                        or point.horizon_step != step+1
                        or pipeline._time(point.target_period) != pipeline._advance(origin, step)
                        or pipeline._time(point.target_period_end) != pipeline._advance(origin, step+1)):
                    raise ValueError('target geometry mismatch')
            keys.append((str(first.organization_id), str(first.series_id), origin.tz_convert('UTC').isoformat()))
        if keys != sorted(set(keys)):
            raise ValueError('batch must be unique in organization/series/origin order')
        names = []
        configuration = b.canonical_schema.configuration
        for group in groups:
            for feature in group.features:
                if feature.name not in configuration.ordered_outputs:
                    raise ValueError('feature absent from original canonical schema')
                source_dtype = configuration.output_dtypes[feature.name]
                if ((source_dtype == 'bool') != (feature.dtype == 'bool')
                        or feature.dtype.startswith('int') and not source_dtype.startswith('int')):
                    raise ValueError('incompatible source and representation dtype')
                if (feature.name in configuration.category_encoding) != (feature.categorical_policy != 'none'):
                    raise ValueError('canonical categorical policy required')
                if feature.categorical_policy != 'none' and (
                        feature.vocabulary_field != feature.name or
                        feature.vocabulary_field not in b.canonical_state.vocabularies
                        or feature.vocabulary_field not in configuration.category_encoding):
                    raise ValueError('unknown frozen vocabulary')
                names.append(feature.name)
        if len(set(names)) != len(names):
            raise ValueError('input groups must be disjoint')
        if len(self.model_dump_json(warnings='error').encode()) > 1024*1024:
            raise ValueError('representation declaration too large')

    def _availability(self, values, masks):
        if len(values) != len(self.samples):
            raise ValueError('availability batch mismatch')
        for grid, releases, pads, missing in zip(self.samples, values, masks.padding, masks.missing):
            if len(releases) != len(pads):
                raise ValueError('availability time mismatch')
            origin = grid[0].forecast_origin.astimezone(timezone.utc)
            for release, pad, cells in zip(releases, pads, missing):
                absent = pad or all(cells)
                if absent != (release is None):
                    raise ValueError('availability/mask mismatch')
                if release is not None and (release.tzinfo is None or release.astimezone(timezone.utc) > origin):
                    raise ValueError('feature unavailable at origin')


Releases = tuple[tuple[RepresentationTimestamp | None, ...], ...]


class TabularRepresentation(_Representation):
    family: Literal['tabular'] = 'tabular'
    axes: tuple[Literal['batch'], Literal['features']] = ('batch', 'features')
    feature_schema: OrderedFeatures
    shape: tuple[StrictInt, StrictInt]
    missing_mask: tuple[tuple[StrictBool, ...], ...]
    available_at: tuple[RepresentationTimestamp | None, ...]

    @model_validator(mode='after')
    def validate_representation(self):
        self._common((self.feature_schema,))
        features = self.feature_schema.features
        if not features or self.shape != (len(self.samples), len(features)):
            raise ValueError('tabular shape mismatch')
        mask = SequenceMasks(lengths=(1,)*len(self.samples), padding=((False,),)*len(self.samples),
                             missing=tuple((row,) for row in self.missing_mask))
        mask.check(len(self.samples), 1, features, 'left')
        self._availability(tuple((v,) for v in self.available_at), mask)
        return self


class RecurrentRepresentation(_Representation):
    family: Literal['recurrent'] = 'recurrent'
    axes: tuple[Literal['batch'], Literal['time'], Literal['features']] = ('batch', 'time', 'features')
    feature_schema: OrderedFeatures
    tensor_dtype: Literal['float32', 'float64']
    window: WindowDeclaration
    shape: tuple[StrictInt, StrictInt, StrictInt]
    masks: SequenceMasks
    available_at: Releases

    def _tensor(self, features):
        if self.binding.scaler is None or not features:
            raise ValueError('explicit train-only scaler state required')
        if any(f.dtype != self.tensor_dtype for f in features):
            raise ValueError('homogeneous tensor dtype required')
        if self.tensor_dtype == 'float32' and struct.unpack('f', struct.pack('f', self.window.fill_value))[0] != self.window.fill_value:
            raise ValueError('fill value must be exactly representable')

    @model_validator(mode='after')
    def validate_representation(self):
        self._common((self.feature_schema,))
        features = self.feature_schema.features
        self._tensor(features)
        if self.shape != (len(self.samples), self.window.window, len(features)):
            raise ValueError('recurrent shape mismatch')
        self.masks.check(len(self.samples), self.window.window, features, self.window.padding)
        self._availability(self.available_at, self.masks)
        return self


class FutureAvailability(RepresentationContract):
    field: StrictStr = Field(min_length=1, max_length=128)
    rule: Literal['known_in_advance', 'external_forecast']
    evidence_identity: Digest


class TransformerRepresentation(RecurrentRepresentation):
    family: Literal['transformer'] = 'transformer'
    axes: tuple[Literal['batch'], Literal['time'], Literal['features']] = ('batch', 'time', 'features')
    # schema/shape/masks/available_at are the past-observed stream.
    past_role: Literal['past_observed'] = 'past_observed'
    static: OrderedFeatures
    static_axes: tuple[Literal['batch'], Literal['features']] = ('batch', 'features')
    static_shape: tuple[StrictInt, StrictInt]
    static_missing_mask: tuple[tuple[StrictBool, ...], ...]
    static_available_at: tuple[RepresentationTimestamp | None, ...]
    known_future: OrderedFeatures
    known_future_axes: tuple[Literal['batch'], Literal['time'], Literal['features']] = ('batch', 'time', 'features')
    known_future_shape: tuple[StrictInt, StrictInt, StrictInt]
    known_future_masks: SequenceMasks
    known_future_available_at: Releases
    future_offsets: tuple[StrictInt, ...]
    future_availability: tuple[FutureAvailability, ...]

    @model_validator(mode='after')
    def transformer_streams(self):
        self._common((self.feature_schema, self.static, self.known_future))
        batch, horizon = len(self.samples), self.binding.plan.forecast_horizon
        static, future = self.static.features, self.known_future.features
        if (self.static_shape != (batch, len(static))
                or self.known_future_shape != (batch, horizon, len(future))
                or self.future_offsets != tuple(range(horizon))
                or any(f.dtype != self.tensor_dtype for f in static+future)):
            raise ValueError('transformer stream shape/dtype mismatch')
        static_mask = SequenceMasks(lengths=(1,)*batch, padding=((False,),)*batch,
                                   missing=tuple((row,) for row in self.static_missing_mask))
        static_mask.check(batch, 1, static, 'left')
        self._availability(tuple((v,) for v in self.static_available_at), static_mask)
        self.known_future_masks.check(batch, horizon, future, 'left')
        if self.known_future_masks.lengths != (horizon,)*batch:
            raise ValueError('known-future target grid must be complete')
        self._availability(self.known_future_available_at, self.known_future_masks)
        if tuple(r.field for r in self.future_availability) != tuple(f.name for f in future):
            raise ValueError('ordered known-future availability rules required')
        rules = {r.field: r.availability for r in self.binding.canonical_schema.configuration.execution.exogenous}
        for rule in self.future_availability:
            if rules.get(rule.field) != rule.rule:
                raise ValueError('future availability must match canonical configuration')
        dimensions = self.binding.canonical_schema.configuration.execution.dimension_fields
        if any(f.name not in dimensions for f in static):
            raise ValueError('static fields must be declared dimensions')
        return self


def validate_representation(value):
    """Guarded public dict/JSON/model ingress; rejects extras and invalid versions.

    Digests and declarations never authorize fitting or prediction. No framework
    import, raw tensor validation, conversion, scaler execution or I/O occurs.
    """
    from .model_evaluation import _reject_declaration
    try:
        if isinstance(value, _Representation):
            value = value.model_dump_json(warnings='error')
        if type(value) in (str, bytes, bytearray):
            text, decoded = _strict_json(value)
            family = decoded.get('family')
            cls = {'tabular': TabularRepresentation, 'recurrent': RecurrentRepresentation,
                   'transformer': TransformerRepresentation}[family]
            return cls.model_validate_json(text)
        if type(value) is dict:
            cls = {'tabular': TabularRepresentation, 'recurrent': RecurrentRepresentation,
                   'transformer': TransformerRepresentation}[value.get('family')]
            return cls.model_validate(value)
    except Exception:
        pass
    _reject_declaration()


def require_compatible(expected, actual):
    """Exact compatibility, excluding batch data; never an authorization check."""
    from .model_evaluation import _reject_declaration
    left, right = validate_representation(expected), validate_representation(actual)
    if type(left) is not type(right) or left.compatibility_id != right.compatibility_id:
        _reject_declaration()
    return left.compatibility_id
