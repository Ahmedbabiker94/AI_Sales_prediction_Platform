"""C0 composition tests only: admitted synthetic data and a fixed dummy producer.

No last-observation/seasonal algorithm, fitting or production authority exists here.
Operator fixtures own independent source ledgers; requests never supply verifiers.
"""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
import hashlib
import runpy
import struct
import traceback
from uuid import UUID

import pandas as pd
import pytest

from src.data.contracts.backtesting import ForecastEvaluationPlan
from src.data.contracts.features import FeatureConfiguration
from src.data.contracts.forecast_evaluation import ForecastScoringPlan
from src.data.contracts.dataset import StagingBudgets
from src.services.canonical_baseline_evaluation_service import (
    BaselineAuthorityBinding, BaselineCompositionError, CanonicalBaselineEvaluationService)
from src.services.canonical_training_preparation_service import CapturedHistoryClaim


def source_identity(value):
    # Independent source values, not identities copied out of incoming claims.
    if type(value) is Decimal:
        t=value.as_tuple();return ('decimal',t.sign,tuple(t.digits),t.exponent)
    return ('float64',struct.pack('>d',float(value)).hex())


@pytest.fixture
def make_composition(tmp_path):
    fixture=runpy.run_path('tests/training/test_forecast_evaluation.py')['make_evaluation'].__wrapped__(tmp_path)
    def make(**kwargs):
        b=fixture(**kwargs);ledger={};trace=[];value_calls=[];revoked=set();predictor_calls=[]
        operator_plan=ForecastEvaluationPlan.model_validate(b['plan'].model_dump())
        metadata={m.reference.organization_id:m for m in b['metadata']}
        for m in b['metadata']:
            raw=b['raws'][m.reference.organization_id.int]
            records=[]
            with b['provider'].open_read(m.reference,purpose=m.purpose) as session:
                for frame in session.batches():records.extend(frame.to_dict('records'))
            for row in records:
                original=raw.iloc[int(row['source_record_id'])]
                assert row['value']==original['reported']
                ledger[m.reference.organization_id,row['source_record_id']]=dict(
                    series=row['series_id'],lineage=row['lineage_id'],start=original['from'],end=original['to'],
                    available=original['released'],evidence=original['proof'],value=source_identity(original['reported']),
                    revision_available=original['released'])
        def release(ctx,claims):
            trace.append(('release',ctx.fold_id,ctx.forecast_origin))
            m=metadata.get(ctx.metadata.reference.organization_id)
            if m!=ctx.metadata:return False
            for c in claims:
                t=ledger.get((m.reference.organization_id,c.source_record_id))
                if (t is None or c.field!='value' or c.series_id!=t['series'] or c.lineage_id!=t['lineage']
                    or c.period_start!=t['start'] or c.period_end!=t['end'] or c.available_at!=t['available']
                    or c.evidence_reference!=t['evidence']):return False
            return True
        def value(ctx,claims):
            trace.append(('value',ctx.fold_id,ctx.forecast_origin));value_calls.append((ctx,claims))
            org=ctx.metadata.reference.organization_id;m=metadata.get(org)
            if m!=ctx.metadata or ctx.plan_digest!=operator_plan.digest:return False
            if (org,ctx.fold_id,ctx.forecast_origin) in revoked:return False
            fold=next((f for f in operator_plan.folds if f.fold_id==ctx.fold_id),None)
            if ctx.fold_id is not None:
                if fold is None or ctx.forecast_origin not in fold.origins or ctx.training_cutoff!=fold.categorical_cutoff:return False
            for c in claims:
                a=c.availability;t=ledger.get((org,a.source_record_id));mapping=m.mapping
                if (type(c) is not CapturedHistoryClaim or t is None or c.dataset_reference!=m.reference
                    or c.manifest_digest!=m.manifest_digest or c.admission_digest!=m.admission_digest
                    or c.source_id!=m.source_id or c.mapping_id!=mapping.mapping_id or c.mapping_version!=mapping.version
                    or c.mapping_digest!=hashlib.sha256(m.mapping_json.encode()).hexdigest()
                    or c.organization_id!=org or c.metric!=mapping.target_metric
                    or (c.unit,c.currency)!=(mapping.measurement.unit,mapping.measurement.currency)
                    or c.purpose!='offline_forecast_evaluation' or c.protocol_version!='captured_history_value_v1'
                    or c.forecast_origin!=ctx.forecast_origin or c.calibration_cutoff!=ctx.training_cutoff
                    or c.plan_digest!=operator_plan.digest or c.fold_id!=ctx.fold_id
                    or c.target_period_end!=ctx.target_period_end or c.series_ids!=ctx.series_ids
                    or a.field!='value' or a.series_id!=t['series'] or a.lineage_id!=t['lineage']
                    or a.period_start!=t['start'] or a.period_end!=t['end']
                    or a.available_at!=t['available'] or a.evidence_reference!=t['evidence']
                    or a.value_identity!=t['value']):return False
                if fold:
                    for cutoff in (ctx.forecast_origin,ctx.training_cutoff):
                        if a.period_end<=cutoff and a.available_at<=cutoff and t['revision_available']>cutoff:return False
            return True
        predictor=b['service'].predictor
        def predict(request):
            trace.append(('predict',request.compatibility.fold_id,request.compatibility.forecast_origin))
            predictor_calls.append(request)
            assert not {'value','label','actual','reported'} & set(request.matrix.columns)
            assert not any(hasattr(request,field) for field in ('labels','future_frame','provider','evaluator'))
            return predictor(request)
        def binding(name,protocol,verify):
            return BaselineAuthorityBinding(name,'synthetic-1',protocol,operator_plan.datasets,verify)
        options=dict(provider=b['provider'],configuration=b['configuration'],capabilities=b['plan'].predictor,predictor=predict,
            release_authority=binding('operator-source-release','release_availability_v1',release),
            value_authority=binding('operator-independent-value','captured_history_value_v1',value),
            label_revision_authority=binding('operator-independent-label','captured_value_as_of_E_v1',b['evaluator'].verify_revision),
            authorize_rollout=b['service'].authorize,authorize_evaluation=b['evaluator'].authorize,
            authorize_label_use=b['evaluator'].authorize_labels)
        b.update(options=options,history_ledger=ledger,composition_trace=trace,value_calls=value_calls,
            revoked=revoked,predictor_calls=predictor_calls)
        return b
    return make


