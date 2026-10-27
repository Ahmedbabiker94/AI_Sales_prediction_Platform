"""Small allowlist. Only M3 XGBoost execution is implemented; no native loaders."""
from types import MappingProxyType
import struct
import pandas as pd
from src.data.contracts.model_candidate import strict_float32
from src.data.contracts.model_representation import TabularRepresentation
from src.data.contracts.runtime_snapshot import _original_value
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
from src.preprocessing.canonical_tabular import FrozenTabularPreprocessor, TabularPreprocessingState


class AdapterUnavailable(ValueError): pass


class M3XGBoostInputAdapter:
    state_type = TabularPreprocessingState

    def dataset(self, state):
        return state.dataset

    def supported(self, state):
        cfg = state.canonical_schema.configuration
        # M3's unchanged kernel has no injected external-forecast authority.
        # Never turn R7-02 publication access into that missing execution policy.
        return (cfg.execution.numeric_conversion == 'lossless_float64'
                and all(r.availability != 'external_forecast' for r in cfg.execution.exogenous))

    def validate_context(self, request, state):
        fold = next(f for f in state.plan.folds if f.fold_id == state.fold_id)
        if (request.dataset != state.dataset or request.forecast_origin not in fold.validation_origins+fold.test_origins
                or request.forecast_origin < fold.model_fit_cutoff):
            raise ValueError()

    def transform(self, snapshot, state, metadata):
        cfg = state.canonical_schema.configuration
        r = snapshot.request
        self.validate_context(r, state)
        if not self.supported(state): raise AdapterUnavailable()
        dimensions = {s.series_id:dict(s.dimensions) for s in snapshot.static}
        past = {(v.series_id,v.period_start,v.field):v for v in snapshot.historical_covariates}
        records = []
        for row in snapshot.history:
            data = dict(organization_id=row.organization_id, series_id=row.series_id, metric=row.metric,
                unit=row.unit, currency=row.currency, frequency=row.frequency, period_start=row.period_start,
                period_end=row.period_end, value=_original_value(row.original_value_identity), available_at=row.available_at,
                **dimensions[row.series_id])
            for rule in cfg.execution.exogenous:
                data[rule.field] = past[(row.series_id,row.period_start,rule.field)].value
            records.append(data)
        frame = pd.DataFrame(records)
        # Rebuild only explicitly supported physical dtypes. No eval or arbitrary
        # dtype parser; exact round-trip checks precede canonical execution.
        simple = {"dtype('O')":object, "dtype('float64')":'float64', "dtype('float32')":'float32',
                  "dtype('int64')":'int64', "dtype('int32')":'int32', "dtype('int16')":'int16', "dtype('int8')":'int8',
                  "dtype('bool')":'bool'}
        for name, encoded in snapshot.source_dtypes:
            if name in ('period_start','period_end','available_at'):
                wanted = f'datetime64[ns, {state.plan.timezone}]'
                if encoded != wanted: raise AdapterUnavailable()
                frame[name] = pd.Series(pd.to_datetime(frame[name]), dtype=wanted)
            else:
                if encoded not in simple: raise AdapterUnavailable()
                before = frame[name].tolist()
                frame[name] = pd.Series(before, dtype=simple[encoded])
                for a, b in zip(before, frame[name].tolist()):
                    if type(a) is float and type(b) is float:
                        if struct.pack('>d',a) != struct.pack('>d',b): raise ValueError()
                    elif type(a) is not type(b) or a != b: raise ValueError()
            if repr(frame[name].dtype) != encoded: raise ValueError()
        engine = CanonicalFeaturePipeline(cfg)
        if engine._schema(frame) != snapshot.source_schema_digest: raise ValueError()
        requests = pd.DataFrame([dict(organization_id=r.dataset.reference.organization_id, series_id=s.series_id,
            metric=cfg.target_metric, unit=cfg.execution.measurement.unit, currency=cfg.execution.measurement.currency,
            frequency=state.plan.frequency, **dict(s.dimensions)) for s in snapshot.static])
        rules = {rule.field:rule for rule in cfg.execution.exogenous}
        exo_rows = [dict(organization_id=r.dataset.reference.organization_id,series_id=v.series_id,
            metric=cfg.target_metric,field=v.field,period_start=v.period_start,period_end=v.period_end,
            value=v.value,available_at=v.available_at,evidence_reference=v.evidence_reference,
            unit=rules[v.field].measurement.unit if rules[v.field].measurement is not None else cfg.execution.measurement.unit,
            currency=rules[v.field].measurement.currency if rules[v.field].measurement is not None else cfg.execution.measurement.currency)
            for v in snapshot.external]
        exogenous = pd.DataFrame(exo_rows) if exo_rows else None
        if exo_rows: exogenous['value'] = pd.Series([v['value'] for v in exo_rows],dtype=object)
        origin = r.forecast_origin
        fold = next(f for f in state.plan.folds if f.fold_id == state.fold_id)
        partition = next(p for p in ('training','validation','test') if origin in getattr(fold,p+'_origins'))
        samples = tuple((dict(plan_digest=state.plan.digest,dataset_version=state.dataset.reference.version_id,
            fold_id=state.fold_id,organization_id=r.dataset.reference.organization_id,series_id=s.series_id,
            metric=cfg.target_metric,unit=cfg.execution.measurement.unit,currency=cfg.execution.measurement.currency,
            frequency=state.plan.frequency,timezone=state.plan.timezone,dimensions=s.dimensions,
            forecast_origin=origin,horizon_step=1,target_period=origin,target_period_end=snapshot.target_period_end),) for s in snapshot.static)
        representation = TabularRepresentation(binding=dict(plan=state.plan,fold_id=state.fold_id,partition=partition,
            canonical_schema=state.canonical_schema,canonical_state=state.canonical_state,
            historical_revision=state.dataset.manifest_digest,historical_evidence_digest=snapshot.snapshot_id,scaler=None),
            feature_schema=state.feature_schema,samples=samples,shape=(len(samples),len(state.feature_schema.features)),
            missing_mask=tuple((False,)*len(state.feature_schema.features) for _ in samples),available_at=(origin,)*len(samples))
        output = FrozenTabularPreprocessor(state).transform(frame,requests,representation,model_family='xgboost',exogenous=exogenous)
        if output.representation.compatibility_id != metadata.representation_compatibility_id: raise ValueError()
        strict_float32(output.values)  # Exact homogeneous native ingress, no native import.
        return output


_ADAPTERS = MappingProxyType({'canonical_m3_xgboost_inputs_v1':M3XGBoostInputAdapter()})


def adapter_for(selection):
    return _ADAPTERS.get(selection.adapter)


def selection_for(metadata, state):
    from src.data.contracts.executable_inputs import InputAdapterSelection
    from src.preprocessing.canonical_recurrent import RecurrentPreprocessingState
    from src.preprocessing.canonical_transformer import TransformerPreprocessingState
    classes = {'tabular':TabularPreprocessingState,'recurrent':RecurrentPreprocessingState,'transformer':TransformerPreprocessingState}
    if type(state) is not classes[metadata.capability.representation]: raise ValueError()
    checked = type(state).model_validate_json(state.canonical_json)
    if checked.state_id != metadata.fitted_state_id: raise ValueError()
    return InputAdapterSelection(family=metadata.specification.family,representation=metadata.capability.representation,
        frozen_state_id=checked.state_id,compatibility_id=metadata.representation_compatibility_id,
        adapter='canonical_m3_xgboost_inputs_v1' if metadata.specification.family=='xgboost' else 'UNAVAILABLE'), checked
