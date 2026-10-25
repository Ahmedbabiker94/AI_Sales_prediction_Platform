"""M8 additive metric/ensemble wrappers over accepted B2a exact arithmetic."""
from fractions import Fraction
from src.data.contracts.backtesting import identity
from src.data.contracts.forecast_evaluation import ScoringArithmeticPolicy
from src.data.contracts.forecast_comparison import SignedRatio
from src.data.contracts.candidate_evaluation import MetricResult
from src.data.processing.forecast_evaluation import bounded, rational, ratio, statistics

POLICY=ScoringArithmeticPolicy(policy_id='m8-exact-metrics-v1')


def signed(value):
    bounded(value,POLICY)
    return SignedRatio(numerator=str(value.numerator),denominator=str(value.denominator))


def metrics(producer_id,population_id,pairs,checkpoint=lambda:None):
    # Inputs are bounded already-admitted (prediction Fraction, actual float64).
    if not 1<=len(pairs)<=256:raise ValueError()
    sae=sse=saa=bias=smape=Fraction()
    for predicted,actual in pairs:
        checkpoint()
        if type(predicted) is not Fraction:raise ValueError()
        bounded(predicted,POLICY,True);truth=rational(actual,POLICY)
        error=bounded(predicted-truth,POLICY)
        sae=bounded(sae+abs(error),POLICY);sse=bounded(sse+bounded(error*error,POLICY),POLICY)
        saa=bounded(saa+abs(truth),POLICY);bias=bounded(bias+error,POLICY)
        denominator=abs(predicted)+abs(truth)
        smape=bounded(smape+(bounded(2*abs(error)/denominator,POLICY) if denominator else Fraction()),POLICY)
    n=len(pairs)
    return MetricResult(producer_id=producer_id,population_id=population_id,
        statistics=statistics(n,sae,sse,saa,POLICY),smape=ratio(smape/n,POLICY),mean_signed_error=signed(bias/n))


def ensemble_values(configuration,batches,keys,checkpoint=lambda:None):
    if tuple(b.source.candidate_id for b in batches)!=configuration.members:raise ValueError()
    indexed=[{p.key.key_id:p.value for p in b.observations} for b in batches]
    if any(set(values)!={k.key_id for k in keys} for values in indexed):raise ValueError()
    output=[]
    for key in keys:
        checkpoint();value=Fraction()
        for weight,points in zip(configuration.effective_weights,indexed):
            value=bounded(value+bounded(weight*rational(points[key.key_id],POLICY),POLICY),POLICY)
        output.append((key,value))
    return tuple(output)