def build(b,**changes):return CanonicalBaselineEvaluationService(**{**b['options'],**changes})


def execute(service,b,**kwargs):return service.evaluate(b['scoring'],b['plan'],b['configuration'],**kwargs)


def track_reads(b):
    reads=[];opened=b['provider'].open_read;active=[False]
    @contextmanager
    def tracked(*a,**kw):
        reads.append(1);active[0]=True
        try:
            with opened(*a,**kw) as session:yield session
        finally:active[0]=False
    b['provider'].open_read=tracked
    return reads,active


def substitute(b,change):
    opened=b['provider'].open_read
    @contextmanager
    def detached(*a,**kw):
        with opened(*a,**kw) as session:
            batches=session.batches
            def stream():
                for frame in batches():
                    view=frame.copy(deep=True);change(view);yield view
            session.batches=stream
            yield session
    b['provider'].open_read=detached


def test_operator_selection_valid_eight_and_owned_evaluation_compatibility(make_composition):
    b=make_composition(decimal=True)
    before=tuple(v.as_tuple() for v in b['raws'][2].reported)
    legacy=b['evaluator'].evaluate(b['scoring'],b['plan'],b['configuration'])
    service=build(b);reads,active=track_reads(b)
    result=execute(service,b)
    assert result.model_dump()==legacy.model_dump()
    assert len(reads)==2 and not active[0]  # One B1 capture, one B2a label capture; none added by C0.
    assert b['value_calls'] and len(b['predictor_calls'])==6
    north=next(s.series_id for s in b['metadata'][0].series if s.dimensions['location']=='north')
    assert b['predictor_calls'][0].matrix.loc[(UUID(int=2),north,b['at'](4)),'lag1']==8.
    assert sum(e.statistics.sae.fraction for e in result.events)==12
    assert tuple(v.as_tuple() for v in b['raws'][2].reported)==before
    with pytest.raises(Exception):result.events[0].scope.metric='changed'
    with pytest.raises(FrozenInstanceError):b['options']['value_authority'].authority_id='caller'
    print('C0 VALID8=ACCEPTED; EXACT_B2A_RESULT_IDENTICAL; CAPTURES=2; synthetic producer only, no baseline algorithm')


