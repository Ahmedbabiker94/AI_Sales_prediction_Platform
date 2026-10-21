"""Guarded C1 declarations and unchanged exact arithmetic policies."""
from fractions import Fraction
import traceback
from uuid import uuid4
import warnings

import pytest

from src.data.contracts.model_joint_evaluation import JointH1ScoringBudgets,JointH1EvaluationResult
from src.data.contracts.model_evaluation import EvaluationDeclarationError
from src.data.contracts.forecast_evaluation import ScoringArithmeticPolicy
from src.data.processing.forecast_evaluation import score_point


@pytest.mark.parametrize('entry',['constructor','python','json','strings'])
@pytest.mark.parametrize('model',[JointH1ScoringBudgets,JointH1EvaluationResult])
def test_public_contract_diagnostics(entry,model,capsys,caplog):
    import json
    secret='C1DECL_'+uuid4().hex;data={secret:{secret:secret}}
    operations={'constructor':lambda:model(**data),'python':lambda:model.model_validate(data),
        'json':lambda:model.model_validate_json(json.dumps(data)),
        'strings':lambda:model.model_validate_strings(data)}
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        try:raise ValueError(secret)
        except ValueError:
            with pytest.raises(EvaluationDeclarationError) as caught:operations[entry]()
    e=caught.value;capture=capsys.readouterr()
    assert e.__cause__ is None and e.__context__ is None
    assert secret not in str(e)+repr(e)+repr(e.args)+''.join(traceback.format_exception(e))+repr(seen)+capture.err+caplog.text
    with warnings.catch_warnings(record=True) as observed:
        warnings.simplefilter('always');warnings.warn('unrelated-c1-warning',UserWarning)
    assert len(observed)==1


@pytest.mark.parametrize('field',list(JointH1ScoringBudgets.model_fields))
@pytest.mark.parametrize('value',[True,False,0,-1,'1',1.5])
def test_strict_resource_declarations(field,value):
    with pytest.raises(EvaluationDeclarationError):JointH1ScoringBudgets(**{field:value})


def test_budget_detachment_immutability_and_roundtrip():
    b=JointH1ScoringBudgets();encoded=b.model_dump_json();data=b.model_dump();data['max_points']=1
    assert b.max_points==512 and JointH1ScoringBudgets.model_validate_json(encoded).model_dump_json()==encoded
    with pytest.raises(Exception):b.max_points=1


@pytest.mark.parametrize('prediction,actual',[(0.,0.),(-0.,0.),(1.5,0.),(-2.,-1.),(35.62840270996094,-2.)])
def test_exact_binary_interpretation_and_undefined_wape(prediction,actual):
    p=ScoringArithmeticPolicy(policy_id='canonical-exact-c1-v1');s=score_point(prediction,actual,p)
    error=Fraction.from_float(prediction)-Fraction.from_float(actual)
    assert (s.sae.fraction,s.sse.fraction,s.saa.fraction)==(abs(error),error*error,abs(Fraction.from_float(actual)))
    assert s.rmse_radicand==s.mse
    if actual==0:assert s.wape is None and s.wape_status=='undefined_zero_denominator'
