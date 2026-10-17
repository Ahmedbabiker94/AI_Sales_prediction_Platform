"""C1 synthetic admitted publications, independent ledgers, no scoring/models."""
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
import hashlib
import math
import runpy
import struct
import traceback
from uuid import UUID

import pandas as pd
import pytest

from src.data.contracts.backtesting import ForecastEvaluationPlan, PredictorCapabilities, PredictionBatch, PredictionPoint
from src.data.contracts.features import FeatureConfiguration
from src.data.contracts.forecast_evaluation import BaselineDefinition
from src.data.contracts.forecast_comparison import JointPredictionPlan, JointProducerPlan, JointPredictionBudgets
from src.data.contracts.temporal_preparation import TemporalFold
from src.data.contracts.dataset import StagingBudgets
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline, FeatureBudgets
from src.features.forecast_baselines import build_baseline_profile, BaselineProfile
from src.services.canonical_baseline_evaluation_service import BaselineAuthorityBinding
from src.services.canonical_forecast_comparison_service import CanonicalForecastComparisonService, JointPredictionError
from src.services.deterministic_forecast_rollout_service import representation_schema_id, numeric_policy_id


@pytest.fixture
def make_joint(tmp_path,monkeypatch):
    reference=runpy.run_path('tests/training/test_deterministic_forecast_rollout.py')
    factory=reference['make_rollout'].__wrapped__(tmp_path)
    baseline_call=BaselineProfile.__call__
    def make(*,season=None,organizations=(2,),decimal=False,frequency='weekly',timezone='UTC',start='2020-01-06',
             raw_change=None,categories=False,horizon=4,origins=(4,8,9,10),exo=False,bounded=False,external=False):
        b=factory(organizations=organizations,decimal=decimal,frequency=frequency,timezone=timezone,start=start,
                  raw_change=raw_change,exo=exo)
        template=b['configuration'];data=template.model_dump()
        if not categories:
            data['ordered_outputs']=tuple(x for x in data['ordered_outputs'] if x!='location')
            data['missing_value_behavior'].pop('location');data['output_dtypes'].pop('location')
            data['category_encoding'].pop('location');data['execution']['categories']=tuple(c for c in data['execution']['categories'] if c['field']!='location')
        if bounded:
            # Explicit SYNTHETIC tolerances, never production business approval.
            def policy(c):
                c['execution'].update(numeric_conversion='versioned_float64',numeric_policies=[dict(field='value',
                    measurement=c['execution']['measurement'],mode='BOUNDED_FLOAT64',absolute_tolerance='0.0000000000000001')])
            policy(data);t=template.model_dump();policy(t);template=FeatureConfiguration.model_validate(t)
        if external:data['execution']['exogenous'][0].update(availability='external_forecast',offset=None)
        cfg=FeatureConfiguration.model_validate(data)
        if exo:
            noexo=template.model_dump();noexo['canonical_inputs']=tuple(x for x in noexo['canonical_inputs'] if x!='promotion')
            noexo['ordered_outputs']=tuple(x for x in noexo['ordered_outputs'] if x!='promotion')
            for name in ('category_encoding','output_dtypes','missing_value_behavior'):noexo[name].pop('promotion',None)
            noexo['execution']['categories']=tuple(c for c in noexo['execution']['categories'] if c['field']!='promotion')
            noexo['execution']['exogenous']=();template=FeatureConfiguration.model_validate(noexo)
        cap=PredictorCapabilities(predictor_id='approved-candidate',predictor_version='1.0',strategy='recursive_one_step',
            representation_kind='tabular_step',representation_schema_id=representation_schema_id(cfg),numeric_policy_id=numeric_policy_id(cfg),
            lookback=4,supported_horizon=4,allowed_covariates=tuple(sorted(r.field for r in cfg.execution.exogenous)))
        definition=BaselineDefinition(algorithm='last_observation_v1' if season is None else 'seasonal_naive_v1',
            season_length=season,lookback=4,supported_horizon=4)
        baseline=build_baseline_profile(definition,template)
        fold=TemporalFold(fold_id='fold-1',training_origins=(b['at'](origins[0]),),
            validation_origins=tuple(b['at'](n) for n in origins[1:]),calibration_start=b['at'](0),
            categorical_cutoff=b['at'](4),model_fit_cutoff=b['at'](origins[0]+horizon),assessment_cutoff=b['at'](origins[-1]+horizon))
        def plan(config,capability):
            return ForecastEvaluationPlan.model_validate({**b['plan'].model_dump(),'predictor':capability,
                'configuration_id':config.configuration_id,'configuration_version':config.version,
                'configuration_digest':CanonicalFeaturePipeline(config).digest,'folds':(fold,),'forecast_horizon':horizon})
        cp=plan(cfg,cap);bp=plan(baseline.configuration,baseline.capabilities)
        jp=JointPredictionPlan(plan_id='joint-synthetic',producers=(JointProducerPlan(role='candidate',profile_id=cap.predictor_id,plan=cp),
            JointProducerPlan(role='baseline',profile_id=baseline.capabilities.predictor_id,plan=bp)))
        ledger={}
        for meta in b['metadata']:
            raw=b['raws'][meta.reference.organization_id.int]
            with b['provider'].open_read(meta.reference,purpose=meta.purpose) as session:
                for frame in session.batches():
                    for row in frame.to_dict('records'):
                        truth=raw.iloc[int(row['source_record_id'])];value=truth.reported
                        if type(value) is Decimal:
                            t=value.as_tuple();value_id=('decimal',t.sign,tuple(t.digits),t.exponent)
                        else:value_id=('float64',struct.pack('>d',float(value)).hex())
                        ledger[meta.reference.organization_id,row['source_record_id']]=dict(metadata=meta,
                            series=row['series_id'],lineage=row['lineage_id'],start=truth['from'],end=truth['to'],
                            released=truth.released,evidence=truth.proof,value=value_id,revision=truth.released,
                            category_release=truth.promo_release,category_evidence=truth.promo_proof,category_value=truth.offer)
        state=dict(calls=[],reads=[],active=False,clock=0.,cancelled=False,checks=[],trace=[])
        b.update(configuration=cfg,baseline=baseline,definition=definition,plan=jp,ledger=ledger,state=state,revoked=set())
        def expected(ctx):
            producer=next((p for p in b['plan'].producers if p.profile_id==ctx.producer_profile_id),None)
            return producer is not None and ctx.joint_plan_digest==b['plan'].digest and ctx.plan_digest==producer.plan.digest
        def release(ctx,claims):
            assert not state['active'];state['trace'].append(('release',ctx.producer_profile_id,ctx.fold_id,ctx.forecast_origin))
            if not expected(ctx):return False
            if hook:=b.get('release_hook'):hook(ctx,claims)
            for c in claims:
                t=ledger.get((ctx.metadata.reference.organization_id,c.source_record_id))
                if (t is None or c.series_id!=t['series'] or c.lineage_id!=t['lineage'] or c.period_start!=t['start'] or c.period_end!=t['end']
                        or c.available_at!=t['released' if c.field=='value' else 'category_release']
                        or c.evidence_reference!=t['evidence' if c.field=='value' else 'category_evidence']):return False
            return True
        def values(ctx,claims):
            assert not state['active'];state['checks'].append((ctx,claims))
            if not expected(ctx):return False
            if hook:=b.get('value_hook'):hook(ctx,claims)
            if (ctx.producer_profile_id,ctx.metadata.reference.organization_id,ctx.fold_id,ctx.forecast_origin) in b['revoked']:return False
            producer=next(p for p in b['plan'].producers if p.profile_id==ctx.producer_profile_id)
            for c in claims:
                a=c.availability;t=ledger.get((c.organization_id,a.source_record_id))
                if t is None:return False
                m=t['metadata'];mapping=m.mapping
                if (c.dataset_reference!=m.reference or ctx.metadata!=m or c.manifest_digest!=m.manifest_digest or c.admission_digest!=m.admission_digest
                        or c.source_id!=m.source_id or c.mapping_digest!=hashlib.sha256(m.mapping_json.encode()).hexdigest()
                        or (c.mapping_id,c.mapping_version)!=(mapping.mapping_id,mapping.version)
                        or (c.metric,c.unit,c.currency)!=(mapping.target_metric,mapping.measurement.unit,mapping.measurement.currency)
                        or c.series_ids!=ctx.series_ids or c.plan_digest!=producer.plan.digest or c.fold_id!=ctx.fold_id
                        or c.forecast_origin!=ctx.forecast_origin or c.calibration_cutoff!=ctx.training_cutoff
                        or c.target_period_end!=ctx.target_period_end or a.value_identity!=t['value']
                        or c.purpose!='offline_forecast_evaluation'):return False
                for cutoff in (ctx.forecast_origin,ctx.training_cutoff):
                    if cutoff is not None and a.period_end<=cutoff and a.available_at<=cutoff and t['revision']>cutoff:return False
            return True
        def authorize(ctx,stage):
            state['trace'].append(('rollout',ctx.producer_profile_id,ctx.fold_id,ctx.forecast_origin,stage))
            if hook:=b.get('authorization_hook'):hook(ctx,stage)
            return expected(ctx) and (ctx.producer_profile_id,ctx.metadata.reference.organization_id,ctx.fold_id,ctx.forecast_origin) not in b['revoked']
        def joint(ctx,stage):
            state['trace'].append(('joint',stage))
            if hook:=b.get('global_hook'):hook(ctx,stage)
            return ctx.plan_digest==b['plan'].digest
        def candidate(req):
            assert not state['active']
            assert len(state['reads'])==len(organizations)  # C1 never opens scoring capture.
            with b['store']._publication_session(StagingBudgets()):pass
            state['calls'].append(('candidate',req))
            result=PredictionBatch(request_id=req.request_id,points=tuple(PredictionPoint(scope=s,value=[-2.,0.,6.,1.][req.prefix_length-1]) for s in req.scopes))
            if hook:=b.get('prediction_hook'):return hook('candidate',req,result)
            return result
        def baseline_spy(self,req):
            assert not state['active'] and len(state['reads'])==len(organizations)
            state['calls'].append(('baseline',req));result=baseline_call(self,req)
            if hook:=b.get('prediction_hook'):return hook('baseline',req,result)
            return result
        monkeypatch.setattr(BaselineProfile,'__call__',baseline_spy)
        opened=b['provider'].open_read
        @contextmanager
        def read(*args,**kwargs):
            state['reads'].append(len(state['calls']));state['active']=True
            try:
                with opened(*args,**kwargs) as session:yield session
            finally:state['active']=False
        b['provider'].open_read=read
        def external_authority(ctx,claim):
            assert not state['active'] and expected(ctx)
            state['trace'].append(('external',ctx.producer_profile_id,ctx.forecast_origin))
            if hook:=b.get('external_hook'):
                if hook(ctx,claim) is not True:return False
            truth=next((v for (org,record),v in ledger.items() if org==claim.organization_id and v['series']==claim.series_id
                and v['start']==claim.period_start),None)
            return (truth is not None and claim.value==truth['category_value'] and claim.available_at==truth['category_release']
                and claim.evidence_reference==truth['category_evidence'] and claim.forecast_origin==ctx.forecast_origin
                and claim.configuration_digest==ctx.configuration_digest)
        def authority(name,protocol,callback):return BaselineAuthorityBinding(name,'synthetic-1',protocol,cp.datasets,callback)
        options=dict(provider=b['provider'],candidate_configuration=cfg,candidate_capabilities=cap,candidate_predictor=candidate,
            baseline_template_configuration=template,baseline_definitions=(definition,),
            release_authority=authority('release','release_availability_v1',release),
            value_authority=authority('independent-source-ledger','captured_history_value_v1',values),
            authorize_joint=joint,authorize_rollout=authorize,elapsed_clock=lambda:state['clock'])
        if external:options['authorize_external_forecast']=external_authority
        b['options']=options;b['joint']=CanonicalForecastComparisonService(**options)
        return b
    return make