def test_substituted_recomputed_800_rejects_before_prediction(make_composition):
    b=make_composition();service=build(b)
    def changed(frame):frame.loc[frame.period_start==b['at'](3),'value']=800.
    substitute(b,changed)
    with pytest.raises(BaselineCompositionError):execute(service,b)
    assert not b['predictor_calls']
    c=next(c for _,claims in b['value_calls'] for c in claims if c.availability.period_start==b['at'](3))
    assert c.availability.value_identity==('float64','4089000000000000')
    assert b['history_ledger'][c.organization_id,c.availability.source_record_id]['value']!=c.availability.value_identity
    print('C0 SUBSTITUTED800=REJECTED; recomputed identity attested against independent ledger; PREDICTOR_CALLS=0')


@pytest.mark.parametrize('argument',['release_verifier','value_verifier','availability_verifier','trusted','evaluator',
    'rollout_service','provider','callback_registry','value_authority','provider_override'])
def test_ordinary_request_cannot_install_authority_or_services(make_composition,argument):
    b=make_composition();service=build(b);reads,_=track_reads(b);called=[]
    def liar(*args):called.append(args);return True
    with pytest.raises(TypeError):execute(service,b,**{argument:liar})
    assert not called and not reads and not b['predictor_calls']


@pytest.mark.parametrize('fault',['missing_release','missing_value','missing_label','release_protocol','value_protocol',
    'purpose','binding_version','different_scope','missing_callable','missing_evaluation_authorizer','schema','numeric','direct'])
def test_invalid_operator_configuration_fails_before_capture(make_composition,fault):
    b=make_composition();reads,_=track_reads(b);changes={}
    if fault.startswith('missing_') and fault in ('missing_release','missing_value','missing_label'):
        changes[{'missing_release':'release_authority','missing_value':'value_authority','missing_label':'label_revision_authority'}[fault]]=None
    elif fault in ('release_protocol','value_protocol','purpose','binding_version','different_scope','missing_callable'):
        key='release_authority' if fault=='release_protocol' else 'value_authority';authority=b['options'][key]
        if fault in ('release_protocol','value_protocol'):authority=replace(authority,protocol='release-only')
        elif fault=='purpose':authority=replace(authority,purpose='production_training')
        elif fault=='binding_version':authority=replace(authority,binding_version='unknown')
        elif fault=='missing_callable':authority=replace(authority,verify=None)
        else:
            d=authority.datasets[0].model_dump();d['source_id']=UUID(int=999)
            authority=replace(authority,datasets=(type(authority.datasets[0]).model_validate(d),))
        changes[key]=authority
    elif fault=='missing_evaluation_authorizer':changes['authorize_evaluation']=None
    else:
        cap=b['options']['capabilities'].model_dump()
        if fault=='schema':cap['representation_schema_id']='0'*64
        elif fault=='numeric':cap['numeric_policy_id']='0'*64
        else:cap['strategy']='direct_multi_horizon'
        changes['capabilities']=type(b['options']['capabilities']).model_validate(cap)
    with pytest.raises(BaselineCompositionError):build(b,**changes)
    assert not reads and not b['predictor_calls']


@pytest.mark.parametrize('fault',['tenant','version','source','mapping','series','configuration','capability','metadata'])
def test_request_declarations_cannot_change_composed_bindings(make_composition,fault):
    b=make_composition();service=build(b);reads,_=track_reads(b)
    data=b['plan'].model_dump();cfg=b['configuration']
    if fault in ('tenant','version','source','mapping','series'):
        d=data['datasets'][0]
        if fault in ('tenant','version'):
            d['reference'].update({'organization_id':UUID(int=9)} if fault=='tenant' else {'version_id':'0'*64})
            if fault=='version':d['manifest_digest']='0'*64
        elif fault=='source':d['source_id']=UUID(int=9)
        elif fault=='mapping':d['mapping_version']='9.0.0'
        else:d['series_ids']=(UUID(int=9),)
    elif fault=='configuration':cfg=cfg.model_copy(update={'configuration_id':'other'})
    elif fault=='capability':data['predictor']['predictor_id']='other'
    else:data['authority_id']='caller-requested-authority'
    with pytest.raises((BaselineCompositionError,ValueError)):
        plan=ForecastEvaluationPlan.model_validate(data)
        service.evaluate(b['scoring'],plan,cfg)
    assert not reads and not b['predictor_calls']


