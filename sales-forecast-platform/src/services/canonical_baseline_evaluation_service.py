"""Operator-owned B1/B2a composition with fixed optional baseline profiles.

Application bootstrap is trusted to select authentic independent authorities and
protect provider/configuration credentials. These Python declarations, private
fields and callable checks do NOT authenticate an operator or sandbox Python.
Ordinary evaluate() requests cannot supply dependencies or select an authority.
"""
from dataclasses import dataclass
from time import monotonic
from threading import Lock
from typing import Callable

from src.data.contracts.backtesting import ForecastEvaluationPlan, PredictorCapabilities, RolloutBudgets
from src.data.contracts.dataset import StagingBudgets, _opaque, operational_json
from src.data.contracts.features import FeatureConfiguration
from src.data.contracts.forecast_evaluation import BaselineDefinition, ForecastScoringPlan, EvaluationBudgets
from src.data.contracts.temporal_preparation import TemporalDatasetBinding
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline, FeatureBudgets
from src.features.forecast_baselines import build_baseline_profile
from src.providers.canonical_training_data_provider import CanonicalTrainingDataProvider
from src.services.canonical_forecast_evaluation_service import CanonicalForecastEvaluationService
from src.services.canonical_training_preparation_service import (
    CanonicalTrainingPreparationService, ValueAttestedAvailabilityVerifier)
from src.services.deterministic_forecast_rollout_service import (
    DeterministicForecastRolloutService, numeric_policy_id, representation_schema_id)


class BaselineCompositionError(ValueError):
    """Sanitized composition/request failure; no partially prepared result."""


@dataclass(frozen=True)
class BaselineAuthorityBinding:
    """BOOTSTRAP configuration, never a request credential or authority receipt.

    verify must consult independently trusted release/value/revision evidence.
    IDs/versions are operator audit references, not evidence of authenticity.
    Scope is an exact operator-approved publication/series/policy binding.
    """
    authority_id: str
    authority_version: str
    protocol: str
    datasets: tuple[TemporalDatasetBinding, ...]
    verify: Callable
    purpose: str = 'offline_forecast_evaluation'
    binding_version: str = '1.0'


def _declaration(value, contract):
    if type(value) is not contract or set(vars(value)) - set(contract.model_fields):
        raise ValueError('exact supported declaration required')
    return contract.model_validate(value.model_dump())


def _authority(binding, protocol):
    if (type(binding) is not BaselineAuthorityBinding
            or binding.binding_version != '1.0' or binding.protocol != protocol
            or binding.purpose != 'offline_forecast_evaluation'
            or not callable(binding.verify) or type(binding.datasets) is not tuple
            or not 1 <= len(binding.datasets) <= 8):
        raise ValueError('incompatible operator authority configuration')
    _opaque(binding.authority_id)
    _opaque(binding.authority_version)
    scopes = []
    for declaration in binding.datasets:
        if type(declaration) is not TemporalDatasetBinding:
            raise ValueError('explicit operator publication scope required')
        # Snapshot nested declarations; later caller aliases cannot change scope.
        scopes.append(_declaration(declaration, TemporalDatasetBinding).model_dump_json())
    organizations = tuple(TemporalDatasetBinding.model_validate_json(s).reference.organization_id for s in scopes)
    if tuple(sorted(set(organizations), key=str)) != organizations:
        raise ValueError('unique ordered operator scopes required')
    return tuple(scopes), binding.verify


