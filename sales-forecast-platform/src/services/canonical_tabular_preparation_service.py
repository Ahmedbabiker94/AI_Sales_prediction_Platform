"""M3 adapter of the accepted provider-backed temporal preparation service.

No SQL, model training, registry operations, or new capture protocol. Original
canonical authorization/availability/return checks run before these detached
representations are assembled. Results are data, never fitting permission.
"""
from dataclasses import dataclass
from time import monotonic

import pandas as pd

from src.data.contracts.model_representation import TabularRepresentation, OrderedFeatures
from src.data.contracts.model_evaluation import _reject_declaration
from src.data.contracts.temporal_preparation import TemporalPreparationPlan, TemporalDatasetBinding
from src.preprocessing.canonical_tabular import TabularPreprocessingState, bind_output
from src.services.canonical_training_preparation_service import CanonicalTrainingPreparationService


@dataclass(frozen=True)
class PreparedTabularPartitions:
    outputs: tuple
    # Separately captured, authorized targets; never part of state or features.
    targets: tuple[tuple[float, ...], ...]
    preparation_id: str

    def partition(self, name):
        if type(name) is not str or name not in ('training', 'validation', 'test'): _reject_declaration()
        pairs = [(o, y) for o, y in zip(self.outputs, self.targets) if o.representation.binding.partition == name]
        if not pairs: _reject_declaration()
        # Never combine distinct learned states, folds, or organizations silently.
        if len({o.state.state_id for o, _ in pairs}) != 1:
            _reject_declaration()
        frames = [o.matrix for o, _ in pairs]
        result = pd.concat(frames)
        y = pd.Series([v for _, values in pairs for v in values], index=result.index, dtype='float64')
        return result, y


class CanonicalTabularPreparationService:
    def __init__(self, preparation):
        if type(preparation) is not CanonicalTrainingPreparationService:
            _reject_declaration()
        self._preparation = preparation

    def prepare(self, plan, configuration, *, model_family='xgboost', dtype='float64', cancelled=lambda: False):
        try:
            # Reject unsupported policy/family before provider access or fitting.
            if (type(model_family) is not str or model_family not in ('xgboost', 'lightgbm')
                    or type(dtype) is not str or dtype not in ('float32', 'float64')):
                raise ValueError()
            plan = TemporalPreparationPlan.model_validate(plan.model_dump(warnings='error'))
            if plan.forecast_horizon != 1 or plan.capability != 'supervised_h1': raise ValueError()
            result = self._preparation.prepare_temporal(plan, configuration, cancelled=cancelled)
            outputs, targets = [], []
            started = monotonic(); assembled = 0
            for group in result.origins:
                if cancelled() is not False or monotonic()-started >= plan.budgets.max_elapsed_seconds:
                    raise ValueError('tabular assembly interrupted')
                prepared = group.preparation
                cfg = prepared.schema.configuration
                features = []
                for name in cfg.ordered_outputs:
                    source = cfg.output_dtypes[name]
                    if source not in ('float64', 'float32', 'bool', 'int8', 'int16', 'int32', 'int64'):
                        raise ValueError('unsupported tabular source type')
                    target = source if source == 'bool' or source.startswith('int') else dtype
                    features.append(dict(name=name, dtype=target,
                        numeric_policy=('strict_exact_'+target+'_v1' if target.startswith('float') else
                                        'strict_boolean_v1' if target == 'bool' else 'strict_integer_v1'),
                        categorical_policy='frozen_vocabulary_v1' if name in cfg.category_encoding else 'none',
                        vocabulary_field=name if name in cfg.category_encoding else None,
                        missing='mask' if cfg.missing_value_behavior[name] == 'nan' else 'reject'))
                feature_schema = OrderedFeatures(features=tuple(features))
                dataset = TemporalDatasetBinding.model_validate_json(group.dataset_binding_json)
                state = TabularPreprocessingState(plan=plan, fold_id=group.fold_id, dataset=dataset,
                    canonical_schema=prepared.schema, canonical_state=prepared.fitted_state, feature_schema=feature_schema)
                matrix = prepared.matrix
                definitions = {s.series_id: s for s in prepared.context.metadata.series}
                # M2 batches are bounded to 32 examples; split without changing ordering.
                for start in range(0, len(matrix), 32):
                    chunk = matrix.iloc[start:start+32]
                    samples = []
                    origin = prepared.context.forecast_origin
                    from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
                    engine = CanonicalFeaturePipeline(cfg)
                    for org, series, target in chunk.index:
                        samples.append((dict(plan_digest=plan.digest, dataset_version=dataset.reference.version_id,
                            fold_id=group.fold_id, organization_id=org, series_id=series, metric=cfg.target_metric,
                            unit=cfg.execution.measurement.unit, currency=cfg.execution.measurement.currency,
                            frequency=plan.frequency, timezone=plan.timezone,
                            dimensions=tuple(sorted((f, definitions[series].dimensions[f]) for f in cfg.execution.dimension_fields)),
                            forecast_origin=origin, horizon_step=1, target_period=target,
                            target_period_end=engine._advance(origin, 1)),))
                    r = TabularRepresentation(binding=dict(plan=plan, fold_id=group.fold_id, partition=group.partition,
                        canonical_schema=prepared.schema, canonical_state=prepared.fitted_state,
                        historical_revision=prepared.context.metadata.manifest_digest,
                        historical_evidence_digest=group.event_id), feature_schema=feature_schema,
                        samples=tuple(samples), shape=chunk.shape,
                        missing_mask=tuple(tuple(bool(pd.isna(v)) for v in row) for row in chunk.itertuples(index=False, name=None)),
                        available_at=(origin,)*len(chunk))
                    output = bind_output(state, r, chunk, model_family)
                    assembled += len(output.canonical_bytes)
                    if assembled > min(plan.budgets.max_assembly_bytes, 32*1024*1024):
                        raise ValueError('tabular output budget exceeded')
                    outputs.append(output)
                    targets.append(prepared.converted_labels[start:start+32])
            if cancelled() is not False or monotonic()-started >= plan.budgets.max_elapsed_seconds: raise ValueError()
            return PreparedTabularPartitions(tuple(outputs), tuple(targets), result.preparation_id)
        except Exception:
            pass
        _reject_declaration()