@pytest.mark.parametrize('fault',['record','period','metric','missing_field'])
def test_detached_capture_faults_cannot_override_authority_scope(make_composition,fault):
    b=make_composition();service=build(b)
    def changed(frame):
        if fault=='record':frame.loc[:,'source_record_id']='unknown-record'
        elif fault=='period':frame.loc[:,'period_start']=b['at'](0)
        elif fault=='metric':frame.loc[:,'metric']='other'
        else:frame.drop(columns='value',inplace=True)
    substitute(b,changed)
    with pytest.raises(BaselineCompositionError):execute(service,b)
    assert not b['predictor_calls']


@pytest.mark.parametrize('fault',['late_revision','backdated','missing_record'])
def test_independent_revision_authority_denies_without_fallback(make_composition,fault):
    b=make_composition();service=build(b)
    key=next(k for k,t in b['history_ledger'].items() if t['start']==b['at'](3))
    if fault=='missing_record':del b['history_ledger'][key]
    else:b['history_ledger'][key]['revision_available']=b['at'](5)
    with pytest.raises(BaselineCompositionError):execute(service,b)
    assert not b['predictor_calls']


def test_original_immutable_claims_exact_cutoff_and_read_lock_lifecycle(make_composition):
    b=make_composition(decimal=True);before=tuple(v.as_tuple() for v in b['raws'][2].reported)
    reads,active=track_reads(b);original=b['options']['value_authority'].verify
    def value(ctx,claims):
        assert not active[0]
        with b['store']._publication_session(StagingBudgets()):pass
        return original(ctx,claims)
    service=build(b,value_authority=replace(b['options']['value_authority'],verify=value))
    result=execute(service,b);assert result.events and len(reads)==2
    repeated=[cs for ctx,cs in b['value_calls'] if ctx.target_period_end is not None and ctx.forecast_origin==b['at'](4)]
    assert len(repeated)==3  # B1 initial/terminal plus B2a terminal.
    assert all(c.availability is repeated[0][n].availability for cs in repeated for n,c in enumerate(cs))
    assert any(c.availability.available_at==b['at'](4) for c in repeated[0])
    assert all(c.availability.value_identity[0]=='decimal' for c in repeated[0])
    assert tuple(v.as_tuple() for v in b['raws'][2].reported)==before


@pytest.mark.parametrize('trigger',['global','read','terminal'])
def test_fresh_origin_value_revocation_discards_entire_evaluation(make_composition,trigger):
    b=make_composition();options={};old=b['options']['authorize_evaluation'];armed=[False]
    target=(UUID(int=2),'fold-1',b['at'](8))
    def authorize(ctx,stage):
        if stage=='return' and ctx.metadata is None:
            armed[0]=True
            if trigger=='global':b['revoked'].add(target)
        return old(ctx,stage)
    options['authorize_evaluation']=authorize
    current=b['provider'].require_current
    def read(*a,**kw):
        result=current(*a,**kw)
        if armed[0] and trigger=='read':b['revoked'].add(target)
        return result
    b['provider'].require_current=read
    value=b['options']['value_authority'].verify
    def authority(ctx,claims):
        if armed[0] and trigger=='terminal' and ctx.forecast_origin==b['at'](8):b['revoked'].add(target)
        return value(ctx,claims)
    options['value_authority']=replace(b['options']['value_authority'],verify=authority)
    service=build(b,**options)
    with pytest.raises(BaselineCompositionError):execute(service,b)
    assert len(b['predictor_calls'])==6 and target in b['revoked']
    assert b['value_calls'][-1][0].forecast_origin==b['at'](8)


def test_prediction_finishes_before_authorized_label_capture(make_composition):
    b=make_composition();opened=b['provider'].open_read;captures=[]
    @contextmanager
    def read(*a,**kw):
        captures.append(len(b['predictor_calls']))
        with opened(*a,**kw) as session:yield session
    b['provider'].open_read=read
    execute(build(b),b)
    assert captures==[0,6]
    assert all(not {'label','actual','value'} & set(r.matrix) for r in b['predictor_calls'])