class CanonicalBaselineEvaluationService:
    """Construct at trusted application startup, expose only evaluate to callers.

    Bootstrap chooses the provider, source authorities, approvals, fixed producer,
    capabilities, representation and resource dependencies. Missing/mismatched
    authority configuration fails before any capture or predictor invocation.
    No global registry, request-controlled locator, public registration method,
    or preconstructed evaluator/rollout is accepted. An intentionally lying
    bootstrap verifier violates the trust assumption; Python cannot detect it.

    The original fixed synthetic producer API is unchanged. Optional baseline
    mode builds a bounded immutable selection of the two trusted lag adapters;
    requests never register callbacks or select authorities. No model fitting.
    """
    def __init__(self, provider, *, release_authority, value_authority, label_revision_authority,
                 configuration, capabilities=None, predictor=None, authorize_rollout,
                 authorize_evaluation, authorize_label_use, authorize_external_forecast=None,
                 read_budgets=None, feature_budgets=None, rollout_budgets=None,
                 evaluation_budgets=None, elapsed_clock=monotonic, baseline_definitions=()):
        try:
            self.__baseline_profiles = ()
            if type(baseline_definitions) is not tuple or len(baseline_definitions) > 8:
                raise ValueError('bounded operator baseline declarations required')
            if baseline_definitions:
                if capabilities is not None or predictor is not None:
                    raise ValueError('baseline callbacks are selected only by the trusted factory')
                profiles = []
                for declaration in baseline_definitions:
                    definition = _declaration(declaration, BaselineDefinition)
                    profile = build_baseline_profile(definition, configuration, feature_budgets=feature_budgets)
                    owned = CanonicalBaselineEvaluationService(provider,
                        release_authority=release_authority, value_authority=value_authority,
                        label_revision_authority=label_revision_authority, configuration=profile.configuration,
                        capabilities=profile.capabilities, predictor=profile, authorize_rollout=authorize_rollout,
                        authorize_evaluation=authorize_evaluation, authorize_label_use=authorize_label_use,
                        authorize_external_forecast=authorize_external_forecast, read_budgets=read_budgets,
                        feature_budgets=feature_budgets, rollout_budgets=rollout_budgets,
                        evaluation_budgets=evaluation_budgets, elapsed_clock=elapsed_clock)
                    profiles.append((definition.model_dump_json(), profile.configuration_json, owned))
                if len({p[0] for p in profiles}) != len(profiles):
                    raise ValueError('duplicate operator baseline declaration')
                self.__baseline_profiles = tuple(profiles)
                self.__baseline_lock = Lock()
                return
            if type(provider) is not CanonicalTrainingDataProvider:
                raise ValueError('operator canonical provider required')
            scopes, release = _authority(release_authority, 'release_availability_v1')
            value_scopes, values = _authority(value_authority, 'captured_history_value_v1')
            label_scopes, labels = _authority(label_revision_authority, 'captured_value_as_of_E_v1')
            if scopes != value_scopes or scopes != label_scopes:
                raise ValueError('contradictory operator authority scopes')
            if not all(callable(c) for c in (predictor, authorize_rollout, authorize_evaluation,
                                             authorize_label_use, elapsed_clock)):
                raise ValueError('complete operator execution dependencies required')
            if authorize_external_forecast is not None and not callable(authorize_external_forecast):
                raise ValueError('invalid external authority')
            if type(configuration) is not FeatureConfiguration or type(capabilities) is not PredictorCapabilities:
                raise ValueError('operator feature and producer declarations required')
            cfg = _declaration(configuration, FeatureConfiguration)
            cap = _declaration(capabilities, PredictorCapabilities)
            fb = feature_budgets if feature_budgets is not None else FeatureBudgets()
            rb = read_budgets if read_budgets is not None else StagingBudgets()
            if type(fb) is not FeatureBudgets or type(rb) is not StagingBudgets:
                raise ValueError('explicit execution budgets required')
            fb = FeatureBudgets(**vars(fb))
            rb = StagingBudgets(**vars(rb))
            engine = CanonicalFeaturePipeline(cfg, budgets=fb)
            if (cap.strategy != 'recursive_one_step' or cap.representation_kind != 'tabular_step'
                    or cap.implementation_kind != 'synthetic'
                    or cap.representation_schema_id != representation_schema_id(cfg)
                    or cap.numeric_policy_id != numeric_policy_id(cfg)
                    or cap.allowed_covariates != tuple(sorted(r.field for r in engine.rules.exogenous))
                    or cap.lookback < max((engine.temporal.minimum_history,) + cfg.lag_offsets + cfg.rolling_windows)
                    or (any(r.availability == 'external_forecast' for r in engine.rules.exogenous)
                        and authorize_external_forecast is None)):
                raise ValueError('incompatible operator producer configuration')

            self.__scopes = scopes
            self.__configuration = operational_json(cfg.model_dump(mode='json'))
            self.__capabilities = operational_json(cap.model_dump(mode='json'))
            self.__read_budgets = rb
            self.__feature_budgets = fb
            self.__rollout_limits = _declaration(rollout_budgets if rollout_budgets is not None
                else RolloutBudgets(), RolloutBudgets).model_dump()
            self.__evaluation_limits = _declaration(evaluation_budgets if evaluation_budgets is not None
                else EvaluationBudgets(), EvaluationBudgets).model_dump()
            self.__authority_references = tuple((a.authority_id, a.authority_version, a.protocol,
                a.purpose, a.binding_version) for a in (release_authority, value_authority, label_revision_authority))

            def require_scope(context):
                metadata = context.metadata
                if metadata is None or metadata.purpose != 'offline_forecast_evaluation':
                    raise ValueError('operator publication scope required')
                actual = CanonicalTrainingPreparationService._temporal_binding(metadata, context.series_ids)
                if actual.model_dump_json() not in scopes:
                    raise ValueError('operator publication scope mismatch')

            def release_check(context, claims):
                require_scope(context)
                return release(context, claims)

            def value_check(context, claims):
                require_scope(context)
                return values(context, claims)

            def label_check(context, claims):
                require_scope(context)
                return labels(context, claims)

            strong = ValueAttestedAvailabilityVerifier(
                verify_availability=release_check, verify_captured_values=value_check)
            # Own real B1/B2a instances. No externally supplied producer/evaluator
            # or raw event import; B2a retains its actual owned-rollout lifecycle.
            rollout = DeterministicForecastRolloutService(provider, predictor=predictor,
                authorize_rollout=authorize_rollout, verify_availability=strong,
                authorize_external_forecast=authorize_external_forecast, elapsed_clock=elapsed_clock)
            self.__evaluation = CanonicalForecastEvaluationService(rollout,
                authorize_evaluation=authorize_evaluation, authorize_label_use=authorize_label_use,
                verify_label_revision=label_check, elapsed_clock=elapsed_clock)
        except Exception:
            raise BaselineCompositionError('invalid operator baseline composition') from None

    def evaluate(self, scoring_plan, producer_plan, configuration, *, cancelled=lambda: False):
        """Untrusted declarations only; no authority/provider/service overrides.

        Fixed operator configuration is compared after full contract validation,
        before capture. Delegation returns directly: no new callbacks or result
        transformations follow B2a's terminal scoped checks and checkpoint.
        """
        try:
            if self.__baseline_profiles:
                raise ValueError('baseline mode requires an approved baseline declaration')
            if (type(scoring_plan) is not ForecastScoringPlan
                    or type(producer_plan) is not ForecastEvaluationPlan
                    or type(configuration) is not FeatureConfiguration or not callable(cancelled)):
                raise ValueError('validated evaluation declarations required')
            scoring = _declaration(scoring_plan, ForecastScoringPlan)
            producer = _declaration(producer_plan, ForecastEvaluationPlan)
            cfg = _declaration(configuration, FeatureConfiguration)
            if (tuple(d.model_dump_json() for d in producer.datasets) != self.__scopes
                    or tuple(d.model_dump_json() for d in scoring.datasets) != self.__scopes
                    or operational_json(cfg.model_dump(mode='json')) != self.__configuration
                    or operational_json(producer.predictor.model_dump(mode='json')) != self.__capabilities):
                raise ValueError('request differs from operator composition')
            if (any(v > self.__rollout_limits[k] for k, v in producer.budgets.model_dump().items())
                    or any(v > self.__evaluation_limits[k] for k, v in scoring.budgets.model_dump().items())):
                raise ValueError('request exceeds operator resource limits')
            return self.__evaluation.evaluate(scoring, producer, cfg,
                read_budgets=self.__read_budgets, feature_budgets=self.__feature_budgets, cancelled=cancelled)
        except Exception:
            raise BaselineCompositionError('canonical baseline-ready evaluation failed') from None

    def evaluate_baseline(self, baseline, scoring_plan, producer_plan, *, cancelled=lambda: False):
        """Select an operator-approved declaration; no request configuration/callback.

        One active baseline call per composition; no waiting queue or shared
        cross-request producer state. Cleanup after delegate return invokes no
        verifier or other revocation-capable callback.
        """
        acquired = False
        try:
            declaration = _declaration(baseline, BaselineDefinition).model_dump_json()
            selected = next((p for p in self.__baseline_profiles if p[0] == declaration), None)
            if selected is None or not callable(cancelled):
                raise ValueError('baseline is not operator approved')
            acquired = self.__baseline_lock.acquire(blocking=False)
            if not acquired:
                raise ValueError('baseline evaluation already active')
            _, configuration_json, owned = selected
            return owned.evaluate(scoring_plan, producer_plan,
                FeatureConfiguration.model_validate_json(configuration_json), cancelled=cancelled)
        except Exception:
            raise BaselineCompositionError('canonical baseline evaluation failed') from None
        finally:
            if acquired:
                self.__baseline_lock.release()
