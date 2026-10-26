"""R7-02 data readiness declarations, never publication or model-use authority."""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BeforeValidator, Field, StrictInt, StrictStr, model_validator

from src.data.contracts.backtesting import identity
from src.data.contracts.dataset import _opaque
from src.data.contracts.model_representation import RepresentationContract, RepresentationTimestamp, _finite_float
from src.data.contracts.temporal_preparation import TemporalDatasetBinding
from src.data.contracts.observation import Observation
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline, _STRUCTURAL
from src.features.feature_schema_compatibility import FeatureSchemaCompatibility


def _schema_from_json(encoded):
    # The string prevents mutable nested legacy configuration aliases. All access
    # returns a newly validated copy, including strict nested measurement reuse.
    from src.data.contracts.model_representation import _strict_json
    text, _ = _strict_json(encoded)
    schema = FeatureSchemaCompatibility.model_validate_json(text)
    from src.data.contracts.dataset import operational_json
    if operational_json(schema.model_dump(mode='json', warnings='error')) != encoded:
        raise ValueError('canonical schema encoding required')
    return schema


def _original_value(encoded):
    """Decode only the accepted bounded pre-conversion scalar identity v1."""
    import math
    import struct
    from decimal import Decimal
    from src.data.contracts.model_representation import _strict_json
    from src.data.contracts.dataset import operational_json
    _, value = _strict_json(encoded)
    if type(value) is not list or not value: raise ValueError('invalid value identity')
    if value[0] == 'float64' and len(value) == 2 and type(value[1]) is str and len(value[1]) == 16:
        raw = struct.unpack('>d', bytes.fromhex(value[1]))[0]
        if not math.isfinite(raw): raise ValueError('nonfinite identity')
    elif value[0] == 'integer' and len(value) == 2 and type(value[1]) is str and len(value[1]) <= 512:
        raw = int(value[1])
    elif (value[0] == 'decimal' and len(value) == 4 and type(value[1]) is int and value[1] in (0, 1)
          and type(value[2]) is list and 1 <= len(value[2]) <= 256
          and all(type(d) is int and 0 <= d <= 9 for d in value[2])
          and type(value[3]) is int and abs(value[3]) <= 1000):
        raw = Decimal((value[1], tuple(value[2]), value[3]))
    else: raise ValueError('unsupported value identity')
    if value[0] == 'float64' and struct.pack('>d', raw).hex() != value[1]: raise ValueError('noncanonical float identity')
    if value[0] == 'integer' and str(raw) != value[1]: raise ValueError('noncanonical integer identity')
    if value[0] == 'decimal':
        parts = raw.as_tuple()
        if [parts.sign, list(parts.digits), parts.exponent] != value[1:]: raise ValueError('noncanonical decimal identity')
    if operational_json(value) != encoded: raise ValueError('noncanonical value identity')
    return raw


class RuntimeSnapshotBudgets(RepresentationContract):
    max_captured_rows: StrictInt = Field(default=10000, ge=1, le=10000)
    max_decoded_bytes: StrictInt = Field(default=16*1024*1024, ge=1, le=16*1024*1024)
    max_output_bytes: StrictInt = Field(default=512*1024, ge=1, le=512*1024)
    max_elapsed_seconds: StrictInt = Field(default=30, ge=1, le=60)