@pytest.mark.parametrize('fault',['read','cleanup','cancel_predict','cancel_final','deadline_predict','deadline_final','typed_secret'])
def test_reader_callbacks_cancellation_and_deadline_are_all_or_nothing(make_composition,fault):
    b=make_composition();cancel=[False];clock=[0.];changes={'elapsed_clock':lambda:clock[0]}
    old=b['options']['predictor']
    def predict(req):
        result=old(req)
        if fault=='cancel_predict':cancel[0]=True
        elif fault=='deadline_predict':clock[0]=300.
        return result
    changes['predictor']=predict
    authorize=b['options']['authorize_evaluation'];final=[False]
    def auth(ctx,stage):
        if stage=='return' and ctx.metadata is None:final[0]=True
        return authorize(ctx,stage)
    changes['authorize_evaluation']=auth
    authority=b['options']['value_authority'].verify
    def value(ctx,claims):
        if final[0] and ctx.target_period_end is not None:
            if fault=='cancel_final':cancel[0]=True
            elif fault=='deadline_final':clock[0]=300.
            elif fault=='typed_secret':raise BaselineCompositionError('C0-SYNTHETIC-SECRET')
        return authority(ctx,claims)
    changes['value_authority']=replace(b['options']['value_authority'],verify=value)
    if fault in ('read','cleanup'):
        opened=b['provider'].open_read
        @contextmanager
        def broken(*a,**kw):
            with opened(*a,**kw) as session:
                if fault=='read':
                    batches=session.batches
                    def stream():
                        for n,frame in enumerate(batches()):
                            if n==2:raise OSError('C0-SYNTHETIC-SECRET')
                            yield frame
                    session.batches=stream
                yield session
            if fault=='cleanup':raise OSError('C0-SYNTHETIC-SECRET')
        b['provider'].open_read=broken
    service=build(b,**changes)
    with pytest.raises(BaselineCompositionError) as info:execute(service,b,cancelled=lambda:cancel[0])
    assert 'C0-SYNTHETIC-SECRET' not in ''.join(traceback.format_exception(info.value))
    if fault in ('read','cleanup'):assert not b['predictor_calls']
    with b['store']._publication_session(StagingBudgets()):pass


def test_declared_request_budget_still_limits_owned_producer(make_composition):
    b=make_composition();service=build(b);data=b['plan'].model_dump();data['budgets']['max_work_cells']=1
    plan=ForecastEvaluationPlan.model_validate(data)
    scoring=ForecastScoringPlan.model_validate({**b['scoring'].model_dump(),'producer_plan_digest':plan.digest})
    b['approved'].add(scoring.digest)
    with pytest.raises(BaselineCompositionError):service.evaluate(scoring,plan,b['configuration'])
    assert not b['predictor_calls']


def test_release_only_operation_cannot_satisfy_explicit_value_protocol(make_composition):
    b=make_composition()
    # Misconfigured bootstrap operation expects AvailabilityClaim, not value
    # attestations. This must fail, never fall back to release-only execution.
    release=b['options']['release_authority'].verify
    service=build(b,value_authority=replace(b['options']['value_authority'],verify=release))
    with pytest.raises(BaselineCompositionError):execute(service,b)
    assert not b['predictor_calls']


@pytest.mark.parametrize('declaration',['producer','scoring'])
def test_unchecked_model_copy_cannot_inject_authority_selector(make_composition,declaration):
    b=make_composition();service=build(b);reads,_=track_reads(b);called=[]
    def fake(*a):called.append(1);return True
    producer=b['plan'];scoring=b['scoring']
    if declaration=='producer':producer=producer.model_copy(update={'value_authority':fake})
    else:scoring=scoring.model_copy(update={'authority_id':'request-authority'})
    with pytest.raises(BaselineCompositionError):service.evaluate(scoring,producer,b['configuration'])
    assert not reads and not called and not b['predictor_calls']


