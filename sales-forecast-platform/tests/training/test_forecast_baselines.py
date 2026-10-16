"""Real synthetic evaluation publications; trusted fixed baseline adapters only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from fractions import Fraction
import inspect
import math
import runpy
import traceback
from threading import Event
from uuid import UUID

import pandas as pd
import pytest

from src.data.contracts.backtesting import ForecastEvaluationPlan, PredictionBatch, PredictionPoint
from src.data.contracts.features import FeatureConfiguration
from src.data.contracts.forecast_evaluation import BaselineDefinition, ForecastScoringPlan, ScoringArithmeticPolicy
from src.data.contracts.temporal_preparation import TemporalFold
from src.data.contracts.dataset import StagingBudgets
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline, FeatureBudgets
from src.features.forecast_baselines import BaselineProfile, BaselineConfigurationError, LAG_FEATURE, build_baseline_profile
from src.services.canonical_baseline_evaluation_service import (
    BaselineAuthorityBinding, BaselineCompositionError, CanonicalBaselineEvaluationService)


def definition(algorithm='last_observation_v1', season=None, *, lookback=4, horizon=4):
    return BaselineDefinition(algorithm=algorithm, season_length=season, lookback=lookback, supported_horizon=horizon)


@pytest.fixture
def make_baseline(tmp_path, monkeypatch):
    reference = runpy.run_path('tests/training/test_deterministic_forecast_rollout.py')
    factory = reference['make_rollout'].__wrapped__(tmp_path)
    original_predict = BaselineProfile.__call__

    def make(*, baseline=None, horizon=4, origins=(4, 8), organizations=(2,), frequency='weekly',
             timezone='UTC', start='2020-01-06', raw_change=None, periods=12, decimal=False, template_change=None):
        d = baseline or definition(horizon=max(4, horizon))
        def raw(frame):
            step = 1 if frequency == 'daily' else 7
            # Extend an independently declared source, before mapping/admission.
            for n in range(12, periods):
                for region, delta in [('north', 0), ('south', 100)]:
                    row = frame[frame.branch == region].iloc[0].copy()
                    row['from'] = pd.Timestamp(start, tz=timezone) + pd.DateOffset(days=step*n)
                    row['to'] = row['from'] + pd.DateOffset(days=step)
                    row['reported'] = float(n + delta)
                    row['released'] = row['to']; row['proof'] = f'extended-{region}-{n}'
                    row['promo_release'] = row['from']; row['promo_proof'] = f'extended-category-{region}-{n}'
                    frame.loc[len(frame)] = row
            if raw_change:
                raw_change(frame)
        b = factory(organizations=organizations, frequency=frequency, timezone=timezone,
                    start=start, raw_change=raw, decimal=decimal)
        template = b['configuration']
        if template_change:
            data = template.model_dump(); template_change(data); template = FeatureConfiguration.model_validate(data)
        profile = build_baseline_profile(d, template)
        cfg = profile.configuration; at = b['at']
        fold = TemporalFold(fold_id='fold-1', training_origins=(at(origins[0]),), validation_origins=(at(origins[1]),),
            calibration_start=at(0), categorical_cutoff=at(4), model_fit_cutoff=at(origins[0]+horizon),
            assessment_cutoff=at(origins[1]+horizon))
        b['plan'] = ForecastEvaluationPlan.model_validate({**b['plan'].model_dump(), 'predictor':profile.capabilities,
            'configuration_id':cfg.configuration_id, 'configuration_version':cfg.version,
            'configuration_digest':CanonicalFeaturePipeline(cfg).digest, 'folds':(fold,), 'forecast_horizon':horizon})
        scoring = ForecastScoringPlan(plan_id='synthetic-baseline-scoring', producer_plan_digest=b['plan'].digest,
            datasets=b['plan'].datasets, partitions=('validation',), arithmetic=ScoringArithmeticPolicy(policy_id='synthetic-exact'))
        release = b['service'].verify_availability
        proof = reference['bind_history_authority'](b)
        state = dict(calls=[], reads=[], active=False, trace=[], cancelled=False, clock=0., final=False)
        b.update(baseline=d, profile=profile, configuration=cfg, template=template, scoring=scoring, proof=proof, state=state,
                 adapter_call=original_predict, permissions={'read':True, 'labels':True, 'revision':True, 'evaluation':True}, revoked=set())
        approved = {scoring.digest}; b['approved'] = approved
        opened = b['provider'].open_read
        @contextmanager
        def read(*a, **kw):
            state['reads'].append(len(state['calls'])); state['active'] = True
            try:
                with opened(*a, **kw) as session:
                    yield session
            finally:
                state['active'] = False
        b['provider'].open_read = read
        old_read = b['provider'].authorize_read
        b['provider'].authorize_read = lambda *args: b['permissions']['read'] and old_read(*args)
        current = b['provider'].require_current
        def publication(*args, **kwargs):
            state['trace'].append(('publication', state['final']))
            value = current(*args, **kwargs)
            if hook := b.get('read_hook'): hook()
            return value
        b['provider'].require_current = publication
        def predict(self, req):
            assert not state['active']
            assert len(state['reads']) == len(organizations)  # No scoring-label capture yet.
            assert req.feature_names == (LAG_FEATURE,)
            assert not any(hasattr(req, k) for k in ('labels', 'future_frame', 'provider', 'evaluator'))
            state['calls'].append(req)
            output = original_predict(self, req)
            if hook := b.get('prediction_hook'): return hook(req, output)
            return output
        monkeypatch.setattr(BaselineProfile, '__call__', predict)
        def rollout(ctx, stage):
            state['trace'].append(('rollout', stage, ctx.fold_id, ctx.forecast_origin))
            return ctx.plan_digest == b['plan'].digest and (ctx.metadata.reference.organization_id, ctx.fold_id, ctx.forecast_origin) not in b['revoked']
        def evaluation(ctx, stage):
            state['trace'].append(('evaluation', stage, ctx.fold_id, ctx.forecast_origin))
            if stage == 'return' and ctx.metadata is None:
                state['final'] = True
                if hook := b.get('global_hook'): hook()
            return b['permissions']['evaluation'] and ctx.scoring_plan_digest in approved and ctx.purpose == 'offline_forecast_evaluation'
        def label_use(ctx, stage):
            state['trace'].append(('labels', stage, ctx.fold_id, ctx.forecast_origin))
            if hook := b.get('label_hook'): hook(ctx, stage)
            return b['permissions']['labels'] and ctx.metadata is not None and ctx.metadata.purpose == 'offline_forecast_evaluation'
        def label_revision(ctx, claims):
            state['trace'].append(('revision', ctx.fold_id, ctx.forecast_origin))
            if not b['permissions']['revision']: return False
            for c in claims:
                t = proof['ledger'].get((c.organization_id, c.source_record_id))
                if (t is None or c.dataset_reference != t['metadata'].reference or ctx.metadata != t['metadata']
                        or c.manifest_digest != t['metadata'].manifest_digest or c.admission_digest != t['metadata'].admission_digest
                        or c.mapping_digest != next(x.mapping_digest for x in b['plan'].datasets if x.reference == c.dataset_reference)
                        or c.source_id != t['metadata'].source_id or c.series_id != t['series_id']
                        or c.metric != t['metadata'].mapping.target_metric
                        or (c.unit,c.currency) != (t['metadata'].mapping.measurement.unit,t['metadata'].mapping.measurement.currency)
                        or (c.period_start,c.period_end) != (t['period_start'],t['period_end'])
                        or c.value_identity != t['value_identity'] or c.lineage_id != t['lineage_id']
                        or c.available_at != t['available_at'] or c.evidence_reference != t['evidence']
                        or c.assessment_cutoff != ctx.assessment_cutoff or t['revision_available'] > c.assessment_cutoff):
                    return False
            return bool(claims)
        def binding(name, protocol, callback):
            return BaselineAuthorityBinding(name, 'synthetic-1', protocol, b['plan'].datasets, callback)
        options = dict(provider=b['provider'], release_authority=binding('release', 'release_availability_v1', release),
            value_authority=binding('independent-history', 'captured_history_value_v1', proof['authority']),
            label_revision_authority=binding('independent-label', 'captured_value_as_of_E_v1', label_revision),
            configuration=template, baseline_definitions=(d,), authorize_rollout=rollout,
            authorize_evaluation=evaluation, authorize_label_use=label_use, elapsed_clock=lambda:state['clock'])
        b['options'] = options
        b['composition'] = CanonicalBaselineEvaluationService(**options)
        return b
    return make


def evaluate(b, **kwargs):
    return b['composition'].evaluate_baseline(b['baseline'], b['scoring'], b['plan'], **kwargs)


def north_outputs(b, origin=None):
    origin = origin if origin is not None else b['at'](4)
    sid = next(s.series_id for s in b['metadata'][0].series if s.dimensions['location'] == 'north')
    return [row[0] for req in b['state']['calls'] if req.compatibility.forecast_origin == origin
            for scope,row in zip(req.scopes,req.rows) if scope.series_id == sid]


def test_last_observation_hand_calculated_four_steps_and_exact_scores(make_baseline):
    b = make_baseline(); result = evaluate(b)
    assert north_outputs(b) == [8.,8.,8.,8.]
    assert b['state']['reads'] == [0,8]
    # Held-out north actuals [4,8,16,32], prior actual at O is zero.
    sid = next(s.series_id for s in b['metadata'][0].series if s.dimensions['location'] == 'north')
    events = [e for e in result.events if e.scope.series_id == sid]
    assert [e.statistics.sae.fraction for e in events] == [Fraction(4),Fraction(8),Fraction(16),Fraction(32)]
    assert sum(e.statistics.sse.fraction for e in events) == 1360
    assert [e.scope.target_period for e in events] == [b['at'](n) for n in range(8,12)]
    print('LAST_OBSERVATION expected=[8,8,8,8] observed=', north_outputs(b))


@pytest.mark.parametrize('season,horizon,expected',[(2,1,[0.]),(2,2,[0.,8.]),(2,4,[0.,8.,0.,8.]),
    (1,4,[8.,8.,8.,8.]),(4,2,[2.,-4.]),(3,4,[-4.,0.,8.,-4.])])
def test_seasonal_hand_calculated_short_equal_long_horizons(make_baseline, season, horizon, expected):
    b = make_baseline(baseline=definition('seasonal_naive_v1',season), horizon=horizon)
    evaluate(b); assert north_outputs(b) == expected
    print('SEASONAL',season,horizon,'expected=',expected,'observed=',north_outputs(b))


@pytest.mark.parametrize('season',[True,False,2.,2.5,0,-1,4097,'2',None])
def test_season_length_invalid_types_and_bounds(season):
    with pytest.raises(ValueError): definition('seasonal_naive_v1',season)


@pytest.mark.parametrize('changes',[{'algorithm':'unknown_v1'},{'version':'2.0'},{'season_length':2},
    {'lookback':True},{'lookback':0},{'lookback':4097},{'supported_horizon':0},{'supported_horizon':1.5},
    {'predictor_id':'caller'}, {'authority_id':'caller'}])
def test_invalid_baseline_contract(changes):
    data=definition().model_dump();data.update(changes)
    with pytest.raises(ValueError): BaselineDefinition.model_validate(data)


def test_immutable_distinct_algorithm_and_season_identities():
    a=definition();b=definition('seasonal_naive_v1',1);c=definition('seasonal_naive_v1',2)
    assert len({a.digest,b.digest,c.digest}) == 3
    assert a.period_lag == b.period_lag == 1 and c.period_lag == 2
    with pytest.raises(ValueError): definition('seasonal_naive_v1',5)
    with pytest.raises(Exception): a.lookback=99


@pytest.mark.parametrize('fault',['history_budget','horizon_budget','row_basis','warmup','anchor','extra_metadata'])
def test_factory_refuses_contradictory_semantics(make_baseline, fault):
    b=make_baseline();cfg=b['template'];budgets=FeatureBudgets()
    if fault=='history_budget':budgets=replace(budgets,max_history_periods=3)
    elif fault=='horizon_budget':budgets=replace(budgets,max_horizon=3)
    elif fault=='extra_metadata':cfg=cfg.model_copy(update={'authority_id':'request'})
    else:
        d=cfg.model_dump();t=d['execution']['temporal']
        if fault=='row_basis':t.update(lag_basis='observation',gap_policy='allow_observation_gaps')
        elif fault=='warmup':t['cold_start']='configured_missing'
        else:t['period_anchor']='end'
        # For anchor changes the old calendar calculations are absent in this fixture.
        cfg=FeatureConfiguration.model_validate(d)
    with pytest.raises(BaselineConfigurationError):build_baseline_profile(b['baseline'],cfg,feature_budgets=budgets)
    assert not b['state']['reads'] and not b['state']['calls']


@pytest.mark.parametrize('kind,period',[('last_observation_v1',3),('seasonal_naive_v1',2),('seasonal_naive_v1',1)])
def test_missing_predecessor_season_reference_and_calendar_gap(make_baseline,kind,period):
    def remove(raw):raw.drop(raw.index[raw['from']==pd.Timestamp('2020-01-06',tz='UTC')+pd.DateOffset(days=7*period)],inplace=True);raw.reset_index(drop=True,inplace=True)
    b=make_baseline(baseline=definition(kind,2 if kind=='seasonal_naive_v1' else None),raw_change=remove)
    with pytest.raises(BaselineCompositionError):evaluate(b)
    assert not b['state']['calls']


@pytest.mark.parametrize('fault',['800','late','missing','backdated'])
def test_value_attestation_is_mandatory_before_any_baseline_prediction(make_baseline,fault):
    b=make_baseline();proof=b['proof'];key=next(k for k,v in proof['ledger'].items() if v['period_start']==b['at'](3))
    if fault=='missing':del proof['ledger'][key]
    elif fault in ('late','backdated'):proof['ledger'][key]['revision_available']=b['at'](5)
    else:
        old=b['provider'].open_read
        @contextmanager
        def substituted(*a,**kw):
            with old(*a,**kw) as session:
                batches=session.batches
                def stream():
                    for frame in batches():
                        frame=frame.copy(deep=True);frame.loc[frame.period_start==b['at'](3),'value']=800.;yield frame
                session.batches=stream;yield session
        b['provider'].open_read=substituted
    with pytest.raises(BaselineCompositionError):evaluate(b)
    assert not b['state']['calls'] and b['state']['reads']==[0]
    if fault=='800':
        claims=[c for _,cs in proof['seen'] for c in cs if c.availability.period_start==b['at'](3)]
        assert claims[0].availability.value_identity==('float64','4089000000000000')
        print('SUBSTITUTED800 recomputed identity rejected before baseline callback')


@pytest.mark.parametrize('argument',['release_verifier','value_authority','availability_verifier','provider','predictor',
    'evaluator','configuration','capabilities','baseline_definitions','events','trusted'])
def test_request_cannot_register_authorities_producers_or_events(make_baseline,argument):
    b=make_baseline();called=[]
    with pytest.raises(TypeError): evaluate(b,**{argument:lambda *_:called.append(1) or True})
    assert not called and not b['state']['reads']


@pytest.mark.parametrize('role',['release_authority','value_authority','label_revision_authority'])
def test_missing_operator_authority_fails_before_capture(make_baseline,role):
    b=make_baseline();opts={**b['options'],role:None}
    with pytest.raises(BaselineCompositionError):CanonicalBaselineEvaluationService(**opts)
    assert not b['state']['reads']


@pytest.mark.parametrize('fault',['algorithm','season','horizon','capability','numeric','configuration','extra_metadata'])
def test_unapproved_or_fabricated_declaration_cannot_select_profile(make_baseline,fault):
    b=make_baseline();d=b['baseline'];plan=b['plan']
    if fault in ('algorithm','season'):d=definition('seasonal_naive_v1',1 if fault=='algorithm' else 2)
    elif fault=='horizon':d=definition(horizon=3)
    elif fault=='extra_metadata':d=d.model_copy(update={'verify':lambda *_:True})
    else:
        data=plan.model_dump()
        if fault=='configuration':data['configuration_digest']='0'*64
        elif fault=='numeric':data['predictor']['numeric_policy_id']='0'*64
        else:data['predictor']['predictor_id']='fabricated-producer'
        plan=ForecastEvaluationPlan.model_validate(data)
    with pytest.raises(BaselineCompositionError):b['composition'].evaluate_baseline(d,b['scoring'],plan)
    assert not b['state']['reads'] and not b['state']['calls']


def test_future_actuals_do_not_change_original_origin_baseline(make_baseline):
    a=make_baseline();evaluate(a);first=north_outputs(a)
    def change(raw):raw.loc[raw['from']>=pd.Timestamp('2020-02-03',tz='UTC'),'reported']=999999999.
    b=make_baseline(raw_change=change);evaluate(b)
    assert north_outputs(b)==first==[8.,8.,8.,8.]
    assert a['plan'].datasets != b['plan'].datasets  # Immutable content IDs must differ.


@pytest.mark.parametrize('value',[-8.,0.,-0.])
def test_signed_zero_and_negative_feedback_remain_exact(make_baseline,value):
    def raw(frame):frame.loc[frame['from']==pd.Timestamp('2020-01-27',tz='UTC'),'reported']=value
    b=make_baseline(raw_change=raw);evaluate(b)
    assert north_outputs(b)==[value]*4
    assert all(math.copysign(1,v)==math.copysign(1,value) for v in north_outputs(b))


@pytest.mark.parametrize('frequency,timezone,start',[('weekly','UTC','2020-12-07'),
    ('daily','UTC','2020-02-25'),('daily','America/New_York','2020-03-04')])
def test_calendar_iso_week53_leap_day_and_local_dst(make_baseline,frequency,timezone,start):
    b=make_baseline(baseline=definition('seasonal_naive_v1',2),frequency=frequency,timezone=timezone,start=start)
    evaluate(b);calls=[r for r in b['state']['calls'] if r.compatibility.forecast_origin==b['at'](4)]
    assert north_outputs(b)==[0.,8.,0.,8.]
    assert [pd.Timestamp(r.scopes[0].target_period) for r in calls]==[b['at'](i) for i in range(4,8)]
    if timezone!='UTC':assert (b['at'](5)-b['at'](4)).total_seconds()==23*3600
    if frequency=='weekly':assert b['at'](3).isocalendar().week==53
    if start=='2020-02-25':assert b['at'](4).day==29


def test_weekly_season52_is_calendar_periods_not_same_iso_week(make_baseline):
    b=make_baseline(baseline=definition('seasonal_naive_v1',52,lookback=52),origins=(53,57),
        periods=62,start='2019-12-30')
    evaluate(b);assert north_outputs(b,b['at'](53))==[-4.,0.,8.,16.]
    assert b['at'](53)==pd.Timestamp('2021-01-04',tz='UTC')
    assert b['at'](1).isocalendar().week==2 and b['at'](53).isocalendar().week==1


@pytest.mark.parametrize('fault',['missing','extra','duplicate','scope','request_id','nan','bool','int'])
def test_keyed_outputs_remain_verified_by_owned_b1(make_baseline,fault):
    b=make_baseline()
    def malformed(req,out):
        points=list(out.points)
        if fault=='missing':points.pop()
        elif fault=='extra':points.append(points[0])
        elif fault=='duplicate':points[1]=points[0]
        elif fault=='scope':points[0]=points[0].model_copy(update={'scope':points[0].scope.model_copy(update={'organization_id':UUID(int=99)})})
        elif fault=='request_id':return out.model_copy(update={'request_id':'0'*64})
        else:points[0]=PredictionPoint.model_construct(scope=points[0].scope,value={'nan':float('nan'),'bool':True,'int':8}[fault])
        return out.model_copy(update={'points':tuple(points)})
    b['prediction_hook']=malformed
    with pytest.raises(BaselineCompositionError):evaluate(b)
    assert len(b['state']['calls'])==1 and b['state']['reads']==[0]


@pytest.mark.parametrize('permission',['labels','revision','evaluation','read'])
def test_separate_label_revision_evaluation_and_read_permissions(make_baseline,permission):
    b=make_baseline();b['permissions'][permission]=False
    with pytest.raises(BaselineCompositionError):evaluate(b)
    if permission in ('evaluation','read'):assert not b['state']['calls']
    else:assert len(b['state']['calls'])==8


@pytest.mark.parametrize('trigger',['global_value','global_permission','read_permission','last_scoring'])
def test_final_revocation_keeps_original_scopes_and_returns_nothing(make_baseline,trigger):
    b=make_baseline();target=(UUID(int=2),'fold-1',b['at'](8))
    if trigger=='global_value':b['global_hook']=lambda:b['proof']['revoked'].add(target)
    elif trigger=='global_permission':b['global_hook']=lambda:b['revoked'].add(target)
    elif trigger=='read_permission':b['read_hook']=lambda:b['revoked'].add(target) if b['state']['final'] else None
    else:
        def revoke(ctx,stage):
            if stage=='return':b['permissions']['labels']=False
        b['label_hook']=revoke
    with pytest.raises(BaselineCompositionError):evaluate(b)
    assert len(b['state']['calls'])==8
    if trigger!='last_scoring':assert b['state']['trace'][-1][0] in ('rollout','publication','evaluation')


@pytest.mark.parametrize('fault',['cleanup','late_read','cancel_predict','deadline_predict','cancel_final','deadline_final','secret'])
def test_lifecycle_cancellation_deadline_and_sanitized_errors(make_baseline,fault):
    b=make_baseline()
    if fault in ('cleanup','late_read'):
        opened=b['provider'].open_read
        @contextmanager
        def read(*a,**kw):
            with opened(*a,**kw) as session:
                if fault=='late_read':
                    batches=session.batches
                    def stream():
                        for n,frame in enumerate(batches()):
                            if n==2:raise OSError('BASELINE-SYNTHETIC-SECRET')
                            yield frame
                    session.batches=stream
                yield session
            if fault=='cleanup':raise OSError('BASELINE-SYNTHETIC-SECRET')
        b['provider'].open_read=read
    elif fault in ('cancel_predict','deadline_predict'):
        def trigger(req,out):
            b['state']['cancelled']=fault=='cancel_predict'
            b['state']['clock']=300. if fault=='deadline_predict' else 0.
            return out
        b['prediction_hook']=trigger
    else:
        def final(ctx,stage):
            if stage=='return':
                if fault=='cancel_final':b['state']['cancelled']=True
                elif fault=='deadline_final':b['state']['clock']=300.
                else:raise BaselineCompositionError('BASELINE-SYNTHETIC-SECRET')
        b['label_hook']=final
    with pytest.raises(BaselineCompositionError) as info:evaluate(b,cancelled=lambda:b['state']['cancelled'])
    assert 'BASELINE-SYNTHETIC-SECRET' not in ''.join(traceback.format_exception(info.value))
    with b['store']._publication_session(StagingBudgets()):pass
    if fault in ('cleanup','late_read'):assert not b['state']['calls']


def test_detached_profile_output_and_original_decimal_preservation(make_baseline):
    b=make_baseline(decimal=True);raw=tuple(v.as_tuple() for v in b['raws'][2].reported)
    out=evaluate(b);before=out.evaluation_id
    matrix=b['state']['calls'][0].matrix;matrix.iloc[:,:]=999.
    assert out.evaluation_id==before and north_outputs(b)==[8.,8.,8.,8.]
    assert tuple(v.as_tuple() for v in b['raws'][2].reported)==raw
    with pytest.raises(FrozenInstanceError):b['profile'].configuration_json='{}'
    with pytest.raises(Exception):out.events[0].scope.metric='other'


@pytest.mark.parametrize('bounded',[False,True])
def test_numeric_conversion_is_explicit_operator_owned_and_schema_bound(make_baseline,bounded):
    def raw(frame):frame.loc[frame['from']==pd.Timestamp('2020-01-27',tz='UTC'),'reported']=Decimal('0.1')
    def policy(c):
        c['execution'].update(numeric_conversion='versioned_float64', numeric_policies=[dict(field='value',
            measurement=c['execution']['measurement'],mode='BOUNDED_FLOAT64',absolute_tolerance='0.0000000000000001')])
    b=make_baseline(decimal=True,raw_change=raw,template_change=policy if bounded else None)
    if bounded:
        evaluate(b);assert north_outputs(b)==[0.1]*4
        assert b['profile'].configuration.execution.numeric_policies[0].absolute_tolerance==Decimal('1e-16')
    else:
        with pytest.raises(BaselineCompositionError):evaluate(b)
        assert not b['state']['calls']


def test_zero_denominator_wape_is_undefined_and_exact(make_baseline):
    def raw(frame):
        frame.loc[frame['from']==pd.Timestamp('2020-02-24',tz='UTC'),'reported']=8.
        frame.loc[frame['from']>=pd.Timestamp('2020-03-02',tz='UTC'),'reported']=0.
    b=make_baseline(raw_change=raw);out=evaluate(b)
    assert all(e.statistics.sae.fraction==8 and e.statistics.sse.fraction==64 for e in out.events)
    assert all(e.statistics.wape is None and e.statistics.wape_status=='undefined_zero_denominator' for e in out.events)
    assert all(g.statistics.saa.fraction==0 and g.statistics.wape is None for g in out.groups)


def test_two_approved_profiles_dispatch_without_reconfiguring_authorities(make_baseline):
    b=make_baseline();second=definition('seasonal_naive_v1',2)
    service=CanonicalBaselineEvaluationService(**{**b['options'],'baseline_definitions':(b['baseline'],second)})
    first=service.evaluate_baseline(b['baseline'],b['scoring'],b['plan'])
    assert first.events and north_outputs(b)==[8.]*4
    b['state']['calls'].clear();b['state']['reads'].clear();b['state']['final']=False
    profile=build_baseline_profile(second,b['template']);cfg=profile.configuration
    b['plan']=ForecastEvaluationPlan.model_validate({**b['plan'].model_dump(),'predictor':profile.capabilities,
        'configuration_id':cfg.configuration_id,'configuration_version':cfg.version,'configuration_digest':CanonicalFeaturePipeline(cfg).digest})
    b['scoring']=ForecastScoringPlan.model_validate({**b['scoring'].model_dump(),'producer_plan_digest':b['plan'].digest})
    b['approved'].add(b['scoring'].digest)
    out=service.evaluate_baseline(second,b['scoring'],b['plan']);assert out.events and north_outputs(b)==[0.,8.,0.,8.]
    assert out.producer_result_id != first.producer_result_id
    # Old plan/capability cannot claim the new algorithm without exact bindings.
    with pytest.raises(BaselineCompositionError):service.evaluate_baseline(b['baseline'],b['scoring'],b['plan'])


def test_multiple_organizations_and_fold_states_do_not_share_feedback(make_baseline):
    b=make_baseline(horizon=2,organizations=(2,3));first=b['plan'].folds[0].model_copy(update={'validation_origins':()})
    second=TemporalFold(fold_id='fold-2',training_origins=(b['at'](5),),validation_origins=(b['at'](9),),
        calibration_start=b['at'](0),categorical_cutoff=b['at'](4),model_fit_cutoff=b['at'](7),assessment_cutoff=b['at'](11))
    b['plan']=ForecastEvaluationPlan.model_validate({**b['plan'].model_dump(),'folds':(first,second)})
    b['scoring']=ForecastScoringPlan.model_validate({**b['scoring'].model_dump(),'producer_plan_digest':b['plan'].digest})
    b['approved'].add(b['scoring'].digest)
    out=evaluate(b);assert len(out.events)==8 and b['state']['reads']==[0,0,12,12]
    states={}
    for req in b['state']['calls']:
        key=(req.compatibility.fold_id,req.compatibility.organization_id)
        states.setdefault(key,set()).add(req.compatibility.state_id)
        assert all(s.organization_id==req.compatibility.organization_id for s in req.scopes)
        for row,scope in zip(req.rows,req.scopes):
            raw=b['raws'][scope.organization_id.int]
            location=dict(scope.dimensions)['location']
            actual=raw[(raw.branch==location)&(raw['to']==pd.Timestamp(scope.forecast_origin))].reported.iloc[0]
            assert row[0]==actual
    assert len(states)==4 and len({next(iter(s)) for s in states.values()})==4


def test_active_concurrent_baseline_request_rejects_without_leakage(make_baseline):
    b=make_baseline();entered=Event();release=Event()
    def pause(req,out):
        if not entered.is_set():entered.set();assert release.wait(30)
        return out
    b['prediction_hook']=pause
    with ThreadPoolExecutor(max_workers=1) as executor:
        current=executor.submit(evaluate,b);assert entered.wait(30)
        try:
            with pytest.raises(BaselineCompositionError):evaluate(b)
        finally:release.set()
        assert current.result(30).events
    assert b['state']['reads']==[0,8]


def test_sequential_calls_recheck_original_claims_and_current_permissions(make_baseline):
    b=make_baseline();first=evaluate(b)
    claims=[cs for c,cs in b['proof']['seen'] if c.target_period_end is not None and c.forecast_origin==b['at'](8)]
    assert len(claims)==3
    assert all(c.availability is claims[0][i].availability for cs in claims for i,c in enumerate(cs))
    b['state']['calls'].clear();b['state']['reads'].clear();b['state']['final']=False
    second=evaluate(b);assert second.model_dump()==first.model_dump()
    b['state']['calls'].clear();b['state']['reads'].clear();b['proof']['revoked'].add((UUID(int=2),'fold-1',b['at'](8)))
    with pytest.raises(BaselineCompositionError):evaluate(b)
    assert b['state']['reads']==[0]


@pytest.mark.parametrize('fault',['names','dtype','period','origin','fold','organization','prefix','schema','numeric','nan','bool'])
def test_adapter_independently_validates_bound_representation(make_baseline,fault):
    b=make_baseline();evaluate(b);req=b['state']['calls'][0]
    updates={}
    if fault=='names':updates['feature_names']=('not-the-lag',)
    elif fault=='dtype':updates['feature_dtypes']=('int64',)
    elif fault in ('period','origin','fold','organization'):
        key,value={'period':('target_period',b['at'](5)), 'origin':('forecast_origin',b['at'](3)),
            'fold':('fold_id','other-fold'), 'organization':('organization_id',UUID(int=9))}[fault]
        updates['scopes']=(req.scopes[0].model_copy(update={key:value}),)+req.scopes[1:]
    elif fault=='prefix':updates['prefix_length']=2
    elif fault=='schema':updates['prefix_schema_id']='0'*64
    elif fault=='numeric':updates['capabilities']=req.capabilities.model_copy(update={'numeric_policy_id':'0'*64})
    else:updates['rows']=((float('nan') if fault=='nan' else True,),)+req.rows[1:]
    forged=req.model_copy(update=updates)
    with pytest.raises(BaselineConfigurationError):b['adapter_call'](b['profile'],forged)


def test_factory_semantics_are_bound_beyond_the_output_column_name(make_baseline):
    b=make_baseline();evaluate(b);cfg=b['profile'].configuration.model_dump()
    cfg['lag_offsets']=(2,);cfg['calculations'][0]['parameters']={'offset':2}
    changed=FeatureConfiguration.model_validate(cfg)
    forged=replace(b['profile'],configuration_json=changed.model_dump_json())
    with pytest.raises(BaselineConfigurationError):b['adapter_call'](forged,b['state']['calls'][0])


def test_real_fold_state_fitting_happens_after_reader_close(make_baseline,monkeypatch):
    b=make_baseline();fits=[];fit=CanonicalFeaturePipeline.fit_state
    def record(self,frame,**kw):
        assert not b['state']['active']
        with b['store']._publication_session(StagingBudgets()):pass
        result=fit(self,frame,**kw);fits.append((kw,result));return result
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',record)
    evaluate(b);assert len(fits)==1
    args,state=fits[0]
    assert state.training_cutoff==b['at'](4) and args['partition_reference']==state.training_partition_reference
    assert all(r.compatibility.state_id==state.state_id for r in b['state']['calls'])
    assert state.configuration_digest==b['plan'].configuration_digest
    assert state.vocabularies=={}  # Lag-only baseline has no categorical outputs to fit.


@pytest.mark.parametrize('budget',['producer_work','scoring_work','raised_ceiling'])
def test_baseline_request_budgets_fail_closed_without_partial_output(make_baseline,budget):
    b=make_baseline()
    if budget in ('producer_work','raised_ceiling'):
        data=b['plan'].model_dump()
        data['budgets']['max_work_cells' if budget=='producer_work' else 'max_elapsed_seconds']=1 if budget=='producer_work' else 301
        b['plan']=ForecastEvaluationPlan.model_validate(data)
        b['scoring']=ForecastScoringPlan.model_validate({**b['scoring'].model_dump(),'producer_plan_digest':b['plan'].digest})
    else:
        data=b['scoring'].model_dump();data['budgets']['max_work_cells']=1
        b['scoring']=ForecastScoringPlan.model_validate(data)
    b['approved'].add(b['scoring'].digest)
    with pytest.raises(BaselineCompositionError):evaluate(b)
    assert len(b['state']['calls'])==(8 if budget=='scoring_work' else 0)


def test_later_tenant_revocation_after_prediction_discards_combined_result(make_baseline):
    b=make_baseline(organizations=(2,3));target=(UUID(int=3),'fold-1',b['at'](8))
    b['global_hook']=lambda:b['proof']['revoked'].add(target)
    with pytest.raises(BaselineCompositionError):evaluate(b)
    assert b['state']['reads']==[0,0,16,16] and len(b['state']['calls'])==16
    context,claims=b['proof']['seen'][-1]
    assert (context.metadata.reference.organization_id,context.fold_id,context.forecast_origin)==target
    assert all(c.organization_id==UUID(int=3) for c in claims)


def test_final_elapsed_just_below_budget_still_succeeds(make_baseline):
    b=make_baseline()
    def final(ctx,stage):
        if stage=='return':b['state']['clock']=299.999
    b['label_hook']=final
    assert evaluate(b).events and b['state']['clock']==299.999


def test_late_label_capture_failure_returns_no_partial_scores(make_baseline):
    b=make_baseline();opened=b['provider'].open_read;count=[0]
    @contextmanager
    def read(*a,**kw):
        count[0]+=1
        with opened(*a,**kw) as session:
            if count[0]==2:
                batches=session.batches
                def late():
                    for i,frame in enumerate(batches()):
                        if i==2:raise OSError('BASELINE-LATE-LABEL-SECRET')
                        yield frame
                session.batches=late
            yield session
    b['provider'].open_read=read
    with pytest.raises(BaselineCompositionError) as info:evaluate(b)
    assert count[0]==2 and len(b['state']['calls'])==8
    assert 'BASELINE-LATE-LABEL-SECRET' not in ''.join(traceback.format_exception(info.value))
    with b['store']._publication_session(StagingBudgets()):pass


def test_last_horizon_failure_never_exposes_partial_producer_or_labels(make_baseline):
    b=make_baseline()
    def fail_last(req,out):
        if req.compatibility.forecast_origin==b['at'](8) and req.prefix_length==4:
            raise OSError('BASELINE-LAST-HORIZON-SECRET')
        return out
    b['prediction_hook']=fail_last
    with pytest.raises(BaselineCompositionError) as info:evaluate(b)
    assert len(b['state']['calls'])==8 and b['state']['reads']==[0]
    assert 'BASELINE-LAST-HORIZON-SECRET' not in ''.join(traceback.format_exception(info.value))


def test_terminal_callback_trace_and_read_lock_release(make_baseline):
    b=make_baseline();original=b['options']['value_authority']
    def verify(ctx,claims):
        assert not b['state']['active']
        with b['store']._publication_session(StagingBudgets()):pass
        b['state']['trace'].append(('value',ctx.fold_id,ctx.forecast_origin,ctx.target_period_end))
        return original.verify(ctx,claims)
    b['composition']=CanonicalBaselineEvaluationService(**{**b['options'],
        'value_authority':replace(original,verify=verify)})
    evaluate(b);trace=b['state']['trace']
    global_index=trace.index(('evaluation','return',None,None));terminal=trace[global_index+1:]
    assert terminal[0]==('publication',True)
    assert [t[0] for t in terminal[1:]]==['value','rollout','value','rollout','revision','labels','evaluation']
    assert terminal[1][2]==b['at'](4) and terminal[3][2]==b['at'](8)
    assert terminal[-1]==('evaluation','return','fold-1',b['at'](8))
    print('BASELINE TERMINAL TRACE',trace[global_index:])


def test_factory_rejects_unused_exogenous_template_instead_of_ignoring_evidence(make_baseline):
    b=make_baseline();data=b['template'].model_dump()
    data['canonical_inputs']=(*data['canonical_inputs'],'external_value')
    data['execution']['exogenous']=(dict(field='external_value',availability='known_in_advance',missing='reject',
        availability_field='external_release',evidence_field='external_evidence'),)
    cfg=FeatureConfiguration.model_validate(data)
    with pytest.raises(BaselineConfigurationError):build_baseline_profile(b['baseline'],cfg)
    assert not b['state']['reads']


def test_season_bound_is_checked_against_actual_configured_history_budget(make_baseline):
    b=make_baseline();d=definition('seasonal_naive_v1',5,lookback=5)
    with pytest.raises(BaselineConfigurationError):
        build_baseline_profile(d,b['template'],feature_budgets=FeatureBudgets(max_history_periods=4))
    assert not b['state']['reads']


def test_maximum_season_declaration_does_not_grant_actual_history_or_resources():
    d=definition('seasonal_naive_v1',4096,lookback=4096)
    assert d.period_lag==4096  # Execution still requires actual eligible periods and resource budgets.