def execute(b):return b['joint'].execute(b['plan'],cancelled=lambda:b['state']['cancelled'])


@pytest.mark.parametrize('season',[None,2])
def test_shared_capture_both_seals_geometry_and_independent_reference(make_joint,season):
    b=make_joint(season=season);out=execute(b)
    assert b['state']['reads']==[0] and len(b['state']['calls'])==32
    assert len(out.grid)==32 and [len(p.result.events) for p in out.producers]==[32,32]
    assert dict(out.usage)['prefix']==320 and dict(out.usage)['calls']==32
    assert dict(out.usage)['events']==64 and dict(out.usage)['output_cells']==128
    for role,req in b['state']['calls'][:4]:
        row=next(row for scope,row in zip(req.scopes,req.rows) if dict(scope.dimensions)['location']=='north')
        k=req.prefix_length
        expected=[(8.,1.5,5.),(-2.,.5,math.sqrt(83/3)),(0.,1.5,math.sqrt(59/3)),(6.,3.,math.sqrt(68/3))][k-1]
        assert role=='candidate' and row==pytest.approx(expected)
    baseline=out.producers[1].result
    values=[e.value for e in baseline.events if dict(e.scope.dimensions)['location']=='north' and e.scope.forecast_origin==b['at'](4)]
    assert values==([8.,8.,8.,8.] if season is None else [0.,8.,0.,8.])
    assert out.producers[0].result.plan.digest!=baseline.plan.digest
    print('C1_GEOMETRY calls=32 events=64 prefix_cells=320 baseline=',values)


def replan(b,**changes):
    b['plan']=JointPredictionPlan.model_validate({**b['plan'].model_dump(),**changes})