def test_operator_snapshots_prevent_configuration_alias_downgrade(make_composition):
    b=make_composition();original=FeatureConfiguration.model_validate_json(b['configuration'].model_dump_json())
    service=build(b)
    with pytest.raises(TypeError):b['configuration'].output_dtypes['lag1']='float32'
    substituted=original.model_copy(update={'output_dtypes':{**dict(original.output_dtypes),'lag1':'float32'}})
    with pytest.raises(BaselineCompositionError):service.evaluate(b['scoring'],b['plan'],substituted)
    result=service.evaluate(b['scoring'],b['plan'],original)
    assert result.events  # Immutable operator snapshot, not the mutated caller alias, remains authoritative.


def test_other_service_callback_aliases_cannot_replace_owned_authorities(make_composition):
    b=make_composition();service=build(b)
    # The pre-existing legacy services used by fixture setup are NOT owned C0
    # instances. Replacing their callbacks does not alter selected dependencies.
    b['service'].verify_availability=lambda *_:True
    b['evaluator'].verify_revision=lambda *_:True
    b['options']['value_authority']=replace(b['options']['value_authority'],verify=lambda *_:True)
    substitute(b,lambda f:f.loc.__setitem__((f.period_start==b['at'](3),'value'),800.))
    with pytest.raises(BaselineCompositionError):execute(service,b)
    assert not b['predictor_calls']


@pytest.mark.parametrize('denial',['labels','revision','evaluation'])
def test_scoring_authorization_and_label_revision_are_not_bypassed(make_composition,denial):
    b=make_composition();service=build(b);b['permissions'][denial]=False
    with pytest.raises(BaselineCompositionError):execute(service,b)
    assert bool(b['predictor_calls'])==(denial!='evaluation')


def test_wrong_original_cutoff_is_not_authorized_by_matching_value_hash(make_composition):
    b=make_composition();service=build(b)
    data=b['plan'].model_dump();data['folds'][0]['training_origins']=(b['at'](5),)
    data['folds'][0]['model_fit_cutoff']=b['at'](8)
    plan=ForecastEvaluationPlan.model_validate(data)
    scoring=ForecastScoringPlan.model_validate({**b['scoring'].model_dump(),'producer_plan_digest':plan.digest})
    b['approved'].add(scoring.digest)  # Scoring approval alone is not independent historical approval.
    with pytest.raises(BaselineCompositionError):service.evaluate(scoring,plan,b['configuration'])
    assert b['value_calls'] and not b['predictor_calls']


def test_later_organization_terminal_value_denial_discards_combined_result(make_composition):
    b=make_composition(organizations=(2,3));authorize=b['options']['authorize_evaluation']
    target=(UUID(int=3),'fold-1',b['at'](8))
    def auth(ctx,stage):
        if stage=='return' and ctx.metadata is None:b['revoked'].add(target)
        return authorize(ctx,stage)
    service=build(b,authorize_evaluation=auth)
    with pytest.raises(BaselineCompositionError):execute(service,b)
    assert len(b['predictor_calls'])==12
    ctx,claims=b['value_calls'][-1]
    assert (ctx.metadata.reference.organization_id,ctx.fold_id,ctx.forecast_origin)==target
    assert all(c.organization_id==UUID(int=3) for c in claims)


def test_delegates_once_to_real_owned_rollout_and_scoring(make_composition,monkeypatch):
    from src.services.canonical_forecast_evaluation_service import CanonicalForecastEvaluationService
    from src.services.deterministic_forecast_rollout_service import DeterministicForecastRolloutService
    b=make_composition();calls={'evaluate':0,'owned':0}
    evaluate=CanonicalForecastEvaluationService.evaluate;owned=DeterministicForecastRolloutService._owned_rollout
    def evaluation(self,*a,**kw):calls['evaluate']+=1;return evaluate(self,*a,**kw)
    def rollout(self,*a,**kw):calls['owned']+=1;return owned(self,*a,**kw)
    monkeypatch.setattr(CanonicalForecastEvaluationService,'evaluate',evaluation)
    monkeypatch.setattr(DeterministicForecastRolloutService,'_owned_rollout',rollout)
    result=execute(build(b),b)
    assert result.events and calls=={'evaluate':1,'owned':1}


