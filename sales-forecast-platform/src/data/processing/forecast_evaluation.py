"""Exact bounded scoring arithmetic. These pure functions grant no data access."""
from decimal import Decimal
from fractions import Fraction
import math
import struct

import numpy as np

from src.data.contracts.forecast_evaluation import ExactRatio,ScoringArithmeticPolicy,ScoringStatistics,ScoredGroup


class ScoringArithmeticError(ValueError):pass


def numeric_identity(value):
    if type(value) is Decimal:
        t=value.as_tuple();return ('decimal',t.sign,tuple(t.digits),t.exponent)
    if type(value) in (int,np.int8,np.int16,np.int32,np.int64,np.uint8,np.uint16,np.uint32,np.uint64):return ('integer',str(int(value)))
    if type(value) in (float,np.float64) and math.isfinite(value):return ('float64',struct.pack('>d',float(value)).hex())
    raise ScoringArithmeticError('unsupported scoring value')


def bounded(value,policy,operand=False):
    limit=policy.max_operand_bits if operand else policy.max_accumulator_bits
    if max(value.numerator.bit_length(),value.denominator.bit_length())>limit:
        raise ScoringArithmeticError('scoring arithmetic bound exceeded')
    return value


def rational(value,policy):
    if type(value) is Decimal:
        t=value.as_tuple()
        if not value.is_finite() or len(t.digits)>policy.max_significant_digits or abs(t.exponent)>policy.max_abs_exponent:
            raise ScoringArithmeticError('unsupported scoring magnitude')
        # Digits/exponent are bounded before constructing the exact rational.
        result=Fraction(value)
    elif type(value) in (int,np.int8,np.int16,np.int32,np.int64,np.uint8,np.uint16,np.uint32,np.uint64):
        if int(value).bit_length()>policy.max_operand_bits:raise ScoringArithmeticError('scoring operand bound exceeded')
        result=Fraction(int(value))
    elif type(value) in (float,np.float64) and math.isfinite(value):result=Fraction.from_float(float(value))
    else:raise ScoringArithmeticError('unsupported scoring value')
    return bounded(result,policy,True)


def ratio(value,policy):
    bounded(value,policy)
    return ExactRatio(numerator=str(value.numerator),denominator=str(value.denominator))


def statistics(count,sae,sse,saa,policy):
    if type(count) is not int or not 1<=count<=10000:raise ScoringArithmeticError('nonempty bounded statistics required')
    for value in (sae,sse,saa):
        if type(value) is not Fraction or value<0:raise ScoringArithmeticError('nonnegative statistics required')
        bounded(value,policy)
    mse=ratio(sse/count,policy)
    return ScoringStatistics(count=count,sae=ratio(sae,policy),sse=ratio(sse,policy),saa=ratio(saa,policy),
        mae=ratio(sae/count,policy),mse=mse,rmse_radicand=mse,
        wape_status='defined' if saa else 'undefined_zero_denominator',wape=ratio(sae/saa,policy) if saa else None)


def score_point(predicted,actual,policy):
    policy=ScoringArithmeticPolicy.model_validate(policy.model_dump())
    if type(predicted) not in (float,np.float64):raise ScoringArithmeticError('float64 forecast point required')
    error=bounded(rational(predicted,policy)-rational(actual,policy),policy)
    # Operand bounds cap multiplication work; bound its exact product too.
    square=bounded(error*error,policy)
    return statistics(1,abs(error),square,abs(rational(actual,policy)),policy)


def grouping_key(event,plan):
    s=event.scope
    return (s.fold_id,str(s.organization_id),str(s.series_id),s.horizon_step,s.metric,s.unit,s.currency or '',
        event.partition,event.assessment_cutoff.isoformat(),s.forecast_origin.isoformat() if plan.event_weighting=='origin_scoped_v1' else '')


def group_scores(events,plan,checkpoint=lambda:None):
    """Only same-fold/tenant/series/horizon/measurement/E/policy pooling exists."""
    if not events or len(events)>plan.budgets.max_scored_events:raise ScoringArithmeticError('event budget exceeded')
    seen=set();groups={};policy=plan.arithmetic;sd=plan.digest;ad=policy.digest
    for event in events:
        checkpoint();sid=event.event_id
        if sid in seen:raise ScoringArithmeticError('duplicate scored event')
        seen.add(sid)
        if event.scoring_plan_digest!=sd or event.arithmetic_policy_digest!=ad:
            raise ScoringArithmeticError('scoring policy mismatch')
        key=grouping_key(event,plan)
        if key not in groups:
            if len(groups)>=plan.budgets.max_groups:raise ScoringArithmeticError('score group budget exceeded')
            groups[key]=[event,[],0,Fraction(0),Fraction(0),Fraction(0)]
        group=groups[key];group[1].append(sid);group[2]+=1
        for i,name in enumerate(('sae','sse','saa'),3):group[i]=bounded(group[i]+getattr(event.statistics,name).fraction,policy)
    output=[]
    for key,(first,ids,n,sae,sse,saa) in sorted(groups.items()):
        checkpoint();s=first.scope
        output.append(ScoredGroup(fold_id=s.fold_id,organization_id=s.organization_id,series_id=s.series_id,horizon_step=s.horizon_step,
            metric=s.metric,unit=s.unit,currency=s.currency,forecast_origin=s.forecast_origin if plan.event_weighting=='origin_scoped_v1' else None,
            assessment_cutoff=first.assessment_cutoff,partition=first.partition,scoring_plan_digest=sd,
            arithmetic_policy_digest=ad,label_revision_policy=plan.label_revision_policy,event_weighting=plan.event_weighting,
            event_ids=tuple(ids),statistics=statistics(n,sae,sse,saa,policy)))
    return tuple(output)