def test_real_states_fit_once_per_producer_and_are_isolated(make_joint,monkeypatch):
    b=make_joint(categories=True);fits=[];old=CanonicalFeaturePipeline.fit_state
    def fit(self,frame,**kw):
        assert not b['state']['active']
        with b['store']._publication_session(StagingBudgets()):pass
        state=old(self,frame,**kw);fits.append((self.configuration,frame.copy(deep=True),state));return state
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',fit)
    out=execute(b)
    assert len(fits)==2 and len({s.state_id for c,f,s in fits})==2
    assert len({s.training_partition_reference for c,f,s in fits})==2
    assert all(s.training_cutoff==b['at'](4) and f.period_end.max()<=b['at'](4) for c,f,s in fits)
    assert fits[0][2].vocabularies['location']==('north','south') and fits[1][2].vocabularies=={}
    assert {e.state_id for e in out.producers[0].result.events}=={fits[0][2].state_id}
    assert {e.state_id for e in out.producers[1].result.events}=={fits[1][2].state_id}


@pytest.mark.parametrize('field',['provider','predictor','release_authority','value_authority','shared','handoff','events',
    'completion','resource_limits','configuration','baseline_definitions','trusted'])
def test_request_cannot_inject_dependencies_or_claims(make_joint,field):
    b=make_joint()
    with pytest.raises(TypeError):b['joint'].execute(b['plan'],**{field:lambda *_:True})
    assert not b['state']['reads'] and not b['state']['calls']


@pytest.mark.parametrize('fault',['missing_release','missing_value','protocol','scope','baseline_callback','duplicate_baseline','candidate_direct','candidate_sequence'])
def test_invalid_bootstrap_cannot_create_joint_execution(make_joint,fault):
    b=make_joint();options=dict(b['options'])
    if fault=='missing_release':options['release_authority']=None
    elif fault=='missing_value':options['value_authority']=None
    elif fault=='protocol':options['value_authority']=replace(options['value_authority'],protocol='release-only')
    elif fault=='scope':options['value_authority']=replace(options['value_authority'],datasets=())
    elif fault=='duplicate_baseline':options['baseline_definitions']=(b['definition'],b['definition'])
    elif fault=='baseline_callback':options['baseline_definitions']=(lambda *_:True,)
    else:
        data=options['candidate_capabilities'].model_dump()
        data['strategy' if fault=='candidate_direct' else 'representation_kind']='direct_multi_horizon' if fault=='candidate_direct' else 'temporal_sequence'
        options['candidate_capabilities']=PredictorCapabilities.model_validate(data)
    with pytest.raises(JointPredictionError):CanonicalForecastComparisonService(**options)
    assert not b['state']['reads']


@pytest.mark.parametrize('fault',['tenant','version','mapping','series','profile','config','numeric','capability','ceiling','unknown'])
def test_unapproved_plan_bindings_reject_before_capture(make_joint,fault):
    b=make_joint();data=b['plan'].model_dump()
    if fault=='ceiling':data['budgets']['max_elapsed_seconds']=301
    elif fault=='unknown':data['trusted']=True
    elif fault=='profile':data['producers'][1]['profile_id']='not-approved'
    elif fault in ('config','numeric','capability'):
        p=data['producers'][1]['plan']
        if fault=='config':p['configuration_digest']='0'*64
        else:p['predictor']['numeric_policy_id' if fault=='numeric' else 'predictor_id']='0'*64
    else:
        for producer in data['producers']:
            binding=producer['plan']['datasets'][0]
            if fault=='tenant':binding['reference']['organization_id']=UUID(int=99)
            elif fault=='version':binding['reference']['version_id']=binding['manifest_digest']='0'*64
            elif fault=='mapping':binding['mapping_version']='99.0.0'
            else:binding['series_ids']=(UUID(int=99),)
    with pytest.raises((ValueError,JointPredictionError)):
        plan=JointPredictionPlan.model_validate(data);b['joint'].execute(plan)
    assert not b['state']['reads'] and not b['state']['calls']


@pytest.mark.parametrize('fault',['value','earlier_value','missing_record','late_revision','lineage'])
def test_independent_value_authority_blocks_substitution_before_prediction(make_joint,fault):
    b=make_joint(season=4);period=0 if fault=='earlier_value' else 3
    if fault in ('missing_record','late_revision'):
        key=next(k for k,v in b['ledger'].items() if v['start']==b['at'](period))
        if fault=='missing_record':del b['ledger'][key]
        else:b['ledger'][key]['revision']=b['at'](5)
    else:
        opened=b['provider'].open_read
        @contextmanager
        def substitute(*a,**kw):
            with opened(*a,**kw) as session:
                batches=session.batches
                def stream():
                    for frame in batches():
                        frame=frame.copy(deep=True)
                        frame.loc[frame.period_start==b['at'](period),'value' if fault!='lineage' else 'lineage_id']=800. if fault!='lineage' else UUID(int=999)
                        yield frame
                session.batches=stream;yield session
        b['provider'].open_read=substitute
    with pytest.raises(JointPredictionError):execute(b)
    assert not b['state']['calls']
    if fault in ('value','earlier_value'):
        claims=[c for ctx,cs in b['state']['checks'] for c in cs if c.availability.period_start==b['at'](period)]
        assert claims and claims[0].availability.value_identity==('float64','4089000000000000')


@pytest.mark.parametrize('fault',['cleanup','late_read','physical_corruption','mutable_cell'])
def test_capture_closure_and_integrity_are_prerequisites(make_joint,fault):
    b=make_joint()
    if fault=='physical_corruption':
        paths=sorted(b['store'].root.rglob('*-canonical.parquet'))
        paths[-1].chmod(0o600);paths[-1].write_bytes(b'SYNTHETIC-CORRUPTION')
    else:
        opened=b['provider'].open_read
        @contextmanager
        def read(*a,**kw):
            with opened(*a,**kw) as session:
                batches=session.batches
                def stream():
                    for i,frame in enumerate(batches()):
                        if fault=='late_read' and i==2:raise OSError('C1-CALLBACK-SECRET')
                        if fault=='mutable_cell':
                            frame=frame.copy(deep=True);frame['untrusted_object']=[{'mutable':True} for _ in range(len(frame))]
                        yield frame
                session.batches=stream;yield session
            if fault=='cleanup':raise OSError('C1-CALLBACK-SECRET')
        b['provider'].open_read=read
    with pytest.raises(JointPredictionError) as error:execute(b)
    assert not b['state']['calls'] and 'C1-CALLBACK-SECRET' not in ''.join(traceback.format_exception(error.value))
    with b['store']._publication_session(StagingBudgets()):pass


@pytest.mark.parametrize('fault',['exception','nan','bool','wrong_key','missing','duplicate'])
def test_second_producer_failure_discards_first_success(make_joint,fault):
    b=make_joint()
    def fail(role,req,out):
        if role=='candidate':return out
        if fault=='exception':raise OSError('SECOND-PRODUCER-SECRET')
        points=list(out.points)
        if fault in ('nan','bool'):points[0]=points[0].model_copy(update={'value':float('nan') if fault=='nan' else True})
        elif fault=='wrong_key':points[0]=points[0].model_copy(update={'scope':points[0].scope.model_copy(update={'series_id':UUID(int=99)})})
        elif fault=='missing':points.pop()
        else:points[1]=points[0]
        return out.model_copy(update={'points':tuple(points)})
    b['prediction_hook']=fail
    with pytest.raises(JointPredictionError) as error:execute(b)
    assert len([r for role,r in b['state']['calls'] if role=='candidate'])==16
    assert len([r for role,r in b['state']['calls'] if role=='baseline'])==1
    assert b['state']['reads']==[0] and 'SECOND-PRODUCER-SECRET' not in ''.join(traceback.format_exception(error.value))


