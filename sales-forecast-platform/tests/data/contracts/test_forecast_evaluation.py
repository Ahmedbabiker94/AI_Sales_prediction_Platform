"""Synthetic arithmetic/contracts only. No model fitting or external services."""
from decimal import Decimal,localcontext
from fractions import Fraction
import math
import runpy

import pytest

from src.data.contracts.forecast_evaluation import ScoringArithmeticPolicy,ForecastScoringPlan,ExactRatio,ScoringStatistics
from src.data.processing.forecast_evaluation import (score_point,statistics,bounded,rational,ScoringArithmeticError)


def policy(**changes):return ScoringArithmeticPolicy(policy_id='synthetic-exact',**changes)


def test_independent_signed_reference_exact_statistics():
    values=[score_point(p,Decimal(a),policy()) for p,a in zip([-2.,1.,3.],['-4','0','6'])]
    stats=statistics(3,*(sum((getattr(v,k).fraction for v in values),Fraction(0)) for k in ('sae','sse','saa')),policy())
    assert (stats.count,stats.sae.fraction,stats.sse.fraction,stats.saa.fraction)==(3,6,14,10)
    assert stats.mae.fraction==2 and stats.mse.fraction==Fraction(14,3)
    assert stats.rmse_radicand==stats.mse and stats.wape.fraction==Fraction(3,5)
    assert math.sqrt(stats.rmse_radicand.fraction)==pytest.approx(2.160246899469287)


@pytest.mark.parametrize('actual,prediction',[('0',0.),('-0.0000',-0.),('0',3.)])
def test_zero_denominator_is_always_undefined(actual,prediction):
    value=score_point(prediction,Decimal(actual),policy())
    assert value.count==1 and value.saa.fraction==0
    assert value.wape_status=='undefined_zero_denominator' and value.wape is None


@pytest.mark.parametrize('precision',[2,7,28,100])
def test_decimal_context_does_not_change_results(precision):
    expected=Fraction.from_float(.1)-Fraction(Decimal('.10'))
    with localcontext() as ctx:
        ctx.prec=precision
        value=score_point(.1,Decimal('.10'),policy())
        assert value.sae.fraction==abs(expected) and value.sse.fraction==expected*expected


@pytest.mark.parametrize('value',['1E-400','-1E-400','1E400','-1E400','19.9900'])
def test_tiny_and_large_canonical_actuals_remain_exact(value):
    original=Decimal(value);before=original.as_tuple();result=score_point(0.,original,policy())
    assert result.sae.fraction==abs(Fraction(original)) and result.wape.fraction==1
    assert original.as_tuple()==before


def test_float64_maximum_does_not_overflow_squared_error():
    number=float.fromhex('0x1.fffffffffffffp+1023')
    stats=score_point(number,0,policy())
    assert stats.sse.fraction==Fraction.from_float(number)**2


@pytest.mark.parametrize('value',[True,None,'1',float('nan'),float('inf'),Decimal('NaN'),Decimal('Infinity')])
def test_invalid_labels_fail_closed(value):
    with pytest.raises(ScoringArithmeticError):score_point(1.,value,policy())


@pytest.mark.parametrize('value',[True,1,Decimal('1'),float('nan'),float('inf')])
def test_prediction_type_is_explicit(value):
    with pytest.raises(ScoringArithmeticError):score_point(value,1,policy())


@pytest.mark.parametrize('case',['digits','exponent','operand','accumulator','count'])
def test_arithmetic_resource_limits(case):
    if case=='digits':
        with pytest.raises(ScoringArithmeticError):score_point(0.,Decimal('1234'),policy(max_significant_digits=3))
    elif case=='exponent':
        with pytest.raises(ScoringArithmeticError):score_point(0.,Decimal('1E-9'),policy(max_abs_exponent=8))
    elif case=='operand':
        with pytest.raises(ScoringArithmeticError):score_point(0.,2**20,policy(max_operand_bits=20))
    elif case=='accumulator':
        with pytest.raises(ScoringArithmeticError):score_point(16.,0,policy(max_operand_bits=8,max_accumulator_bits=8))
    else:
        with pytest.raises(ScoringArithmeticError):statistics(0,Fraction(0),Fraction(0),Fraction(0),policy())


@pytest.mark.parametrize('numerator,denominator',[('2','4'),('-1','1'),('0','2'),('01','1'),('1','0')])
def test_ratios_are_canonical_nonnegative(numerator,denominator):
    with pytest.raises(ValueError):ExactRatio(numerator=numerator,denominator=denominator)


def test_immutable_policy_and_versioned_identity():
    p=policy()
    with pytest.raises(Exception):p.max_operand_bits=3
    assert p.digest!=policy(max_operand_bits=3000).digest
    with pytest.raises(ValueError):policy(version='2.0')


@pytest.mark.parametrize('partitions',[(),('training',),('validation','validation'),('test','validation')])
def test_only_explicit_ordered_heldout_partitions(partitions):
    binding=runpy.run_path('tests/data/contracts/test_backtesting.py')['binding']()
    with pytest.raises(ValueError):ForecastScoringPlan(plan_id='score',producer_plan_digest='1'*64,datasets=(binding,),
        partitions=partitions,arithmetic=policy())
