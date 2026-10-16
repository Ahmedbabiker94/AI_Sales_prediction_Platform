"""Pure C1 declaration validation, no capture or predictor execution."""
import runpy
import pytest
from src.data.contracts.forecast_comparison import JointPredictionPlan, JointPredictionBudgets, JointProducerPlan, JointForecastKey
from src.data.contracts.backtesting import ForecastEvaluationPlan


@pytest.fixture
def joint_plan():
    from datetime import timedelta
    from src.data.contracts.temporal_preparation import TemporalFold
    ref=runpy.run_path('tests/data/contracts/test_backtesting.py');o=ref['O']
    fold=TemporalFold(fold_id='fold-1',training_origins=(o,),validation_origins=(o+timedelta(weeks=4),),
        calibration_start=o-timedelta(weeks=4),categorical_cutoff=o,model_fit_cutoff=o+timedelta(weeks=4),
        assessment_cutoff=o+timedelta(weeks=8))
    producers=[]
    for role in ('candidate','baseline'):
        cap=ref['cap'](predictor_id=role,supported_horizon=4)
        p=ForecastEvaluationPlan(plan_id=role,datasets=(ref['binding'](),),predictor=cap,
            configuration_id=role,configuration_version='2.0',configuration_digest=ref['D'],frequency='weekly',
            calendar='gregorian',timezone='UTC',period_anchor='start',folds=(fold,),forecast_horizon=4)
        producers.append(JointProducerPlan(role=role,profile_id=role,plan=p))
    return JointPredictionPlan(plan_id='pure-contract',producers=tuple(producers))


@pytest.mark.parametrize('field',['max_predictor_calls','max_prediction_events','max_prefix_cells',
    'max_verification_calls','max_verification_claims','max_output_bytes','max_work_cells','max_elapsed_seconds'])
@pytest.mark.parametrize('bad',[True,0,-1,1.5,'2'])
def test_joint_budget_positive_strict_integer(field,bad):
    with pytest.raises(ValueError):JointPredictionBudgets(**{field:bad})


@pytest.mark.parametrize('count',[0,1,3])
def test_exactly_two_producers(joint_plan,count):
    data=joint_plan.model_dump();data['producers']=(data['producers']*2)[:count]
    with pytest.raises(ValueError):JointPredictionPlan.model_validate(data)


@pytest.mark.parametrize('fault',['duplicate','order','role','unknown','strategy','representation','calendar','origin','horizon','assessment'])
def test_roles_scope_and_execution_capability_fail_closed(joint_plan,fault):
    data=joint_plan.model_dump()
    if fault=='duplicate':data['producers'][1]['profile_id']=data['producers'][0]['profile_id']
    elif fault=='order':data['producers']=tuple(reversed(data['producers']))
    elif fault=='role':data['producers'][1]['role']='candidate'
    elif fault=='unknown':data['trusted']=True
    else:
        p=data['producers'][1]['plan']
        if fault=='strategy':p['predictor']['strategy']='direct_multi_horizon'
        elif fault=='representation':p['predictor']['representation_kind']='statistical'
        elif fault=='calendar':p['timezone']='America/New_York'
        elif fault=='horizon':p['forecast_horizon']=3
        else:
            from datetime import timedelta
            p['folds'][0]['training_origins']=tuple(o+timedelta(weeks=1) for o in p['folds'][0]['training_origins']) if fault=='origin' else p['folds'][0]['training_origins']
            if fault=='assessment':p['folds'][0]['assessment_cutoff']+=timedelta(weeks=1)
    with pytest.raises(ValueError):JointPredictionPlan.model_validate(data)


def test_configuration_and_plan_ids_may_differ_but_common_grid_must_match(joint_plan):
    assert joint_plan.producers[0].plan.digest!=joint_plan.producers[1].plan.digest
    assert JointPredictionPlan.model_validate_json(joint_plan.model_dump_json())==joint_plan
    with pytest.raises(Exception):joint_plan.plan_id='mutated'
    data=joint_plan.model_dump();data['plan_id']='another'
    assert JointPredictionPlan.model_validate(data).digest!=joint_plan.digest