@pytest.mark.parametrize('role',['candidate','baseline'])
@pytest.mark.parametrize('origin',[4,8,10])
def test_global_revocation_rechecks_each_original_producer_origin(make_joint,role,origin):
    b=make_joint();profile=b['plan'].producers[0 if role=='candidate' else 1].profile_id
    def global_check(ctx,stage):
        if stage=='return':b['revoked'].add((profile,UUID(int=2),'fold-1',b['at'](origin)))
    b['global_hook']=global_check
    with pytest.raises(JointPredictionError):execute(b)
    assert len(b['state']['calls'])==32
    ctx,claims=b['state']['checks'][-1]
    assert (ctx.producer_profile_id,ctx.fold_id,ctx.forecast_origin)==(profile,'fold-1',b['at'](origin))
    assert all(c.plan_digest==ctx.plan_digest and c.forecast_origin==ctx.forecast_origin for c in claims)


@pytest.mark.parametrize('role',['candidate','baseline'])
@pytest.mark.parametrize('failure',['cancel','deadline','secret'])
def test_terminal_callbacks_checkpoint_and_sanitize(make_joint,role,failure):
    b=make_joint();armed=[False]
    def global_check(ctx,stage):
        if stage=='return':armed[0]=True
    def callback(ctx,stage):
        if armed[0] and ctx.producer_profile_id==b['plan'].producers[0 if role=='candidate' else 1].profile_id:
            if failure=='cancel':b['state']['cancelled']=True
            elif failure=='deadline':b['state']['clock']=300.
            else:
                try:raise OSError('CHAIN-ROOT-SECRET')
                except OSError as e:raise JointPredictionError('CHAIN-WRAPPER-SECRET') from e
    b['global_hook']=global_check;b['authorization_hook']=callback
    with pytest.raises(JointPredictionError) as error:execute(b)
    assert 'SECRET' not in ''.join(traceback.format_exception(error.value))
    assert len(b['state']['calls'])==32
    with b['store']._publication_session(StagingBudgets()):pass


@pytest.mark.parametrize('role',['candidate','baseline'])
@pytest.mark.parametrize('failure',['cancel','deadline'])
def test_predictor_cancellation_or_global_deadline(make_joint,role,failure):
    b=make_joint()
    def change(current,req,out):
        if current==role:
            b['state']['cancelled']=failure=='cancel';b['state']['clock']=300. if failure=='deadline' else 0.
        return out
    b['prediction_hook']=change
    with pytest.raises(JointPredictionError):execute(b)
    assert b['state']['reads']==[0]
    assert len(b['state']['calls'])==(1 if role=='candidate' else 17)


@pytest.mark.parametrize('clock,accepted',[(299.999,True),(300.,False),(301.,False)])
def test_exact_deadline_boundary_after_all_final_callbacks(make_joint,clock,accepted):
    b=make_joint();armed=[False]
    def global_check(ctx,stage):
        if stage=='return':armed[0]=True
    def final(ctx,stage):
        if armed[0]:b['state']['clock']=clock
    b['global_hook']=global_check;b['authorization_hook']=final
    if accepted:assert execute(b).producers
    else:
        with pytest.raises(JointPredictionError):execute(b)


@pytest.mark.parametrize('budget',['work','prefix','calls','state','assembly','prediction','representation','claims','checks','verification','output','output_cells','feedback'])
def test_observed_geometry_admission_before_any_prediction(make_joint,budget):
    b=make_joint();field={'work':'max_work_cells','prefix':'max_prefix_cells','calls':'max_predictor_calls',
        'state':'max_fitted_state_bytes','assembly':'max_assembly_bytes','prediction':'max_prediction_bytes',
        'representation':'max_representation_bytes','claims':'max_verification_claims','checks':'max_verification_calls',
        'verification':'max_verification_bytes','output':'max_output_bytes','output_cells':'max_output_cells',
        'feedback':'max_feedback_bytes'}[budget]
    data=b['plan'].model_dump();data['budgets'][field]=1
    with pytest.raises((ValueError,JointPredictionError)):
        replan(b,budgets=data['budgets']);execute(b)
    assert not b['state']['calls']


def test_runtime_request_budget_covers_both_producers(make_joint,monkeypatch):
    from src.services.canonical_forecast_comparison_service import _JointRequest
    b=make_joint();original=_JointRequest.charge
    def account(self,kind,amount):
        # Inject measured extra work after producer one: exercises the shared
        # runtime guard, rather than relying on an incorrectly low admission bound.
        if kind=='work' and len(self.handoffs)==1:
            amount+=self.limits['work']
        return original(self,kind,amount)
    monkeypatch.setattr(_JointRequest,'charge',account)
    with pytest.raises(JointPredictionError):execute(b)
    assert len(b['state']['calls'])==16 and b['state']['reads']==[0]


def test_original_decimals_and_diagnostic_views_are_detached(make_joint):
    b=make_joint(decimal=True);original=tuple(v.as_tuple() for v in b['raws'][2].reported)
    result=execute(b);before=result.diagnostic_id
    assert tuple(v.as_tuple() for v in b['raws'][2].reported)==original
    assert all(c.availability.value_identity[0]=='decimal' for ctx,cs in b['state']['checks'] for c in cs)
    frame=b['state']['calls'][0][1].matrix;frame.iloc[:,:]=999.
    assert result.diagnostic_id==before
    with pytest.raises(Exception):result.producers[0].result.events[0].value=800.
    with pytest.raises(JointPredictionError):b['joint'].execute(result)


def test_fresh_sequential_requests_and_concurrent_refusal(make_joint):
    b=make_joint();first=execute(b)
    b['state']['calls'].clear();b['state']['reads'].clear()
    first_again=execute(b);assert first_again.diagnostic_id==first.diagnostic_id
    b['state']['calls'].clear();b['state']['reads'].clear();entered=[]
    def reenter(role,req,out):
        if not entered:
            entered.append(True)
            with pytest.raises(JointPredictionError):execute(b)
        return out
    b['prediction_hook']=reenter
    assert execute(b).producers and b['state']['reads']==[0]


