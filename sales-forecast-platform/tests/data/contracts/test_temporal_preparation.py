from datetime import datetime, timezone
from uuid import UUID

import pandas as pd
import pytest

from src.data.contracts.dataset import StoredDatasetReference
from src.data.contracts.temporal_preparation import (
    TemporalBudgets, TemporalDatasetBinding, TemporalFold, TemporalPreparationPlan,
)


def date(day):
    return datetime.fromisoformat(day).replace(tzinfo=timezone.utc)


def fold(**changes):
    values=dict(fold_id='fold-1',training_origins=(date('2020-02-03'),date('2020-02-10')),
        validation_origins=(date('2020-02-17'),),test_origins=(date('2020-02-24'),),
        calibration_start=date('2020-01-06'),categorical_cutoff=date('2020-02-03'),
        model_fit_cutoff=date('2020-02-17'),assessment_cutoff=date('2020-03-02'))
    values.update(changes)
    return TemporalFold(**values)


def binding(**changes):
    values=dict(reference=StoredDatasetReference(UUID(int=1),UUID(int=2),UUID(int=3),'a'*64),
        source_id=UUID(int=4),manifest_digest='a'*64,admission_digest='b'*64,mapping_id='mapping',
        mapping_version='1.0',mapping_digest='c'*64,quality_policy_id='SALES_GENERAL',
        quality_policy_version='1.0.0',series_definition_digest='d'*64,series_ids=(UUID(int=5),))
    values.update(changes)
    return TemporalDatasetBinding(**values)


def plan(**changes):
    values=dict(plan_id='plan-1',datasets=(binding(),),configuration_id='features',configuration_version='2.0',
        configuration_digest='e'*64,frequency='weekly',calendar='gregorian',timezone='UTC',
        period_anchor='start',purpose='production_training',folds=(fold(),))
    values.update(changes)
    return TemporalPreparationPlan(**values)


def test_frozen_nested_contracts_roundtrip_and_identity():
    p=plan()
    assert TemporalPreparationPlan.model_validate_json(p.model_dump_json())==p
    assert p.digest==TemporalPreparationPlan.model_validate_json(p.model_dump_json()).digest
    with pytest.raises(ValueError):p.folds[0].fold_id='changed'
    with pytest.raises(TypeError):p.folds[0].training_origins[0]=date('2020-01-01')
    with pytest.raises(ValueError):p.datasets[0].series_ids=(UUID(int=9),)
    assert plan(plan_id='plan-2').digest!=p.digest
    assert plan(folds=(fold(categorical_cutoff=date('2020-01-27')),)).digest!=p.digest


@pytest.mark.parametrize('changes',[
    {'fold_id':' '},{'training_origins':()},
    {'training_origins':(date('2020-02-10'),date('2020-02-03'))},
    {'training_origins':(date('2020-02-03'),date('2020-02-03'))},
    {'validation_origins':(date('2020-02-10'),)},
    {'test_origins':(date('2020-02-03'),)},
    {'categorical_cutoff':date('2020-02-10')},
    {'calibration_start':date('2020-02-03')},
    {'model_fit_cutoff':date('2020-02-24')},
    {'model_fit_cutoff':date('2020-02-10')},
    {'assessment_cutoff':date('2020-02-24')},
    {'history_strategy':'rolling'},{'purge_policy':'purge'},
    {'embargo_periods':1},{'embargo_periods':True},
    {'categorical_cutoff':datetime(2020,2,3)},
])
def test_invalid_or_unsupported_fold_rejects(changes):
    with pytest.raises(ValueError):fold(**changes)


@pytest.mark.parametrize('changes',[
    {'version':'2.0'},{'purpose':'diagnostic'},{'timezone':'invalid-zone'},
    {'calendar':'fiscal'},{'forecast_horizon':True},{'forecast_horizon':0},
    {'forecast_horizon':2},{'folds':(fold(),fold())},
    {'datasets':(binding(),binding())},{'configuration_id':' '},
    {'budgets':TemporalBudgets(max_origins=3)},
])
def test_invalid_plan_rejects(changes):
    with pytest.raises(ValueError):plan(**changes)


def test_higher_horizon_planning_does_not_declare_execution():
    p=plan(capability='planning_only',forecast_horizon=3)
    assert p.forecast_horizon==3 and p.capability=='planning_only'
    assert p.digest!=plan().digest


@pytest.mark.parametrize('changes',[
    {'manifest_digest':'f'*64},{'mapping_version':'bad'},
    {'series_ids':(UUID(int=6),UUID(int=5))},{'series_ids':(UUID(int=5),UUID(int=5))},
    {'admission_digest':'bad'},{'mapping_id':'Bearer secret'},
])
def test_invalid_binding_rejects(changes):
    with pytest.raises(ValueError):binding(**changes)


@pytest.mark.parametrize('value',[0,-1,True,1.5,'4'])
def test_budget_values_are_strict_positive_integers(value):
    with pytest.raises(ValueError):TemporalBudgets(max_output_cells=value)


def test_walk_forward_reuses_training_origins_but_not_heldout_events():
    second=fold(fold_id='fold-2',training_origins=(date('2020-02-03'),date('2020-02-10'),date('2020-02-17')),
        validation_origins=(date('2020-03-02'),),test_origins=(),model_fit_cutoff=date('2020-03-02'),
        assessment_cutoff=date('2020-03-09'))
    assert len(plan(folds=(fold(),second)).folds)==2
    with pytest.raises(ValueError):plan(folds=(second,fold()))


def test_ambiguous_dst_cutoffs_compare_instants_without_normalizing_inputs():
    from zoneinfo import ZoneInfo
    ny=ZoneInfo('America/New_York')
    values=dict(fold_id='dst',training_origins=(datetime(2020,10,31,tzinfo=ny),),
        calibration_start=datetime(2020,10,1,tzinfo=ny),categorical_cutoff=datetime(2020,10,31,tzinfo=ny),
        model_fit_cutoff=datetime(2020,11,1,1,30,tzinfo=ny,fold=1),
        assessment_cutoff=datetime(2020,11,1,1,40,tzinfo=ny,fold=0))
    # 06:30 UTC follows 05:40 UTC despite the displayed local wall times.
    with pytest.raises(ValueError):TemporalFold(**values)
    values['assessment_cutoff']=datetime(2020,11,1,1,40,tzinfo=ny,fold=1)
    good=TemporalFold(**values)
    assert good.model_fit_cutoff.fold==1 and good.model_fit_cutoff.tzinfo is ny


@pytest.mark.parametrize('timestamp',['2020-02-03T00:00:00.000000001Z',
    pd.Timestamp('2020-02-03T00:00:00.000000001Z'),1580688000,'1580688000','1580688000000'])
def test_origins_cannot_be_truncated_or_inferred_from_numeric_epochs(timestamp):
    with pytest.raises(ValueError):fold(training_origins=(timestamp,))