def test_malicious_operator_is_explicitly_outside_supported_request_boundary(make_composition):
    b=make_composition()
    def deliberately_lying_bootstrap_authority(*args):return True
    # This is an operator trust-assumption violation, NOT an ordinary request.
    service=build(b,value_authority=replace(b['options']['value_authority'],verify=deliberately_lying_bootstrap_authority))
    substitute(b,lambda f:f.loc.__setitem__((f.period_start==b['at'](3),'value'),800.))
    result=execute(service,b)
    assert result.events
    north=next(s.series_id for s in b['metadata'][0].series if s.dimensions['location']=='north')
    assert b['predictor_calls'][0].matrix.loc[(UUID(int=2),north,b['at'](4)),'lag1']==800.
    print('TRUST LIMIT: malicious bootstrap can lie; C0 does not authenticate Python callbacks or sandbox an operator')


@pytest.mark.parametrize('target',['producer','scoring'])
def test_request_cannot_raise_operator_resource_ceilings(make_composition,target):
    b=make_composition();service=build(b);reads,_=track_reads(b)
    producer=b['plan'];scoring=b['scoring']
    if target=='producer':
        data=producer.model_dump();data['budgets']['max_elapsed_seconds']=301
        producer=ForecastEvaluationPlan.model_validate(data)
        scoring=ForecastScoringPlan.model_validate({**scoring.model_dump(),'producer_plan_digest':producer.digest})
    else:
        data=scoring.model_dump();data['budgets']['max_elapsed_seconds']=301
        scoring=ForecastScoringPlan.model_validate(data)
    b['approved'].add(scoring.digest)
    with pytest.raises(BaselineCompositionError):service.evaluate(scoring,producer,b['configuration'])
    assert not reads and not b['predictor_calls']


def test_final_callback_elapsed_just_inside_operator_deadline_succeeds(make_composition):
    b=make_composition();clock=[0.];final=[False];authorize=b['options']['authorize_evaluation']
    value=b['options']['value_authority'].verify
    def approval(ctx,stage):
        if stage=='return' and ctx.metadata is None:final[0]=True
        return authorize(ctx,stage)
    def history(ctx,claims):
        if final[0] and ctx.target_period_end is not None:clock[0]=299.999
        return value(ctx,claims)
    service=build(b,elapsed_clock=lambda:clock[0],authorize_evaluation=approval,
        value_authority=replace(b['options']['value_authority'],verify=history))
    assert execute(service,b).events and clock[0]==299.999


@pytest.mark.parametrize('fault',['callback','capability','duplicate','unbounded','mutable_registry'])
def test_baseline_bootstrap_never_accepts_request_registered_producers(make_composition,fault):
    from src.data.contracts.forecast_evaluation import BaselineDefinition
    b=make_composition();reads,_=track_reads(b)
    d=BaselineDefinition(algorithm='last_observation_v1',lookback=4,supported_horizon=4)
    options={k:v for k,v in b['options'].items() if k not in ('predictor','capabilities')}
    options['baseline_definitions']=(d,)
    if fault=='callback':options['predictor']=lambda *_:True
    elif fault=='capability':options['capabilities']=b['plan'].predictor
    elif fault=='duplicate':options['baseline_definitions']=(d,d)
    elif fault=='unbounded':options['baseline_definitions']=(d,)*9
    else:options['baseline_definitions']=[d]
    with pytest.raises(BaselineCompositionError):CanonicalBaselineEvaluationService(**options)
    assert not reads and not b['predictor_calls']


def test_fixed_c0_has_no_implicit_baseline_registration(make_composition):
    from src.data.contracts.forecast_evaluation import BaselineDefinition
    b=make_composition();service=build(b);reads,_=track_reads(b)
    d=BaselineDefinition(algorithm='last_observation_v1',lookback=4,supported_horizon=4)
    with pytest.raises(BaselineCompositionError):service.evaluate_baseline(d,b['scoring'],b['plan'])
    assert not reads


def test_baseline_mode_cannot_fall_back_to_generic_fixed_producer_api(make_composition):
    from src.data.contracts.forecast_evaluation import BaselineDefinition
    b=make_composition();reads,_=track_reads(b)
    d=BaselineDefinition(algorithm='last_observation_v1',lookback=4,supported_horizon=4)
    options={k:v for k,v in b['options'].items() if k not in ('predictor','capabilities')}
    service=CanonicalBaselineEvaluationService(**options,baseline_definitions=(d,))
    with pytest.raises(BaselineCompositionError):execute(service,b)
    assert not reads and not b['predictor_calls']