@pytest.mark.parametrize('fault',['copied','forged_values','previous_request'])
def test_foreign_or_reconstructed_handoff_never_seals(make_joint,monkeypatch,fault):
    from src.services.deterministic_forecast_rollout_service import DeterministicForecastRolloutService
    from src.data.contracts.backtesting import DeterministicRolloutResult
    b=make_joint();old=DeterministicForecastRolloutService._owned_shared_rollout;seen=[]
    def wrap(self,owner,plan,cfg,shared,**kw):
        result=old(self,owner,plan,cfg,shared,**kw);seen.append((self,owner,plan,cfg,shared,result))
        if fault=='previous_request':return result
        if fault=='copied':return replace(result)
        # Recompute all recursive parents, giving the forgery internally valid
        # event hashes/lineage. It still lacks the actual recorded handoff identity.
        events=[];parents={}
        for event in result.result.events:
            scope=event.scope;key=(scope.organization_id,scope.series_id,scope.forecast_origin,scope.fold_id)
            changed=event.model_copy(update={'value':event.value+1.,'parent_feedback_ids':tuple(parents.get(key,()))})
            parents.setdefault(key,[]).append(changed.event_id);events.append(changed)
        changed=DeterministicRolloutResult.model_validate({**result.result.model_dump(),'events':tuple(events)})
        return replace(result,result=changed)
    monkeypatch.setattr(DeterministicForecastRolloutService,'_owned_shared_rollout',wrap)
    if fault=='previous_request':
        assert execute(b).producers
        source,owner,plan,cfg,shared,handoff=seen[0]
        with pytest.raises(ValueError):old(source,owner,plan,cfg,shared)
        b['state']['calls'].clear();b['state']['reads'].clear()
        monkeypatch.setattr(DeterministicForecastRolloutService,'_owned_shared_rollout',lambda *a,**kw:handoff)
    with pytest.raises(JointPredictionError):execute(b)
    assert b['state']['reads']==[0]


@pytest.mark.parametrize('fault',['missing','extra','duplicate','misassigned'])
def test_seal_independently_validates_expected_grid(make_joint,monkeypatch,fault):
    from src.services.canonical_forecast_comparison_service import _JointRequest
    b=make_joint();old=_JointRequest.seal;checked=[]
    def seal(self,*args):
        grid=list(self.grid)
        if fault=='missing':grid.pop()
        elif fault=='extra':grid.append(grid[-1].model_copy(update={'forecast_origin':b['at'](11)}))
        elif fault=='duplicate':grid[-1]=grid[0]
        else:grid[0]=grid[0].model_copy(update={'dimensions':(('department','different'),('location','north'))})
        self.grid=tuple(grid);checked.append(True)
        return old(self,*args)
    monkeypatch.setattr(_JointRequest,'seal',seal)
    with pytest.raises(JointPredictionError):execute(b)
    assert checked and len(b['state']['calls'])==16


@pytest.mark.parametrize('org',[2,3])
def test_two_organizations_late_selective_revocation_aborts_all(make_joint,org):
    b=make_joint(organizations=(2,3));profile=b['plan'].producers[1].profile_id
    def late(ctx,stage):
        if stage=='return':b['revoked'].add((profile,UUID(int=org),'fold-1',b['at'](10)))
    b['global_hook']=late
    with pytest.raises(JointPredictionError):execute(b)
    assert b['state']['reads']==[0,0] and len(b['state']['calls'])==64
    last,claims=b['state']['checks'][-1]
    assert last.metadata.reference.organization_id==UUID(int=org)
    assert all(c.organization_id==UUID(int=org) for c in claims)


def test_distinct_organizations_and_folds_do_not_share_state_or_feedback(make_joint,monkeypatch):
    b=make_joint(organizations=(2,3),horizon=2,origins=(4,8));data=b['plan'].model_dump();fits=[]
    first=data['producers'][0]['plan']['folds'][0]
    second={**first,'fold_id':'fold-2','training_origins':(b['at'](5),),'validation_origins':(b['at'](10),),
        'model_fit_cutoff':b['at'](7),'assessment_cutoff':b['at'](12)}
    for producer in data['producers']:producer['plan']['folds']=(first,second)
    b['plan']=JointPredictionPlan.model_validate(data)
    fit=CanonicalFeaturePipeline.fit_state
    def track(self,frame,**kw):
        state=fit(self,frame,**kw);fits.append(state);return state
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',track)
    result=execute(b)
    assert len(fits)==8 and len({s.state_id for s in fits})==8
    assert all(len(s.organization_ids)==1 for s in fits)
    baseline=result.producers[1].result
    for event in baseline.events:
        raw=b['raws'][event.scope.organization_id.int];location=dict(event.scope.dimensions)['location']
        expected=raw[(raw.branch==location)&(raw['to']==pd.Timestamp(event.scope.forecast_origin))].reported.iloc[0]
        assert event.value==expected


@pytest.mark.parametrize('frequency,timezone,start,expected',[
 ('weekly','UTC','2020-12-07',['2021-01-04T00:00:00+00:00','2021-01-11T00:00:00+00:00','2021-01-18T00:00:00+00:00','2021-01-25T00:00:00+00:00']),
 ('daily','UTC','2020-02-25',['2020-02-29T00:00:00+00:00','2020-03-01T00:00:00+00:00','2020-03-02T00:00:00+00:00','2020-03-03T00:00:00+00:00']),
 ('daily','America/New_York','2020-03-04',['2020-03-08T00:00:00-05:00','2020-03-09T00:00:00-04:00','2020-03-10T00:00:00-04:00','2020-03-11T00:00:00-04:00'])])
def test_calendar_grid_uses_actual_executor_boundaries(make_joint,frequency,timezone,start,expected):
    b=make_joint(frequency=frequency,timezone=timezone,start=start,season=2);out=execute(b)
    for producer in out.producers:
        events=[e for e in producer.result.events if dict(e.scope.dimensions)['location']=='north' and e.scope.forecast_origin==b['at'](4)]
        assert [pd.Timestamp(e.scope.target_period).isoformat() for e in events]==expected


@pytest.mark.parametrize('remove',[2,3])
def test_missing_eligible_history_rejects_without_silent_search(make_joint,remove):
    def raw(frame):
        frame.drop(frame.index[frame['from']==pd.Timestamp('2020-01-06',tz='UTC')+pd.DateOffset(days=7*remove)],inplace=True)
        frame.reset_index(drop=True,inplace=True)
    b=make_joint(raw_change=raw,season=2)
    with pytest.raises(JointPredictionError):execute(b)
    assert not b['state']['calls']


def test_future_actuals_do_not_change_first_origin_projections(make_joint):
    a=make_joint();execute(a)
    expected=[(role,req.rows) for role,req in a['state']['calls'] if req.compatibility.forecast_origin==a['at'](4)]
    def changed(raw):raw.loc[raw['from']>=pd.Timestamp('2020-02-03',tz='UTC'),'reported']=999999999.
    b=make_joint(raw_change=changed);execute(b)
    observed=[(role,req.rows) for role,req in b['state']['calls'] if req.compatibility.forecast_origin==b['at'](4)]
    assert observed==expected and a['plan'].producers[0].plan.datasets!=b['plan'].producers[0].plan.datasets


