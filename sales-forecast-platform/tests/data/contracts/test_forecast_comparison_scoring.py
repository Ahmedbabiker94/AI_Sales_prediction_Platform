"""Bounded signed fractions and explicit C2 policy declarations, no model work."""
from fractions import Fraction
import runpy
import pytest
from src.data.contracts.forecast_comparison import (SignedRatio,JointScoreDelta,JointComparisonPolicy,
    JointComparisonPlan,JointScoringBudgets)
from src.data.contracts.forecast_evaluation import ScoringArithmeticPolicy
from src.data.processing.forecast_evaluation import statistics

@pytest.mark.parametrize('n,d', [('0','1'),('-1','2'),('3','7'),('-9','1')])
def test_signed_fraction_exact_canonical(n,d):
    r=SignedRatio(numerator=n,denominator=d)
    assert r.fraction==Fraction(int(n),int(d))
    with pytest.raises(Exception):r.numerator='5'

@pytest.mark.parametrize('n,d',[('-0','1'),('01','1'),('+1','1'),('1','0'),('2','4'),('-2','4'),('0','2'),('1','-1'),(1,'1'),('1',True),('1'*2501,'1'),(str(2**8192),'1')])
def test_signed_fraction_rejects_malformed_noncanonical_unbounded(n,d):
    with pytest.raises(ValueError):SignedRatio(numerator=n,denominator=d)

@pytest.mark.parametrize('a,b',[(1,4),(4,4),(9,4)])
def test_negative_zero_positive_deltas_are_exact(a,b):
    p=ScoringArithmeticPolicy(policy_id='synthetic')
    left=statistics(1,Fraction(a),Fraction(a*a),Fraction(10),p)
    right=statistics(1,Fraction(b),Fraction(b*b),Fraction(10),p)
    def r(v):return SignedRatio(numerator=str(v.numerator),denominator=str(v.denominator))
    delta=JointScoreDelta(semantic_group_id='1'*64,forecast_key_ids=('2'*64,),candidate=left,baseline=right,
        sae=r(Fraction(a-b)),sse=r(Fraction(a*a-b*b)),mae=r(Fraction(a-b)),mse=r(Fraction(a*a-b*b)),
        wape_status='defined',wape=r(Fraction(a-b,10)))
    assert delta.mae.fraction==a-b
    assert not {'rmse','rmse_delta','percentage_improvement'}&set(type(delta).model_fields)
    with pytest.raises(ValueError):JointScoreDelta.model_validate({**delta.model_dump(),'sae':dict(numerator='99',denominator='1')})

@pytest.mark.parametrize('field',list(JointScoringBudgets.model_fields))
@pytest.mark.parametrize('bad',[0,-1,True,1.5,'2'])
def test_scoring_budgets_strict_positive(field,bad):
    with pytest.raises(ValueError):JointScoringBudgets(**{field:bad})

def test_policy_is_versioned_immutable_and_validation_only():
    p=JointComparisonPolicy(policy_id='synthetic',arithmetic=ScoringArithmeticPolicy(policy_id='exact'))
    assert p.digest!=p.model_copy(update={'event_weighting':'forecast_event_weighted_v1'}).digest
    for update in ({'partitions':('test',)},{'partitions':()},{'version':'2.0'},{'winner':True}):
        with pytest.raises(ValueError):JointComparisonPolicy.model_validate({**p.model_dump(),**update})
    with pytest.raises(Exception):p.policy_id='other'
