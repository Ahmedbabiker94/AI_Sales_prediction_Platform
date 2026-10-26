"""R7-02: original canonical repository/provider capture -> data-only snapshot.

No SQL, feature calculation, fitting, prediction, model use or production wiring.
The protected host pins the provider, schema/state and independent attesters.
Publication purposes retain their original meaning; data readiness grants no new
runtime/inference purpose. Checks are sequential and budgets are cooperative.
"""
import math
from dataclasses import dataclass
from time import monotonic

import pandas as pd

from src.data.contracts.dataset import StagingBudgets, operational_json, _opaque
from src.data.contracts.features import FeatureState
from src.data.contracts.runtime_snapshot import (
    RuntimeSnapshotRequest, RuntimeInputSnapshot, RuntimeHistoryObservation,
    RuntimeStaticContext, RuntimeCovariate, _schema_from_json,
)
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline, FeatureBudgets, _STRUCTURAL
from src.features.feature_schema_compatibility import FeatureSchemaCompatibility
from src.providers.canonical_training_data_provider import CanonicalTrainingDataProvider
from src.services.canonical_training_preparation_service import (
    CanonicalTrainingPreparationService, PreparationBudgets, PreparationContext,
    AvailabilityClaim, ValueAttestedAvailabilityVerifier, historical_value_identity,
)


@dataclass(frozen=True)
class RuntimeSnapshotContext(PreparationContext):
    request_json: str
    capture_cutoff: pd.Timestamp


class RuntimeSnapshotError(ValueError):
    pass


def _invalid():
    error = RuntimeSnapshotError('canonical runtime snapshot unavailable or invalid')
    try: raise error from None
    finally: error.__cause__ = None; error.__context__ = None


def _continue(): return False