def test_terminal_order_original_claims_and_lock_release(make_joint):
    b=make_joint();armed=[False];seen=[]
    def final(ctx,stage):
        if stage=='return':armed[0]=True
    def authority(ctx,claims):
        if armed[0]:
            with b['store']._publication_session(StagingBudgets()):pass
            seen.append((ctx,claims))
    b['global_hook']=final;b['value_hook']=authority
    assert execute(b).producers
    assert len(seen)==8
    wanted=[(p.profile_id,'fold-1',b['at'](n)) for p in b['plan'].producers for n in (4,8,9,10)]
    assert [(c.producer_profile_id,c.fold_id,c.forecast_origin) for c,cs in seen]==wanted
    for ctx,claims in seen:
        prior=[cs for c,cs in b['state']['checks'] if c==ctx]
        assert all(c.availability is prior[0][i].availability for cs in prior for i,c in enumerate(cs))
    trace=b['state']['trace'];global_index=trace.index(('joint','return'))
    assert all(t[0] in ('release','rollout') for t in trace[global_index+1:])


def test_per_field_future_category_rejects_before_fitting(make_joint,monkeypatch):
    def raw(frame):
        frame.loc[0,'offer']='0-FUTURE';frame.loc[0,'promo_release']=pd.Timestamp('2020-02-10',tz='UTC')
    b=make_joint(exo=True,raw_change=raw);fitted=[];old=CanonicalFeaturePipeline.fit_state
    def fit(*a,**kw):fitted.append(True);return old(*a,**kw)
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',fit)
    with pytest.raises(JointPredictionError):execute(b)
    assert not fitted and not b['state']['calls']


def test_time_varying_category_state_uses_only_calibration_prefix(make_joint,monkeypatch):
    b=make_joint(exo=True,horizon=1);fitted=[];old=CanonicalFeaturePipeline.fit_state
    def fit(self,frame,**kw):
        state=old(self,frame,**kw);fitted.append(state);return state
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',fit)
    assert execute(b).producers
    assert len(fitted)==2 and fitted[0].vocabularies['promotion']==('A',)
    assert 'promotion' not in fitted[1].vocabularies
    for role,req in b['state']['calls']:
        if role=='candidate' and req.compatibility.forecast_origin>=b['at'](8):
            assert all(row[req.feature_names.index('promotion')]==-1 for row in req.rows)


@pytest.mark.parametrize('field',['organization_id','location'])
def test_raw_fields_cannot_shadow_verified_scope(make_joint,field):
    b=make_joint();opened=b['provider'].open_read
    @contextmanager
    def altered(*a,**kw):
        with opened(*a,**kw) as session:
            batches=session.batches
            def stream():
                for frame in batches():
                    frame=frame.copy(deep=True);frame[field]='forged';yield frame
            session.batches=stream;yield session
    b['provider'].open_read=altered
    with pytest.raises(JointPredictionError):execute(b)
    assert not b['state']['calls']


@pytest.mark.parametrize('role',['candidate','baseline'])
def test_producer_permission_does_not_substitute_for_other_producer(make_joint,role):
    b=make_joint();profile=b['plan'].producers[0 if role=='candidate' else 1].profile_id
    def authorization(ctx,stage):
        if ctx.producer_profile_id==profile and stage=='origin':
            b['revoked'].add((profile,UUID(int=2),ctx.fold_id,ctx.forecast_origin))
    b['authorization_hook']=authorization
    with pytest.raises(JointPredictionError):execute(b)
    assert len(b['state']['calls'])==(0 if role=='candidate' else 16)


@pytest.mark.parametrize('bounded',[False,True])
def test_exact_decimal_conversion_is_operator_bound_and_original_is_preserved(make_joint,bounded):
    def raw(frame):frame.loc[frame['from']==pd.Timestamp('2020-01-27',tz='UTC'),'reported']=Decimal('0.1')
    b=make_joint(decimal=True,raw_change=raw,bounded=bounded)
    before=tuple(v.as_tuple() for v in b['raws'][2].reported)
    if bounded:
        out=execute(b)
        assert all(p.result.plan.predictor.numeric_policy_id==numeric_policy_id(
            b['configuration'] if i==0 else b['baseline'].configuration) for i,p in enumerate(out.producers))
        values=[e.value for e in out.producers[1].result.events if dict(e.scope.dimensions)['location']=='north' and e.scope.forecast_origin==b['at'](4)]
        assert values==[.1]*4
    else:
        with pytest.raises(JointPredictionError):execute(b)
        assert not b['state']['calls']
    assert tuple(v.as_tuple() for v in b['raws'][2].reported)==before


@pytest.mark.parametrize('value',[-8.,0.,-0.])
def test_signed_target_feedback_survives_immutable_capture(make_joint,value):
    def raw(frame):frame.loc[frame['from']==pd.Timestamp('2020-01-27',tz='UTC'),'reported']=value
    b=make_joint(raw_change=raw);result=execute(b)
    events=[e for e in result.producers[1].result.events if dict(e.scope.dimensions)['location']=='north' and e.scope.forecast_origin==b['at'](4)]
    assert all(e.value==value and math.copysign(1,e.value)==math.copysign(1,value) for e in events)


@pytest.mark.parametrize('kind',['overlap','origin_alignment','calendar'])
def test_invalid_full_horizon_declaration_rejects_before_capture(make_joint,kind):
    b=make_joint();data=b['plan'].model_dump()
    for producer in data['producers']:
        p=producer['plan']
        if kind=='overlap':p['folds'][0].update(validation_origins=(b['at'](6),),model_fit_cutoff=b['at'](6))
        elif kind=='origin_alignment':p['folds'][0]['training_origins']=(b['at'](4)+pd.Timedelta(days=1),)
        else:p['timezone']='America/New_York'
    with pytest.raises((JointPredictionError,ValueError)):
        b['plan']=JointPredictionPlan.model_validate(data);execute(b)
    assert not b['state']['reads'] and not b['state']['calls']


@pytest.mark.parametrize('kind',['permission','values'])
def test_publication_callback_revocation_precedes_terminal_scope_rechecks(make_joint,kind):
    b=make_joint();armed=[False];trace=[];old=b['provider'].require_current
    profile=b['plan'].producers[1].profile_id
    def global_check(ctx,stage):
        if stage=='return':armed[0]=True
    def read(*a,**kw):
        result=old(*a,**kw)
        if armed[0]:
            trace.append('read');b['revoked'].add((profile,UUID(int=2),'fold-1',b['at'](10)))
        return result
    b['provider'].require_current=read;b['global_hook']=global_check
    with pytest.raises(JointPredictionError):execute(b)
    assert trace==['read'] and len(b['state']['calls'])==32
    assert b['state']['checks'][-1][0].forecast_origin==b['at'](10)


@pytest.mark.parametrize('categories',[False,True])
def test_observed_admission_bounds_cover_actual_execution(make_joint,monkeypatch,categories):
    from src.services.canonical_forecast_comparison_service import _JointRequest
    b=make_joint(categories=categories);observed=[];old=_JointRequest.close
    def close(self):
        observed.append((dict(self.admission_base),dict(self.reservation),dict(self.used)))
        old(self)
    monkeypatch.setattr(_JointRequest,'close',close)
    result=execute(b);assert result.admission
    base,reserved,used=observed[0]
    for kind,amount in reserved.items():
        assert used[kind]-base[kind]<=amount,(kind,used[kind],base[kind],amount)