class RuntimeSnapshotRequest(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    dataset: TemporalDatasetBinding
    purpose: Literal['production_training', 'offline_forecast_evaluation']
    forecast_origin: RepresentationTimestamp
    capture_cutoff: RepresentationTimestamp
    forecast_horizon: StrictInt = Field(default=1, ge=1, le=1)
    feature_schema_json: StrictStr = Field(max_length=128*1024)
    history_periods: StrictInt = Field(ge=1, le=4096)
    static_fields: tuple[StrictStr, ...] = ()
    external_fields: tuple[StrictStr, ...] = ()
    availability_policy: Literal['attested_origin_eligible_v1'] = 'attested_origin_eligible_v1'
    missing_policy: Literal['reject'] = 'reject'
    budgets: RuntimeSnapshotBudgets = Field(default_factory=RuntimeSnapshotBudgets)

    @model_validator(mode='after')
    def geometry(self):
        schema = _schema_from_json(self.feature_schema_json)
        engine = CanonicalFeaturePipeline(schema.configuration)
        origin, cutoff = engine._time(self.forecast_origin), engine._time(self.capture_cutoff)
        engine._aligned(origin)
        if cutoff > origin or schema.forecast_horizon != 1:
            raise ValueError('unsupported cutoff or horizon')
        required = max((engine.temporal.minimum_history, *schema.configuration.lag_offsets,
                        *(c.parameters['window']+c.parameters['offset']-1
                          for c in schema.configuration.calculations if c.operation.startswith('rolling_'))))
        if self.history_periods < required or self.history_periods*len(self.dataset.series_ids) > 4096:
            raise ValueError('insufficient or oversized history geometry')
        if (len(set(self.static_fields)) != len(self.static_fields)
                or not set(self.static_fields) <= set(engine.rules.dimension_fields)
                or self.external_fields != tuple(r.field for r in engine.rules.exogenous)
                or any(r.missing != 'reject' for r in engine.rules.exogenous)):
            raise ValueError('unsupported static or external input scope')
        if self.history_periods*len(self.dataset.series_ids) > self.budgets.max_captured_rows:
            raise ValueError('history row budget exceeded')
        return self

    @property
    def request_id(self):
        return identity(['runtime_snapshot_request_v1', self.model_dump(mode='json', warnings='error')])


class RuntimeHistoryObservation(RepresentationContract, Observation):
    """Projection of canonical Observation fields plus original release evidence.

    The value is converted ONLY by the accepted configuration's numeric policy;
    original pre-conversion value identity remains bound for revision attestation.
    No engineered feature, target actual, scaler fit or padding is produced.
    """
    organization_id: UUID
    period_start: RepresentationTimestamp
    period_end: RepresentationTimestamp
    frequency: Literal['daily', 'weekly']
    value_status: Literal['actual'] = 'actual'
    value: Annotated[float, BeforeValidator(_finite_float)]
    source_record_id: StrictStr
    lineage_id: UUID
    available_at: RepresentationTimestamp
    evidence_reference: StrictStr
    original_value_identity: StrictStr

    @model_validator(mode='after')
    def released_actual(self):
        import math
        if not math.isfinite(self.value) or self.period_start >= self.period_end or self.available_at < self.period_end:
            raise ValueError('invalid historical actual')
        _opaque(self.source_record_id); _opaque(self.evidence_reference)
        if not self.original_value_identity or len(self.original_value_identity) > 8192:
            raise ValueError('bounded original value identity required')
        _original_value(self.original_value_identity)
        return self


class RuntimeStaticContext(RepresentationContract):
    series_id: UUID
    dimensions: tuple[tuple[StrictStr, StrictStr], ...]


class RuntimeCovariate(RepresentationContract):
    series_id: UUID
    field: StrictStr
    period_start: RepresentationTimestamp
    period_end: RepresentationTimestamp
    available_at: RepresentationTimestamp
    evidence_reference: StrictStr
    source_record_id: StrictStr
    lineage_id: UUID
    value: Annotated[float, BeforeValidator(_finite_float)] | StrictStr | bool

    @model_validator(mode='after')
    def bounded(self):
        import math
        if type(self.value) is float and not math.isfinite(self.value): raise ValueError('finite covariate required')
        if type(self.value) is str: _opaque(self.value)
        _opaque(self.evidence_reference); _opaque(self.source_record_id)
        if self.period_start >= self.period_end: raise ValueError('invalid covariate geometry')
        return self


class RuntimeInputSnapshot(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    classification: Literal['DATA_READINESS_ONLY'] = 'DATA_READINESS_ONLY'
    missingness: Literal['complete_no_padding_v1'] = 'complete_no_padding_v1'
    request: RuntimeSnapshotRequest
    target_period_end: RepresentationTimestamp
    history: tuple[RuntimeHistoryObservation, ...] = Field(min_length=1, max_length=4096)
    static: tuple[RuntimeStaticContext, ...]
    external: tuple[RuntimeCovariate, ...]
    historical_covariates: tuple[RuntimeCovariate, ...]
    source_dtypes: tuple[tuple[StrictStr, StrictStr], ...]
    captured_rows: StrictInt = Field(ge=1)
    captured_bytes: StrictInt = Field(ge=1)
    # Validated source physical schema; not a claim about exported/native tensors.
    source_schema_digest: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def consistent(self):
        r = self.request; schema = _schema_from_json(r.feature_schema_json)
        engine = CanonicalFeaturePipeline(schema.configuration)
        origin = engine._time(r.forecast_origin); cutoff = engine._time(r.capture_cutoff)
        if (engine._time(self.target_period_end) != engine._advance(origin, 1)
                or self.source_schema_digest != schema.training_schema_digest
                or self.captured_rows > r.budgets.max_captured_rows
                or self.captured_bytes > r.budgets.max_decoded_bytes
                or self.captured_rows < len(self.history)):
            raise ValueError('snapshot provenance or resource mismatch')
        expected = [(s, engine._advance(origin, -p)) for s in r.dataset.series_ids
                    for p in range(r.history_periods, 0, -1)]
        if [(v.series_id, engine._time(v.period_start)) for v in self.history] != expected:
            raise ValueError('complete ordered series-isolated history required')
        import struct
        for row in self.history:
            converted = engine.convert_numeric(_original_value(row.original_value_identity))
            if struct.pack('>d', converted) != struct.pack('>d', row.value):
                raise ValueError('original value and conversion policy mismatch')
            if (row.organization_id != r.dataset.reference.organization_id
                    or engine._time(row.period_end) != engine._advance(engine._time(row.period_start), 1)
                    or engine._time(row.available_at) > cutoff
                    or row.metric != schema.configuration.target_metric
                    or row.unit != engine.rules.measurement.unit or row.currency != engine.rules.measurement.currency
                    or row.frequency != engine.temporal.frequency.value):
                raise ValueError('historical scope or release mismatch')
        if tuple(v.series_id for v in self.static) != r.dataset.series_ids:
            raise ValueError('complete ordered static context required')
        from src.data.processing.canonical_mapper import _identity
        for row in self.static:
            if (tuple(k for k, _ in row.dimensions) != tuple(sorted(engine.rules.dimension_fields))
                    or _identity(r.dataset.reference.organization_id, dict(row.dimensions)) != row.series_id):
                raise ValueError('canonical series identity mismatch')
        import hashlib
        from src.data.contracts.dataset import operational_json
        source_fields = _STRUCTURAL+('available_at',)+schema.configuration.canonical_inputs
        if (tuple(k for k, _ in self.source_dtypes) != source_fields
                or hashlib.sha256(operational_json(self.source_dtypes).encode()).hexdigest() != self.source_schema_digest):
            raise ValueError('source dtype/order mismatch')
        expected_past = [(s, at, rule.field) for s, at in expected for rule in engine.rules.exogenous]
        if [(v.series_id, engine._time(v.period_start), v.field) for v in self.historical_covariates] != expected_past:
            raise ValueError('complete ordered historical covariates required')
        source_rows = {(v.series_id,engine._time(v.period_start)):v for v in self.history}
        for value in self.historical_covariates:
            row = source_rows[(value.series_id,engine._time(value.period_start))]
            if (engine._time(value.period_end) != engine._time(row.period_end)
                    or engine._time(value.available_at) > cutoff
                    or value.source_record_id != row.source_record_id or value.lineage_id != row.lineage_id):
                raise ValueError('historical covariate provenance mismatch')
        expected_exo = [(s, rule.field) for s in r.dataset.series_ids for rule in engine.rules.exogenous]
        if [(v.series_id, v.field) for v in self.external] != expected_exo:
            raise ValueError('complete ordered covariates required')
        for value in self.external:
            rule = next(x for x in engine.rules.exogenous if x.field == value.field)
            at = engine._advance(origin, -rule.offset) if rule.offset else origin
            if (engine._time(value.period_start) != at
                    or engine._time(value.period_end) != engine._advance(at, 1)
                    or engine._time(value.available_at) > cutoff
                    or rule.availability in ('observed_only', 'lagged') and engine._time(value.period_end) > origin):
                raise ValueError('covariate geometry or availability mismatch')
        if len(self.canonical_bytes) > r.budgets.max_output_bytes:
            raise ValueError('snapshot output budget exceeded')
        return self

    @property
    def snapshot_id(self):
        return identity(['runtime_input_snapshot_v1', self.model_dump(mode='json', warnings='error')])


class RuntimePreparationCompatibility(RepresentationContract):
    version: Literal['1.0'] = '1.0'
    classification: Literal['SOURCE_CONTEXT_ONLY'] = 'SOURCE_CONTEXT_ONLY'
    model_family: Literal['xgboost', 'lightgbm', 'lstm', 'gru', 'transformer']
    request_id: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')
    preparation_state_id: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')
    required_history_periods: StrictInt = Field(ge=1)