class CanonicalRuntimeSnapshotService:
    def __init__(self, provider, *, schema, fitted_state, authorize_snapshot,
                 verify_history, authorize_external_forecast=None, elapsed_clock=monotonic):
        try:
            if (type(provider) is not CanonicalTrainingDataProvider
                    or type(schema) is not FeatureSchemaCompatibility or type(fitted_state) is not FeatureState
                    or type(verify_history) is not ValueAttestedAvailabilityVerifier
                    or not all(callable(c) for c in (authorize_snapshot, elapsed_clock))
                    or authorize_external_forecast is not None and not callable(authorize_external_forecast)):
                raise ValueError()
            self._feature_schema_json = operational_json(schema.model_dump(mode='json', warnings='error'))
            checked = _schema_from_json(self._feature_schema_json)
            engine = CanonicalFeaturePipeline(checked.configuration)
            state = engine.validate_fitted_state(FeatureState.model_validate_json(fitted_state.model_dump_json(warnings='error')))
            if (checked.fitted_state_id != state.state_id or checked.training_schema_digest != state.training_schema_digest
                    or checked.executor_version != state.executor_version or checked.forecast_horizon != 1):
                raise ValueError()
            self._state_json = operational_json(state.model_dump(mode='json', warnings='error'))
            self._provider = provider; self._authorize = authorize_snapshot
            self._history = ValueAttestedAvailabilityVerifier(verify_availability=verify_history._availability,
                verify_captured_values=verify_history._values)
            self._external = authorize_external_forecast; self._clock = elapsed_clock
            return
        except Exception:
            pass
        _invalid()

    def validate_preparation_context(self, request, frozen_state, *, model_family):
        """Structural source-context compatibility only, never inference permission.

        Accepted M2/M3-M5 bindings still restrict their original folds/origins.
        No scaler/vocabulary is fitted, no feature/tensor is calculated, and this
        check does not authenticate a caller's detached fitted-state declaration.
        """
        try:
            from src.preprocessing.canonical_tabular import TabularPreprocessingState
            from src.preprocessing.canonical_recurrent import RecurrentPreprocessingState
            from src.preprocessing.canonical_transformer import TransformerPreprocessingState
            from src.data.contracts.runtime_snapshot import RuntimePreparationCompatibility
            classes = {'xgboost':TabularPreprocessingState, 'lightgbm':TabularPreprocessingState,
                'lstm':RecurrentPreprocessingState, 'gru':RecurrentPreprocessingState,
                'transformer':TransformerPreprocessingState}
            if type(model_family) is not str or model_family not in classes or type(frozen_state) is not classes[model_family]:
                raise ValueError()
            r = RuntimeSnapshotRequest.model_validate_json(request.canonical_json)
            state = classes[model_family].model_validate_json(frozen_state.canonical_json)
            base = state if model_family in ('xgboost','lightgbm') else state.encoder.base if model_family=='transformer' else state.base
            if (r.feature_schema_json != self._feature_schema_json
                    or operational_json(base.canonical_schema.model_dump(mode='json', warnings='error')) != self._feature_schema_json
                    or operational_json(base.canonical_state.model_dump(mode='json', warnings='error')) != self._state_json
                    or base.dataset.reference.organization_id != r.dataset.reference.organization_id
                    or not set(r.dataset.series_ids) <= set(base.dataset.series_ids)):
                raise ValueError()
            fold = next(f for f in base.plan.folds if f.fold_id==base.fold_id)
            if r.forecast_origin not in fold.origins: raise ValueError()
            required = max(base.canonical_schema.configuration.execution.temporal.minimum_history,
                *base.canonical_schema.configuration.lag_offsets,
                *(c.parameters['window']+c.parameters['offset']-1 for c in base.canonical_schema.configuration.calculations
                  if c.operation.startswith('rolling_')))
            if model_family not in ('xgboost','lightgbm'):
                if (r.forecast_origin not in state.request.origins
                        or model_family in ('lstm','gru') and state.request.model_family != model_family): raise ValueError()
                # Historical fields are captured as of this request's cutoff,
                # not separately revision-attested at every encoder origin.
                unavailable = {x.field for x in base.canonical_schema.configuration.execution.exogenous}
                for _ in base.canonical_schema.configuration.calculations:
                    for calculation in base.canonical_schema.configuration.calculations:
                        if set(calculation.inputs) & unavailable: unavailable.add(calculation.output)
                if set(state.request.feature_names) & unavailable: raise ValueError()
                required += state.request.window.lookback
            if model_family=='transformer':
                if (not set(state.request.static_names) <= set(r.static_fields)
                        or not set(state.request.known_future_names) <= set(r.external_fields)): raise ValueError()
            if r.history_periods < required: raise ValueError()
            return RuntimePreparationCompatibility(model_family=model_family, request_id=r.request_id,
                preparation_state_id=state.state_id, required_history_periods=required)
        except Exception:
            pass
        _invalid()

    def capture(self, request, *, cancelled=_continue):
        try:
            return self._capture(request, cancelled)
        except Exception:
            pass
        _invalid()

    def _capture(self, request, cancelled):
        if type(request) is not RuntimeSnapshotRequest or not callable(cancelled): raise ValueError()
        r = RuntimeSnapshotRequest.model_validate_json(request.canonical_json)
        if r.feature_schema_json != self._feature_schema_json: raise ValueError()
        schema = _schema_from_json(self._feature_schema_json)
        state = FeatureState.model_validate_json(self._state_json)
        if r.dataset.reference.organization_id not in state.organization_ids: raise ValueError()
        clock = self._clock; previous = None
        def now():
            nonlocal previous
            value = clock()
            if type(value) not in (int, float) or not math.isfinite(value) or previous is not None and value < previous:
                raise ValueError()
            previous = value; return value
        start = now()
        def check():
            if cancelled() is not False or now()-start >= r.budgets.max_elapsed_seconds: raise ValueError()
        def authorize(ctx, stage):
            check()
            approved = self._authorize(r, ctx, stage)
            check()
            if approved is not True: raise ValueError()
            return True
        authorize(None, 'admit')
        budgets = FeatureBudgets(max_rows=r.budgets.max_captured_rows,
            max_frame_bytes=r.budgets.max_decoded_bytes, max_horizon=1)
        engine = CanonicalFeaturePipeline(schema.configuration, budgets=budgets,
            authorize_external_forecast=self._external)
        origin = engine._time(r.forecast_origin); cutoff = engine._time(r.capture_cutoff)
        if origin < engine._time(state.training_cutoff): raise ValueError()
        def context(meta):
            if (CanonicalTrainingPreparationService._temporal_binding(meta,r.dataset.series_ids) != r.dataset
                    or meta.purpose != r.purpose): raise ValueError()
            return RuntimeSnapshotContext(meta, engine.configuration.model_dump_json(warnings='error'), engine.digest,
                r.dataset.series_ids, origin, engine._time(state.training_cutoff), 1, r.canonical_json, cutoff)
        preparation = CanonicalTrainingPreparationService(self._provider, authorize_preparation=authorize,
            verify_availability=self._history, elapsed_clock=clock)
        sizes = {'rows':0, 'bytes':0}
        def account(kind, rows, size):
            if kind == 'capture': sizes['rows'] += rows; sizes['bytes'] += size
        read_budgets = StagingBudgets(max_rows=r.budgets.max_captured_rows,
            max_elapsed_seconds=r.budgets.max_elapsed_seconds)
        metadata, ctx, frame, definitions = preparation._capture(r.dataset.reference,engine,
            r.dataset.series_ids,r.purpose,context,budgets,
            PreparationBudgets(r.budgets.max_decoded_bytes,r.budgets.max_elapsed_seconds),read_budgets,check,account)
        # Preserve accepted complete-version physical-dtype admission semantics.
        # Only now derive the bounded, origin-eligible producer-facing view.
        history = frame[(frame.period_start >= engine._advance(origin,-r.history_periods))
                        & (frame.period_end <= origin) & (frame.available_at <= cutoff)].copy()
        # Reuse original strict canonical row admission; no feature calculations.
        rows = engine._rows(history, observations=True); engine._scope(rows)
        if engine._schema(history) != schema.training_schema_digest: raise ValueError()
        rows.sort(key=lambda row:(str(row['series_id']),row['period_start']))
        claims, past_exo = preparation._availability(history,engine,check)
        # _availability also inspects historical exogenous release columns. Keep
        # those claims separate from H1 covariates selected from the SAME capture.
        external_rows = []; exo_claims = []
        for sid in r.dataset.series_ids:
            for rule in engine.rules.exogenous:
                check()
                at = engine._advance(origin,-rule.offset) if rule.offset else origin
                selected = frame[(frame.series_id == sid) & (frame.period_start == at)]
                if len(selected) != 1 or not rule.availability_field or not rule.evidence_field: raise ValueError()
                source = selected.iloc[0]
                release = engine._time(source[rule.availability_field]); evidence = source[rule.evidence_field]
                _opaque(evidence)
                if release > cutoff: raise ValueError()
                value = source[rule.field]
                external_rows.append(dict(organization_id=metadata.reference.organization_id,series_id=sid,
                    metric=engine.configuration.target_metric,field=rule.field,value=value,
                    period_start=at,period_end=engine._advance(at,1),available_at=release,
                    evidence_reference=evidence,unit=rule.measurement.unit if rule.measurement else None,
                    currency=rule.measurement.currency if rule.measurement else None,
                    source_record_id=source.source_record_id,lineage_id=source.lineage_id))
                exo_claims.append(AvailabilityClaim(sid,source.source_record_id,source.lineage_id,rule.field,
                    at,engine._advance(at,1),release,evidence))
        all_claims = claims+tuple(exo_claims)
        def verify():
            check()
            if self._history(ctx, all_claims) is not True: raise ValueError()
            check()
        verify()
        static = tuple(RuntimeStaticContext(series_id=sid,
            dimensions=tuple(sorted(definitions[sid].dimensions.items()))) for sid in r.dataset.series_ids)
        for item in static:
            for category in engine.rules.categories:
                if category.field in dict(item.dimensions):
                    engine._category(category.field,dict(item.dimensions)[category.field],state)
        history_records = tuple(RuntimeHistoryObservation(organization_id=row['organization_id'],
            series_id=row['series_id'],period_start=row['period_start'],period_end=row['period_end'],
            frequency=row['frequency'],metric=row['metric'],unit=row['unit'],currency=row['currency'],
            value=engine.convert_numeric(row['value']),source_record_id=row['source_record_id'],
            lineage_id=row['lineage_id'],available_at=row['available_at'],evidence_reference=row['availability_reference'],
            original_value_identity=operational_json(historical_value_identity(row['value']))) for row in rows)
        def scope_for(sid):
            return dict(organization_id=metadata.reference.organization_id,series_id=sid,
                metric=engine.configuration.target_metric,unit=engine.rules.measurement.unit,
                currency=engine.rules.measurement.currency,frequency=engine.temporal.frequency.value,
                **definitions[sid].dimensions)
        def external_values():
            if not external_rows: return ()
            # Preserve source scalars before accepted numeric admission/coercion.
            exo = pd.DataFrame({k:pd.Series([v[k] for v in external_rows],dtype=object if k=='value' else None)
                                for k in external_rows[0]})
            index = engine._exo_index(exo); result = []
            for item in external_rows:
                check()
                scope = scope_for(item['series_id'])
                rule = next(x for x in engine.rules.exogenous if x.field == item['field'])
                value = engine._exo(scope,origin,origin,rule,index); check()
                result.append(RuntimeCovariate(series_id=item['series_id'],field=rule.field,
                    period_start=item['period_start'],period_end=item['period_end'],available_at=item['available_at'],
                    evidence_reference=item['evidence_reference'],source_record_id=item['source_record_id'],
                    lineage_id=item['lineage_id'],value=value))
            return tuple(result)
        external = external_values()
        def historical_values():
            past = []
            if past_exo is not None:
                index = engine._exo_index(past_exo)
                candidates = {(row['series_id'], engine._time(row['period_start']), row['field']):row
                              for row in past_exo.to_dict('records')}
                for source in rows:
                    for rule in engine.rules.exogenous:
                        check()
                        item = candidates[(source['series_id'], source['period_start'], rule.field)]
                        if engine._time(item['available_at']) > cutoff: raise ValueError()
                        value = item['value']
                        if rule.availability == 'external_forecast':
                            # Historical P is authorized as of the request's O,
                            # not a fabricated earlier encoder forecast origin.
                            value = engine._exo(scope_for(source['series_id']),source['period_start'],origin,rule,index)
                            check()
                        if rule.field in engine.configuration.category_encoding:
                            value = engine._text(value); engine._category(rule.field,value,state)
                        elif rule.availability != 'external_forecast' and type(value) is not bool:
                            value = engine.convert_numeric(value,rule.field)
                        past.append(RuntimeCovariate(series_id=source['series_id'],field=rule.field,
                            period_start=source['period_start'],period_end=source['period_end'],
                            available_at=item['available_at'],evidence_reference=item['evidence_reference'],
                            source_record_id=source['source_record_id'],lineage_id=source['lineage_id'],value=value))
            return tuple(past)
        past = historical_values()
        snapshot = RuntimeInputSnapshot(request=r,target_period_end=engine._advance(origin,1),
            history=history_records,static=static,external=external,historical_covariates=past,
            source_dtypes=tuple((field,repr(history[field].dtype)) for field in
                _STRUCTURAL+('available_at',)+engine.configuration.canonical_inputs),captured_rows=sizes['rows'],
            captured_bytes=sizes['bytes'],source_schema_digest=engine._schema(history))
        self._provider.require_current(metadata,budgets=read_budgets); check()
        verify()
        if external_values() != external or historical_values() != past: raise ValueError()
        authorize(ctx,'return')
        check()
        return snapshot