@pytest.mark.parametrize('revoked',[False,True])
def test_external_forecast_evidence_rechecked_after_other_producer(make_joint,revoked):
    b=make_joint(exo=True,external=True,horizon=1);permissions=[True]
    def predict(role,req,out):
        if revoked and role=='baseline' and req.compatibility.forecast_origin==b['at'](10):permissions[0]=False
        return out
    b['prediction_hook']=predict;b['external_hook']=lambda *_:permissions[0]
    if revoked:
        with pytest.raises(JointPredictionError):execute(b)
        assert len(b['state']['calls'])==8
    else:assert execute(b).producers
    assert any(t[0]=='external' for t in b['state']['trace'])


def test_missing_external_forecast_authority_fails_bootstrap(make_joint):
    b=make_joint(exo=True,external=True,horizon=1)
    with pytest.raises(JointPredictionError):CanonicalForecastComparisonService(**{**b['options'],'authorize_external_forecast':None})
    assert not b['state']['reads']


def test_calibration_evidence_revocation_still_rejects(make_joint):
    b=make_joint();revoked=[False]
    def predict(role,req,out):
        if role=='baseline' and req.compatibility.forecast_origin==b['at'](10) and req.prefix_length==4:revoked[0]=True
        return out
    def release(ctx,claims):
        if revoked[0] and ctx.target_period_end is None:raise JointPredictionError('CALIBRATION-SECRET')
    b['prediction_hook']=predict;b['release_hook']=release
    with pytest.raises(JointPredictionError) as error:execute(b)
    assert len(b['state']['calls'])==32 and 'CALIBRATION-SECRET' not in ''.join(traceback.format_exception(error.value))


def test_c1_owner_seals_predictions_without_entering_b2a_label_phase(make_joint,monkeypatch):
    from src.services.canonical_forecast_evaluation_service import _EvaluationOwner
    b=make_joint();sealed=[];old=_EvaluationOwner._seal_predictions
    def seal(self,handoff):
        value=old(self,handoff)
        assert self._EvaluationOwner__state=='sealed';sealed.append(self)
        return value
    def labels(*a,**kw):raise AssertionError('C1 must not enter the labels phase')
    monkeypatch.setattr(_EvaluationOwner,'_seal_predictions',seal)
    monkeypatch.setattr(_EvaluationOwner,'consume',labels)
    assert execute(b).producers and len(sealed)==2
    assert all(o._EvaluationOwner__state=='closed' for o in sealed)


def configure_numeric_joint(bundle, *, availability='lagged', tolerance=None):
    helper=runpy.run_path('tests/training/test_deterministic_forecast_rollout.py')['numeric_covariate_configuration']
    cfg=helper(bundle['configuration'],availability=availability,tolerance=tolerance)
    data=bundle['plan'].model_dump();producer=data['producers'][0]['plan']
    cap=PredictorCapabilities.model_validate({**producer['predictor'],
        'representation_schema_id':representation_schema_id(cfg),'numeric_policy_id':numeric_policy_id(cfg)})
    producer.update(configuration_digest=CanonicalFeaturePipeline(cfg).digest,predictor=cap)
    bundle['plan']=JointPredictionPlan.model_validate(data);bundle['configuration']=cfg
    bundle['joint']=CanonicalForecastComparisonService(**{**bundle['options'],
        'candidate_configuration':cfg,'candidate_capabilities':cap})
    return cfg


def test_numeric_snapshot_mixed_object_and_nullable_scalar_fidelity(make_joint):
    from src.services.canonical_forecast_comparison_service import _FrozenCapture, _scalar
    b=make_joint()
    original=pd.DataFrame({
        'mixed':pd.Series([2**53+1,1.0],dtype=object),
        'negative':pd.Series([-(2**53+1),1.0],dtype=object),
        'decimal':pd.Series([Decimal('-0.0000'),Decimal('19.9900')],dtype=object),
        'tiny_large':pd.Series([Decimal('1E-400'),Decimal('1E400')],dtype=object),
        'positive_zero':pd.Series([Decimal('0.000'),Decimal('0.00')],dtype=object),
        'float_zero':[-0.,0.],
        'nullable_int':pd.Series([2**53+1,pd.NA],dtype='Int64'),
        'nullable_float':pd.Series([-0.,pd.NA],dtype='Float64'),
        'nullable_text':pd.Series(['category-A',pd.NA],dtype='string'),
        'time':pd.Series([pd.Timestamp('2020-03-08',tz='America/New_York'),pd.Timestamp('2020-03-09',tz='America/New_York')])})
    frozen=_FrozenCapture.freeze(b['metadata'][0],original,4096,lambda *a:None)
    view=frozen.view();other=frozen.view()
    pd.testing.assert_frame_equal(original,view,check_exact=True)
    for i,column in enumerate(original):
        expected=[_scalar(v,4096)[1] for v in original[column].tolist()]
        assert [_scalar(row[i],4096)[1] for row in frozen.rows]==expected
        assert [_scalar(v,4096)[1] for v in view[column].tolist()]==expected
    assert type(view['mixed'].iloc[0]) is int and view['mixed'].iloc[0]==9007199254740993
    assert type(view['mixed'].iloc[1]) is float
    original.loc[0,'mixed']=0;view.loc[1,'decimal']=Decimal('800')
    assert other['mixed'].iloc[0]==2**53+1 and other['decimal'].iloc[1].as_tuple()==Decimal('19.9900').as_tuple()
    pd.testing.assert_frame_equal(frozen.view(),other,check_exact=True)


def test_numeric_snapshot_still_rejects_mutable_and_implicit_categories(make_joint):
    from src.services.canonical_forecast_comparison_service import _FrozenCapture
    b=make_joint()
    for values in ([{'mutable':True}],[['mutable']],pd.Categorical(['category-A'])):
        with pytest.raises(ValueError):
            _FrozenCapture.freeze(b['metadata'][0],pd.DataFrame({'unsupported':values}),4096,lambda *a:None)


@pytest.mark.parametrize('value,tolerance,accepted',[
    (2**53+1,None,False),(-(2**53+1),None,False),(2**53,None,True),(2**53+2,None,True),
    (Decimal('0.10'),None,False),(Decimal('1E-400'),None,False),(Decimal('1E400'),None,False),
    (Decimal('-0.0000'),None,True),(-0.,None,True),(2**53+1,'1',True),(2**53+1,'0.999',False),
    (Decimal('0.10'),'1E-16',True),(Decimal('19.99'),'1E-14',True)])
