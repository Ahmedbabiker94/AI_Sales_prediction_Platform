"""Fixed deterministic period-lag adapters. Construction is not authorization.

Only trusted C0 bootstrap selects these adapters. B1 owns history, real fold
state, prefix compatibility and feedback; B2a alone obtains/scores held-out
labels. No reader, state fitting, calendar engine or scorer is implemented here.
"""
from dataclasses import dataclass

from src.data.contracts.backtesting import (
    PredictionRequest, PredictionBatch, PredictionPoint, PredictorCapabilities, identity, point)
from src.data.contracts.features import FeatureConfiguration
from src.data.contracts.forecast_evaluation import BaselineDefinition
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline, FeatureBudgets
from src.services.deterministic_forecast_rollout_service import numeric_policy_id, representation_schema_id


LAG_FEATURE = 'baseline_period_lag'


class BaselineConfigurationError(ValueError):
    """Fixed diagnostics, never rows or trusted-callback exception details."""


def _validated(value, contract):
    if type(value) is not contract or set(vars(value)) - set(contract.model_fields):
        raise ValueError('exact supported declaration required')
    return contract.model_validate(value.model_dump())


@dataclass(frozen=True)
class BaselineProfile:
    """Stateless bootstrap artifact, not an authenticated fitted-model receipt.

    Serialized immutable declarations yield fresh copies. The callback validates
    the operator-generated representation, never a column-name-only convention.
    Calling it directly grants no capture, publication or scoring permission.
    """
    definition_json: str
    configuration_json: str
    capabilities_json: str

    @property
    def definition(self):
        return BaselineDefinition.model_validate_json(self.definition_json)

    @property
    def configuration(self):
        return FeatureConfiguration.model_validate_json(self.configuration_json)

    @property
    def capabilities(self):
        return PredictorCapabilities.model_validate_json(self.capabilities_json)

    def __call__(self, request):
        try:
            req = _validated(request, PredictionRequest)
            cfg = self.configuration
            cap = self.capabilities
            definition = self.definition
            engine = CanonicalFeaturePipeline(cfg)
            compatibility = req.compatibility
            if (req.capabilities != cap or compatibility.configuration_digest != engine.digest
                    or compatibility.strategy != 'recursive_one_step'
                    or compatibility.total_horizon > cap.supported_horizon
                    or req.feature_names != (LAG_FEATURE,) or req.feature_dtypes != ('float64',)
                    or cap.predictor_id != 'baseline-' + definition.digest or cap.predictor_version != definition.version
                    or (cap.lookback, cap.supported_horizon) != (definition.lookback, definition.supported_horizon)
                    or cap.representation_schema_id != representation_schema_id(cfg)
                    or cap.numeric_policy_id != numeric_policy_id(cfg)
                    or cfg.lag_offsets != (definition.period_lag,) or len(cfg.calculations) != 1
                    or cfg.calculations[0].operation != 'lag'
                    or dict(cfg.calculations[0].parameters) != {'offset': definition.period_lag}
                    or engine.temporal.lag_basis != 'period' or engine.temporal.gap_policy != 'reject'):
                raise ValueError()
            origin = engine._time(compatibility.forecast_origin)
            engine._aligned(origin)
            target = engine._advance(origin, req.prefix_length - 1)
            end = engine._advance(origin, req.prefix_length)
            for scope in req.scopes:
                if ((scope.metric, scope.unit, scope.currency, scope.frequency, scope.timezone)
                        != (cfg.target_metric, engine.rules.measurement.unit, engine.rules.measurement.currency,
                            engine.temporal.frequency.value, engine.temporal.timezone)
                        or engine._time(scope.target_period) != target or engine._time(scope.target_period_end) != end
                        or tuple(k for k, _ in scope.dimensions) != tuple(sorted(engine.rules.dimension_fields))):
                    raise ValueError()
            # B1 already verified the actual complete prefix and fitted state.
            # No shifted origin, state fabrication, history lookup or feedback
            # mutation occurs in this one-step representation adapter.
            return PredictionBatch(request_id=req.request_id, points=tuple(
                PredictionPoint(scope=scope, value=point(row[0])) for scope, row in zip(req.scopes, req.rows)))
        except Exception:
            raise BaselineConfigurationError('baseline representation is incompatible') from None


def build_baseline_profile(definition, template_configuration, *, feature_budgets=None):
    """Trusted factory: fixed dispatch over two versioned period-lag algorithms.

    The template supplies approved measurement, dimensions, calendar and target
    conversion. Baselines intentionally have one target-lag output and no unused
    category/covariate outputs. Time-varying covariate templates reject; static
    dimensions remain verified by the existing provider. Start anchors only.
    Neither this factory nor its declarations establish authority authenticity.
    """
    try:
        definition = _validated(definition, BaselineDefinition)
        template = _validated(template_configuration, FeatureConfiguration)
        budgets = feature_budgets if feature_budgets is not None else FeatureBudgets()
        if type(budgets) is not FeatureBudgets:
            raise ValueError()
        original = CanonicalFeaturePipeline(template, budgets=budgets)
        rules = original.rules
        if (rules.exogenous or any(c.field not in rules.dimension_fields for c in rules.categories)
                or original.temporal.lag_basis != 'period' or original.temporal.gap_policy != 'reject'
                or original.temporal.cold_start != 'reject' or original.temporal.period_anchor != 'start'
                or definition.lookback > budgets.max_history_periods
                or definition.supported_horizon > budgets.max_horizon):
            raise ValueError()
        execution = rules.model_dump()
        execution.update(categories=(), exogenous=())
        execution['temporal']['minimum_history'] = definition.lookback
        cfg = FeatureConfiguration(
            configuration_id='baseline-' + identity([definition.digest, original.digest]), version='2.0',
            target_metric=template.target_metric,
            canonical_inputs=('value', 'period_start', 'period_end') + rules.dimension_fields,
            calculations=(dict(output=LAG_FEATURE, operation='lag', inputs=('value',),
                               parameters={'offset': definition.period_lag}),),
            history_order='oldest_first', lag_offsets=(definition.period_lag,), rolling_windows=(),
            missing_value_behavior={LAG_FEATURE: 'reject'}, category_encoding={},
            ordered_outputs=(LAG_FEATURE,), output_dtypes={LAG_FEATURE: 'float64'}, execution=execution)
        CanonicalFeaturePipeline(cfg, budgets=budgets)
        cap = PredictorCapabilities(
            predictor_id='baseline-' + definition.digest, predictor_version=definition.version,
            strategy='recursive_one_step', representation_kind='tabular_step',
            representation_schema_id=representation_schema_id(cfg), numeric_policy_id=numeric_policy_id(cfg),
            lookback=definition.lookback, supported_horizon=definition.supported_horizon, allowed_covariates=())
        return BaselineProfile(definition.model_dump_json(), cfg.model_dump_json(), cap.model_dump_json())
    except Exception:
        raise BaselineConfigurationError('unsupported baseline configuration') from None
