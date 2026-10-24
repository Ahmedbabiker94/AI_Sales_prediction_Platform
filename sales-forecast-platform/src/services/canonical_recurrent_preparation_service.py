"""Provider -> accepted canonical/M3 preparation -> M4 frozen sequences.

The M3 intermediate is a numeric canonical tabular buffer, not a model producer.
Targets remain separate and never enter scaler fitting or sequence execution.
"""
from dataclasses import dataclass
from time import monotonic

from src.data.contracts.model_evaluation import _reject_declaration
from src.data.contracts.backtesting import identity
from src.data.contracts.temporal_preparation import TemporalPreparationPlan
from src.preprocessing.canonical_recurrent import (
    RecurrentPreparationRequest, FrozenRecurrentPreprocessor, fit_recurrent_state,
)
from src.services.canonical_tabular_preparation_service import CanonicalTabularPreparationService


@dataclass(frozen=True)
class PreparedRecurrentPartitions:
    outputs: tuple
    targets: tuple[tuple[float, ...], ...]
    source_preparation_id: str

    @property
    def preparation_id(self):
        return identity(['canonical_recurrent_preparation_v1',self.source_preparation_id,
                         [o.output_id for o in self.outputs]])


class CanonicalRecurrentPreparationService:
    def __init__(self, tabular):
        if type(tabular) is not CanonicalTabularPreparationService: _reject_declaration()
        self._tabular=tabular

    def prepare(self, plan, configuration, request, *, cancelled=lambda:False):
        try:
            req=RecurrentPreparationRequest.model_validate_json(request.canonical_json)
            plan=TemporalPreparationPlan.model_validate(plan.model_dump(warnings='error'))
            fold=next(f for f in plan.folds if f.fold_id==req.fold_id)
            if any(o not in fold.origins for o in req.origins):raise ValueError('unapproved origins')
            cells=sum(len(d.series_ids) for d in plan.datasets)*len(req.origins)*req.window.window*len(req.feature_names)
            if cells>min(plan.budgets.max_output_cells,100000):raise ValueError('aggregate sequence admission exceeded')
            if cancelled() is not False:raise ValueError('cancelled')
            started=monotonic()
            # Reuse original authorization, capture, fitted categories and feature
            # execution. No second capture or alternate feature formulas.
            source=self._tabular.prepare(plan,configuration,dtype='float64',cancelled=cancelled)
            groups={}
            labels={}
            for output,targets in zip(source.outputs,source.targets):
                if output.state.fold_id!=req.fold_id:continue
                groups.setdefault(output.state.state_id,[]).append(output)
                for grid,value in zip(output.representation.samples,targets):
                    p=grid[0];labels[(p.organization_id,p.series_id,p.forecast_origin)]=value
            outputs=[]; targets=[]; size=0
            for batches in groups.values():
                if cancelled() is not False or monotonic()-started>=plan.budgets.max_elapsed_seconds:raise ValueError('interrupted')
                batches=tuple(batches)
                state=fit_recurrent_state(batches[0].state,tuple(b for b in batches if b.representation.binding.partition=='training'),req)
                for output in FrozenRecurrentPreprocessor(state).transform(batches):
                    size+=len(output.canonical_bytes)
                    if size>min(plan.budgets.max_assembly_bytes,32*1024*1024):raise ValueError('aggregate output budget exceeded')
                    outputs.append(output)
                    targets.append(tuple(labels[(g[0].organization_id,g[0].series_id,g[0].forecast_origin)]
                        for g in output.representation.samples))
            if not outputs or cancelled() is not False or monotonic()-started>=plan.budgets.max_elapsed_seconds:
                raise ValueError('recurrent preparation failed')
            return PreparedRecurrentPartitions(tuple(outputs),tuple(targets),source.preparation_id)
        except Exception:
            pass
        _reject_declaration()