def test_numeric_authorized_joint_preserves_identity_to_policy_boundary(make_joint,monkeypatch,value,tolerance,accepted):
    from fractions import Fraction
    from src.services.canonical_forecast_comparison_service import _FrozenCapture
    from src.services.canonical_training_preparation_service import CanonicalTrainingPreparationService
    def raw(frame):
        frame['offer']=pd.Series([value]*len(frame),dtype=object);frame.loc[0,'offer']=1.0
    b=make_joint(exo=True,horizon=1,raw_change=raw);cfg=configure_numeric_joint(b,tolerance=tolerance)
    stages={name:[] for name in ('provider','frozen','view','exogenous','conversion')}
    def identity(v):
        if type(v) is Decimal:return ('Decimal',v.as_tuple())
        if type(v) is float:return ('float',struct.pack('>d',v).hex())
        return (type(v).__name__,v)
    expected=identity(value)
    with b['provider'].open_read(b['metadata'][0].reference,purpose='offline_forecast_evaluation') as session:
        for frame in session.batches():stages['provider'].extend(identity(v) for v in frame.promotion.tolist())
    b['state']['reads'].clear()
    old_view=_FrozenCapture.view
    def view(self):
        i=self.columns.index('promotion');stages['frozen'].extend(identity(row[i]) for row in self.rows)
        result=old_view(self);stages['view'].extend(identity(v) for v in result.promotion.tolist());return result
    monkeypatch.setattr(_FrozenCapture,'view',view)
    old_availability=CanonicalTrainingPreparationService._availability
    def availability(self,frame,engine,checkpoint):
        claims,exo=old_availability(self,frame,engine,checkpoint)
        if exo is not None:stages['exogenous'].extend(identity(v) for v in exo['value'].tolist())
        return claims,exo
    monkeypatch.setattr(CanonicalTrainingPreparationService,'_availability',availability)
    old_convert=CanonicalFeaturePipeline.convert_numeric
    def convert(self,v,field='value'):
        if field=='promotion':stages['conversion'].append(identity(v))
        return old_convert(self,v,field)
    monkeypatch.setattr(CanonicalFeaturePipeline,'convert_numeric',convert)
    def predict(role,request,result):
        if role=='candidate':return PredictionBatch(request_id=request.request_id,
            points=tuple(PredictionPoint(scope=scope,value=float(row[-1])) for scope,row in zip(request.scopes,request.rows)))
        return result
    b['prediction_hook']=predict
    if accepted:
        result=execute(b);assert len(result.producers)==2 and len(b['state']['calls'])==8
        for event in result.producers[0].result.events:
            assert event.value==float(value)
            assert event.numeric_policy_id==numeric_policy_id(cfg)
            if tolerance is not None:assert abs(Fraction.from_float(event.value)-Fraction(value))<=Fraction(Decimal(tolerance))
            if float(value)==0:assert math.copysign(1,event.value)==math.copysign(1,float(value))
    else:
        with pytest.raises(JointPredictionError):execute(b)
        assert not b['state']['calls']
    assert all(expected in values for values in stages.values()),stages
    assert b['state']['reads']==[0]
    assert identity(b['raws'][2].offer.iloc[1])==expected
    print('NUMERIC_C1',repr(expected),'stages=',{k:expected in vs for k,vs in stages.items()},
        'tolerance=',tolerance,'calls=',len(b['state']['calls']),'accepted=',accepted)


def test_numeric_exact_covariates_keep_fold_tenant_and_producer_isolation(make_joint,monkeypatch):
    def raw(frame):
        frame['offer']=pd.Series([2**53+2]*len(frame),dtype=object);frame.loc[0,'offer']=1.0
    b=make_joint(exo=True,horizon=1,origins=(4,8),organizations=(2,3),raw_change=raw)
    configure_numeric_joint(b);data=b['plan'].model_dump();first=data['producers'][0]['plan']['folds'][0]
    second={**first,'fold_id':'fold-2','training_origins':(b['at'](5),),'validation_origins':(b['at'](10),),
        'model_fit_cutoff':b['at'](6),'assessment_cutoff':b['at'](11)}
    for producer in data['producers']:producer['plan']['folds']=(first,second)
    b['plan']=JointPredictionPlan.model_validate(data)
    states=[];old=CanonicalFeaturePipeline.fit_state
    def fit(self,frame,**kwargs):
        state=old(self,frame,**kwargs);states.append(state);return state
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',fit)
    result=execute(b)
    assert len(states)==8 and len({s.state_id for s in states})==8
    assert len(b['state']['calls'])==16 and b['state']['reads']==[0,0]
    assert len(result.grid)==16 and sum(len(p.result.events) for p in result.producers)==32
    assert all(len(s.organization_ids)==1 for s in states)
    for role,request in b['state']['calls']:
        assert not {'value','label','actual'}&set(request.feature_names)
        if role=='candidate':assert all(row[-1]==float(2**53+2) for row in request.rows)


@pytest.mark.parametrize('route',['C1','B1'])
@pytest.mark.parametrize('value,tolerance,accepted',[(2**53+1,None,False),(2**53+2,None,True),(2**53+1,'1',True)])
def test_numeric_verified_chunk_dtype_variation_is_lossless(tmp_path,monkeypatch,route,value,tolerance,accepted):
    helper=runpy.run_path('tests/training/test_deterministic_forecast_rollout.py')
    helper['variable_numeric_batch_adapter'](monkeypatch)
    reference=runpy.run_path('tests/training/test_forecast_comparison_shared_execution.py')
    def raw(frame):frame['offer']=pd.Series([1.0]*3+[value]*(len(frame)-3),dtype=object)
    b=reference['make_joint'].__wrapped__(tmp_path,monkeypatch)(exo=True,horizon=1,raw_change=raw)
    cfg=configure_numeric_joint(b,tolerance=tolerance)
    with b['provider'].open_read(b['metadata'][0].reference,purpose='offline_forecast_evaluation') as session:
        chunks=list(session.batches())
    assert [str(frame.promotion.dtype) for frame in chunks][:2]==['float64','int64']
    assert type(chunks[1].promotion.tolist()[0]) is int and chunks[1].promotion.tolist()[0]==value
    b['state']['reads'].clear();seen=[];old=CanonicalFeaturePipeline.convert_numeric
    def convert(self,v,field='value'):
        if field=='promotion' and type(v) is int:seen.append(v)
        return old(self,v,field)
    monkeypatch.setattr(CanonicalFeaturePipeline,'convert_numeric',convert)
    def run():
        return execute(b) if route=='C1' else b['service'].rollout(b['plan'].producers[0].plan,cfg)
    if accepted:
        result=run();assert result
    else:
        from src.services.deterministic_forecast_rollout_service import ForecastRolloutError
        with pytest.raises(JointPredictionError if route=='C1' else ForecastRolloutError):run()
    assert value in seen
    calls=b['state']['calls'] if route=='C1' else b['calls']
    assert len(calls)==((8 if route=='C1' else 4) if accepted else 0)
    requests=[request for role,request in calls if role=='candidate'] if route=='C1' else calls
    assert all(row[-1]==float(value) for request in requests for row in request.rows)
    print('NUMERIC_CHUNKS',route,'value=',value,'tolerance=',tolerance,'accepted=',accepted,'calls=',len(calls))
