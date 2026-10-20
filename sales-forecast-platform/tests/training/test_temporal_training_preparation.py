"""Synthetic FINALIZED stores; no model fitting or predictor execution."""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from datetime import timedelta
from decimal import Decimal
import math
import runpy
import traceback
from uuid import UUID

import pandas as pd
import pyarrow as pa
import pytest

from src.data.contracts.dataset import (StoredDatasetReference,DatasetAccessContext,ConsistencyPolicy,
    VerifiedConsistencyEvidence,ScopedWarningAuthorization,StagingBudgets)
from src.data.contracts.features import FeatureConfiguration
from src.data.contracts.temporal_preparation import TemporalPreparationPlan,TemporalFold,TemporalBudgets
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
from src.providers.canonical_training_data_provider import CanonicalTrainingDataProvider
from src.providers.dataset_store_provider import StagingError
from src.repositories.local_dataset_repository import LocalDatasetRepository
from src.services.dataset_ingestion_service import DatasetIngestionService
from src.services.dataset_finalization_service import DatasetFinalizationService
from src.services.canonical_training_preparation_service import CanonicalTrainingPreparationService,TrainingPreparationError


@pytest.fixture
def make_temporal(tmp_path):
    old=runpy.run_path('tests/training/test_canonical_training_preparation.py')
    reference=runpy.run_path('tests/data/adapters/test_integration_foundation.py')
    base=old['make_bundle'].__wrapped__(tmp_path/'compatibility-reference')()
    def make(*,exo=False,organizations=(2,),raw_change=None,frequency='weekly',timezone='UTC',start='2020-01-06',decimal=False):
        cfg=base['configuration'].model_dump()
        cfg['execution']['temporal'].update(frequency=frequency,timezone=timezone,week_start=0 if frequency=='weekly' else None)
        if exo:
            cfg['canonical_inputs']=(*cfg['canonical_inputs'],'promotion');cfg['ordered_outputs']=(*cfg['ordered_outputs'],'promotion')
            cfg['category_encoding']['promotion']='ordinal';cfg['output_dtypes']['promotion']='int8';cfg['missing_value_behavior']['promotion']='reject'
            cfg['execution']['categories']=(*cfg['execution']['categories'],dict(field='promotion',unknown='use_code',unknown_code=-1,missing='reject'))
            cfg['execution']['exogenous']=[dict(field='promotion',availability='lagged',offset=1,missing='reject',availability_field='promotion_available_at',evidence_field='promotion_evidence')]
        configuration=FeatureConfiguration.model_validate(cfg)
        store=LocalDatasetRepository(tmp_path/str(__import__('uuid').uuid4()))
        provider=CanonicalTrainingDataProvider(store,authorize_read=lambda *_:True,clock=lambda:old['NOW'])
        service=CanonicalTrainingPreparationService(provider,authorize_preparation=lambda *_:True,verify_availability=lambda *_:True)
        bindings=[];raws={};metadata=[];step=1 if frequency=='daily' else 7
        at=lambda n:pd.Timestamp(start,tz=timezone)+pd.DateOffset(days=step*n)
        for org in organizations:
            rows=[]
            for region,delta in [('north',0),('south',100)]:
                for n,value in enumerate([2,-4,0,8,16,32,-2,0,4,8,16,32]):
                    value=value+delta+1000*(org-2)
                    rows.append(dict(branch=region,group='toys',reported=Decimal(value) if decimal else float(value),
                        **{'from':at(n),'to':at(n+1)},offer='A' if n<4 else 'B',released=at(n+1),
                        proof=f'release-{org}-{region}-{n}',promo_release=at(n)-pd.DateOffset(days=step),promo_proof=f'category-{org}-{region}-{n}'))
            raw=pd.DataFrame(rows)
            if raw_change:raw_change(raw)
            if decimal:
                raw['reported']=pd.Series(pa.array(raw.reported.tolist(),type=pa.decimal128(20,4)),
                    dtype=pd.ArrowDtype(pa.decimal128(20,4)))
            mapping=reference['config'](organization_id=UUID(int=org),frequency=frequency,timezone=timezone)
            data=mapping.model_dump();data['field_mappings'].update(available_at='released',availability_reference='proof')
            data['required_auxiliary_fields']=(*data['required_auxiliary_fields'],'available_at','availability_reference')
            if exo:data['field_mappings'].update(promotion_available_at='promo_release',promotion_evidence='promo_proof')
            mapping=type(mapping).model_validate(data)
            asset=reference['asset'].__wrapped__().model_copy(update={'organization_id':UUID(int=org),'timezone':timezone})
            manifest=DatasetIngestionService(store,authorize_ingestion=lambda *_:True).ingest(
                reference['MemoryAdapter'](raw,asset),mapping,dataset_id=UUID(int=100+org),batch_size=3)
            ref=StoredDatasetReference(manifest.organization_id,manifest.dataset_id,manifest.attempt_id,manifest.version_id)
            scope=DatasetAccessContext(ref,manifest.source_id,manifest.ingestion_batch_id,mapping.mapping_id,mapping.version,
                manifest.mapping_digest,mapping.target_metric,mapping.business_scope,'opaque-source','connection-1','production_training')
            now=old['NOW']
            evidence=VerifiedConsistencyEvidence(scope,'snapshot','snapshot_external_v1','method-1','1.0',now-timedelta(days=2),now-timedelta(hours=1),now-timedelta(minutes=1),now+timedelta(days=1))
            warning=ScopedWarningAuthorization(scope,'NEGATIVE_VALUE','value','keep','synthetic-warning',now-timedelta(hours=1),now+timedelta(days=1))
            DatasetFinalizationService(store,authorize=lambda *_:True,verify_consistency=lambda *_:True,verify_policy=lambda *_:True,
                verify_warning=lambda *_:True,decision_context_reference='synthetic-decision',clock=lambda:now).finalize(
                    ref,mapping,connection_reference='connection-1',consistency_policy=ConsistencyPolicy(scope,'SALES_GENERAL','1.0.0'),
                    consistency_evidence=evidence,warning_authorizations=(warning,))
            with provider.open_read(ref,purpose='production_training') as session:
                meta=session.metadata;list(session.batches())
            ids=tuple(s.series_id for s in meta.series)
            bindings.append(service._temporal_binding(meta,ids));metadata.append(meta);raws[org]=raw
        fold=TemporalFold(fold_id='fold-1',training_origins=(at(4),at(5)),validation_origins=(at(6),),test_origins=(at(7),),
            calibration_start=at(0),categorical_cutoff=at(4),model_fit_cutoff=at(6),assessment_cutoff=at(8))
        engine=CanonicalFeaturePipeline(configuration)
        plan=TemporalPreparationPlan(plan_id='temporal-synthetic',datasets=tuple(bindings),configuration_id=configuration.configuration_id,
            configuration_version=configuration.version,configuration_digest=engine.digest,frequency=frequency,calendar='gregorian',
            timezone=timezone,period_anchor='start',purpose='production_training',folds=(fold,))
        def verified(context,claims):
            raw=raws[context.metadata.reference.organization_id.int]
            return all(c.available_at==raw.iloc[int(c.source_record_id)]['released' if c.field=='value' else 'promo_release']
                       and c.evidence_reference==raw.iloc[int(c.source_record_id)]['proof' if c.field=='value' else 'promo_proof'] for c in claims)
        service.verify_availability=verified
        return dict(service=service,provider=provider,store=store,plan=plan,configuration=configuration,
                    raws=raws,metadata=metadata,at=at)
    return make


def prepare(b,**kwargs):
    return b['service'].prepare_temporal(b['plan'],b['configuration'],**kwargs)


def change_plan(b,**changes):
    data=b['plan'].model_dump();data.update(changes)
    b['plan']=TemporalPreparationPlan.model_validate(data)


def change_fold(b,**changes):
    data=b['plan'].folds[0].model_dump();data.update(changes)
    change_plan(b,folds=(TemporalFold.model_validate(data),))


def test_hand_calculated_multi_origin_features_labels_and_identity(make_temporal):
    b=make_temporal();result=prepare(b)
    assert len(result.origins)==4 and len(result.matrix)==8
    by_region={s.dimensions['location']:s.series_id for s in b['metadata'][0].series}
    north=by_region['north']
    first,second,validation,test=result.origins
    assert first.preparation.matrix.loc[(UUID(int=2),north,b['at'](4)),['lag1','mean4','std4']].tolist()==[8.,1.5,5.]
    row=second.preparation.matrix.loc[(UUID(int=2),north,b['at'](5))]
    assert row.lag1==16 and row.mean4==5 and row.std4==pytest.approx(math.sqrt(236/3))
    assert [g.preparation.labels.loc[(UUID(int=2),north,g.preparation.context.forecast_origin)] for g in result.origins]==[16.,32.,-2.,0.]
    assert tuple(result.matrix.index.names)==('fold','organization_id','series_id','metric','forecast_origin','horizon_step','target_period')
    assert result.matrix.index.is_unique and result.labels.index.equals(result.matrix.index)
    assert prepare(b).preparation_id==result.preparation_id


def test_one_capture_and_one_frozen_state_per_fold_after_lock_release(make_temporal,monkeypatch):
    b=make_temporal(exo=True);reads=[];fits=[];original_read=b['provider'].open_read;original_fit=CanonicalFeaturePipeline.fit_state
    @contextmanager
    def tracked_read(*args,**kwargs):
        reads.append(1)
        with original_read(*args,**kwargs) as session:yield session
    b['provider'].open_read=tracked_read
    def tracked_fit(self,*args,**kwargs):
        with b['store']._publication_session(StagingBudgets()):fits.append(1)
        return original_fit(self,*args,**kwargs)
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',tracked_fit)
    result=prepare(b)
    assert len(reads)==len(fits)==1
    assert len({g.preparation.fitted_state.state_id for g in result.origins})==1
    assert all(g.preparation.fitted_state.vocabularies['promotion']==('A',) for g in result.origins)
    assert result.origins[0].preparation.matrix.promotion.eq(0).all()
    assert all(g.preparation.matrix.promotion.eq(-1).all() for g in result.origins[1:])


@pytest.mark.parametrize('case',['future','missing','unverified'])
def test_category_calibration_evidence_rejects_before_any_fit(make_temporal,monkeypatch,case):
    def change(raw):
        if case=='future':raw.loc[0,'promo_release']=pd.Timestamp('2020-02-10',tz='UTC')
        if case=='missing':raw.loc[0,'promo_proof']=None
    b=make_temporal(exo=True,raw_change=change);calls=[]
    if case=='unverified':b['service'].verify_availability=lambda *_:False
    original=CanonicalFeaturePipeline.fit_state
    def fit(self,*args,**kwargs):calls.append(1);return original(self,*args,**kwargs)
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',fit)
    with pytest.raises(TrainingPreparationError):prepare(b)
    assert not calls


@pytest.mark.parametrize('partition',['training','validation','test'])
@pytest.mark.parametrize('available',['eligible','late'])
def test_labels_may_arrive_after_origin_but_must_meet_partition_cutoff(make_temporal,partition,available):
    # Delayed training label is at the LAST training origin. The next requested
    # origin follows release; no intermediate unavailable target becomes history.
    def change(raw):
        n={'training':5,'validation':7,'test':8}[partition]
        limit={'training':pd.Timestamp('2020-02-21',tz='UTC'),
               'validation':pd.Timestamp('2020-03-09',tz='UTC'),'test':pd.Timestamp('2020-03-09',tz='UTC')}[partition]
        for position in (n,12+n):raw.loc[position,'released']=limit if available=='eligible' else limit+pd.DateOffset(days=1)
    b=make_temporal(raw_change=change)
    change_fold(b,model_fit_cutoff=pd.Timestamp('2020-02-21',tz='UTC'),
        validation_origins=(b['at'](7),),test_origins=(b['at'](8),),assessment_cutoff=b['at'](9))
    if available=='late':
        with pytest.raises(TrainingPreparationError):prepare(b)
    elif partition=='validation':
        # A validation label released at E cannot yet become history for the
        # requested intervening test origin: that history must reject the gap.
        with pytest.raises(TrainingPreparationError):prepare(b)
        change_fold(b,test_origins=())
        assert prepare(b).origins[-1].partition=='validation'
    else:
        result=prepare(b)
        assert len(result.origins)==4
        if partition=='training':
            assert sorted(result.origins[2].preparation.matrix.mean4)==[13.5,113.5]


def test_future_actual_values_never_change_earlier_origin_features(make_temporal):
    b=make_temporal();before=prepare(b).origins[0].preparation.matrix
    def changed(raw):raw.loc[raw['from']>=pd.Timestamp('2020-02-10',tz='UTC'),'reported']=999.
    other=make_temporal(raw_change=changed)
    pd.testing.assert_frame_equal(before,prepare(other).origins[0].preparation.matrix)


@pytest.mark.parametrize('fault',['missing_period','short_history','delayed_history'])
def test_missing_or_unavailable_history_rejects_without_dropping(make_temporal,fault):
    def change(raw):
        if fault=='missing_period':raw.drop(index=[2],inplace=True);raw.reset_index(drop=True,inplace=True)
        if fault=='delayed_history':raw.loc[3,'released']=pd.Timestamp('2020-02-10',tz='UTC')
    b=make_temporal(raw_change=change)
    if fault=='short_history':change_fold(b,training_origins=(b['at'](3),b['at'](4)),categorical_cutoff=b['at'](3))
    with pytest.raises(TrainingPreparationError):prepare(b)


def test_organizations_and_folds_do_not_share_category_state(make_temporal):
    b=make_temporal(exo=True,organizations=(2,3))
    second=TemporalFold(fold_id='fold-2',training_origins=(b['at'](5),b['at'](6)),validation_origins=(b['at'](8),),
        calibration_start=b['at'](0),categorical_cutoff=b['at'](5),model_fit_cutoff=b['at'](7),assessment_cutoff=b['at'](9))
    change_plan(b,folds=(*b['plan'].folds,second));result=prepare(b)
    states={(g.fold_id,g.preparation.context.metadata.reference.organization_id):g.preparation.fitted_state for g in result.origins}
    assert len(states)==4 and len({s.state_id for s in states.values()})==4
    for (fold_id,org),state in states.items():
        assert state.organization_ids==(org,)
        assert state.vocabularies['promotion']==(('A',) if fold_id=='fold-1' else ('A','B'))
    one=[g for g in result.origins if g.fold_id=='fold-1' and g.preparation.context.metadata.reference.organization_id==UUID(int=2)][0]
    two=[g for g in result.origins if g.fold_id=='fold-1' and g.preparation.context.metadata.reference.organization_id==UUID(int=3)][0]
    assert sorted(two.preparation.labels)==[v+1000 for v in sorted(one.preparation.labels)]


@pytest.mark.parametrize('fault',['version','mapping','source','admission','series_definitions','other_tenant_series','configuration'])
def test_binding_replay_rejects(make_temporal,fault):
    b=make_temporal(organizations=(2,3));data=b['plan'].model_dump()
    if fault=='configuration':data['configuration_digest']='0'*64
    elif fault=='version':
        data['datasets'][0]['reference']['version_id']='0'*64;data['datasets'][0]['manifest_digest']='0'*64
    elif fault=='mapping':data['datasets'][0]['mapping_version']='2.0'
    elif fault=='source':data['datasets'][0]['source_id']=UUID(int=999)
    elif fault=='admission':data['datasets'][0]['admission_digest']='0'*64
    elif fault=='series_definitions':data['datasets'][0]['series_definition_digest']='0'*64
    else:data['datasets'][1]['series_ids']=data['datasets'][0]['series_ids']
    b['plan']=TemporalPreparationPlan.model_validate(data)
    with pytest.raises(TrainingPreparationError):prepare(b)


def test_higher_horizon_and_planning_only_never_execute(make_temporal,monkeypatch):
    b=make_temporal();change_plan(b,capability='planning_only',forecast_horizon=3)
    def no_read(*args,**kwargs):pytest.fail('unsupported plan must reject before reader')
    monkeypatch.setattr(b['provider'],'open_read',no_read)
    with pytest.raises(TrainingPreparationError,match='horizon one only'):prepare(b)


@pytest.mark.parametrize('frequency,timezone,start,expected',[
    ('weekly','UTC','2019-12-09','2020-01-06'),
    ('daily','America/New_York','2020-03-05','2020-03-09'),
    ('daily','America/New_York','2020-10-29','2020-11-02'),
])
def test_calendar_iso_year_and_dst_use_local_periods(make_temporal,frequency,timezone,start,expected):
    b=make_temporal(frequency=frequency,timezone=timezone,start=start);result=prepare(b)
    origin=result.origins[0].preparation.context.forecast_origin
    assert origin==pd.Timestamp(expected,tz=timezone)
    assert result.origins[1].preparation.context.forecast_origin==origin+pd.DateOffset(days=1 if frequency=='daily' else 7)
    assert all(str(g.preparation.matrix.index.get_level_values('period_start').tz)==timezone for g in result.origins)


@pytest.mark.parametrize('failure',['late_chunk','late_read','cleanup'])
def test_late_integrity_read_and_cleanup_failures_return_nothing(make_temporal,failure):
    b=make_temporal()
    if failure=='late_chunk':
        ref=b['plan'].datasets[0].reference;path=b['store'].root/str(ref.attempt_id)/'7-canonical.parquet'
        path.chmod(0o600);path.write_bytes(b'corrupt')
    else:
        original=b['provider'].open_read
        @contextmanager
        def failed(*args,**kwargs):
            with original(*args,**kwargs) as session:
                if failure=='late_read':
                    batches=session.batches
                    def broken():
                        for n,frame in enumerate(batches()):
                            if n==2:raise StagingError('SYNTHETIC_READ_SECRET')
                            yield frame
                    session.batches=broken
                yield session
            if failure=='cleanup':raise StagingError('SYNTHETIC_CLOSE_SECRET')
        b['provider'].open_read=failed
    with pytest.raises(TrainingPreparationError) as exc:prepare(b)
    assert 'SECRET' not in ''.join(traceback.format_exception(exc.value))
    with b['store']._publication_session(StagingBudgets()):pass


@pytest.mark.parametrize('boundary',['origin','fold','return','availability','read_return'])
def test_revocation_discards_all_prepared_origins(make_temporal,boundary):
    b=make_temporal();calls=[]
    def authorize(context,phase):
        if phase==boundary:
            calls.append(1)
            return False if boundary=='return' or len(calls)==2 else True
        if boundary=='read_return' and phase=='return':b['provider'].authorize_read=lambda *_:False
        return True
    b['service'].authorize=authorize
    if boundary=='fold':
        second=b['plan'].folds[0].model_dump();second.update(fold_id='fold-2',validation_origins=(),test_origins=(),model_fit_cutoff=b['at'](7))
        change_plan(b,folds=(*b['plan'].folds,TemporalFold.model_validate(second)))
    if boundary=='availability':
        def verify(*args):calls.append(1);return len(calls)<3
        b['service'].verify_availability=verify
    with pytest.raises(TrainingPreparationError):prepare(b)
    with b['store']._publication_session(StagingBudgets()):pass


@pytest.mark.parametrize('mode',['cancel','deadline','callback_secret'])
def test_final_callbacks_cannot_return_after_interruption(make_temporal,mode):
    b=make_temporal();status={'cancelled':False,'elapsed':0};b['service'].elapsed_clock=lambda:status['elapsed']
    def authorize(context,phase):
        if phase=='return':
            if mode=='cancel':status['cancelled']=True
            elif mode=='deadline':status['elapsed']=300
            else:raise TrainingPreparationError('FINAL_CALLBACK_SECRET')
        return True
    b['service'].authorize=authorize
    with pytest.raises(TrainingPreparationError) as exc:prepare(b,cancelled=lambda:status['cancelled'])
    assert 'SECRET' not in ''.join(traceback.format_exception(exc.value))
    with b['store']._publication_session(StagingBudgets()):pass


@pytest.mark.parametrize('budget',['max_captured_rows','max_decoded_bytes','max_output_cells','max_fitted_state_bytes','max_assembly_bytes','max_work_cells'])
def test_aggregate_budget_limits_fail_closed(make_temporal,budget):
    b=make_temporal();data=b['plan'].budgets.model_dump();data[budget]=1
    change_plan(b,budgets=TemporalBudgets.model_validate(data))
    with pytest.raises(TrainingPreparationError):prepare(b)


def test_detached_output_and_original_decimal_labels_remain_immutable(make_temporal):
    b=make_temporal(decimal=True);result=prepare(b)
    assert result.original_labels[0].as_tuple()==Decimal('16.0000').as_tuple()
    frame=result.matrix;labels=result.labels;frame.iloc[0,0]=99;labels.iloc[0]=999
    assert result.matrix.iloc[0,0]!=99 and result.labels.iloc[0]!=999
    with pytest.raises(FrozenInstanceError):result.origins=()
    stored=[v for frame in b['provider'].read_batches(b['plan'].datasets[0].reference,purpose='production_training') for v in frame.value]
    assert Decimal(-4) in stored and Decimal(0) in stored


def test_single_origin_api_retains_preexisting_reference_behavior(make_temporal):
    b=make_temporal();binding=b['plan'].datasets[0]
    from src.data.contracts.forecast import ForecastConfig
    old=b['service'].prepare(binding.reference,b['configuration'],forecast_origin=b['at'](4),training_cutoff=b['at'](4),
        forecast_config=ForecastConfig(forecast_frequency='weekly',forecast_horizon=1),series_ids=binding.series_ids)
    pd.testing.assert_frame_equal(old.matrix,prepare(b).origins[0].preparation.matrix)
    pd.testing.assert_series_equal(old.labels,prepare(b).origins[0].preparation.labels)


@pytest.mark.parametrize('boundary',['availability','external','read'])
@pytest.mark.parametrize('mode',['cancel','deadline'])
def test_interruption_inside_other_final_callbacks_returns_no_result(make_temporal,boundary,mode):
    b=make_temporal(exo=boundary=='external');status={'cancelled':False,'elapsed':0,'fired':False};calls=[]
    b['service'].elapsed_clock=lambda:status['elapsed']
    def interrupt():
        status['fired']=True
        if mode=='cancel':status['cancelled']=True
        else:status['elapsed']=301
        return True
    if boundary=='availability':
        verified=b['service'].verify_availability
        def verify(*args):
            calls.append(1)
            return interrupt() if len(calls)==7 else verified(*args)
        b['service'].verify_availability=verify
    elif boundary=='external':
        config=b['configuration'].model_dump();rule=config['execution']['exogenous'][0]
        rule.update(availability='external_forecast',offset=None)
        b['configuration']=FeatureConfiguration.model_validate(config)
        engine=CanonicalFeaturePipeline(b['configuration']);change_plan(b,configuration_digest=engine.digest)
        def verify(*args):calls.append(1);return interrupt() if len(calls)==9 else True
        b['service'].authorize_external=verify
    else:
        current=b['provider'].require_current
        def verify(*args,**kwargs):
            calls.append(1)
            if len(calls)==3:
                b['provider'].authorize_read=lambda *_:interrupt()
            return current(*args,**kwargs)
        b['provider'].require_current=verify
    with pytest.raises(TrainingPreparationError):prepare(b,cancelled=lambda:status['cancelled'])
    assert status['fired']
    with b['store']._publication_session(StagingBudgets()):pass


@pytest.mark.parametrize('deadline',[299,300])
def test_temporal_deadline_boundary_is_explicit(make_temporal,deadline):
    b=make_temporal();clock=[0];b['service'].elapsed_clock=lambda:clock[0]
    def approve(context,phase):
        if phase=='return':clock[0]=deadline
        return True
    b['service'].authorize=approve
    if deadline==300:
        with pytest.raises(TrainingPreparationError):prepare(b)
    else:assert len(prepare(b).origins)==4


def test_authorization_can_deny_one_organization_without_partial_result(make_temporal):
    b=make_temporal(organizations=(2,3));visited=[]
    def authorize(context,phase):
        visited.append(context.metadata.reference.organization_id)
        return context.metadata.reference.organization_id==UUID(int=2)
    b['service'].authorize=authorize
    with pytest.raises(TrainingPreparationError):prepare(b)
    assert UUID(int=2) in visited and UUID(int=3) in visited
    with b['store']._publication_session(StagingBudgets()):pass


def test_plan_identity_binds_state_and_does_not_accept_external_fitted_state(make_temporal):
    b=make_temporal();first=prepare(b)
    change_plan(b,plan_id='different-authorized-plan');second=prepare(b)
    assert first.preparation_id!=second.preparation_id
    assert first.origins[0].preparation.fitted_state.state_id!=second.origins[0].preparation.fitted_state.state_id
    with pytest.raises(TypeError):prepare(b,fitted_state=first.origins[0].preparation.fitted_state)


def test_strict_and_synthetic_bounded_numeric_policy_share_existing_executor(make_temporal):
    def changed(raw):raw.loc[raw['from']==pd.Timestamp('2020-02-03',tz='UTC'),'reported']=Decimal('19.99')
    b=make_temporal(decimal=True,raw_change=changed)
    with pytest.raises(TrainingPreparationError):prepare(b)
    config=b['configuration'].model_dump();config['execution'].update(numeric_conversion='versioned_float64',numeric_policies=[dict(
        field='value',measurement=config['execution']['measurement'],mode='BOUNDED_FLOAT64',absolute_tolerance='1e-12',relative_tolerance='0')])
    b['configuration']=FeatureConfiguration.model_validate(config)
    engine=CanonicalFeaturePipeline(b['configuration']);change_plan(b,configuration_digest=engine.digest)
    result=prepare(b)
    assert result.origins[0].preparation.labels.eq(19.99).all()
    assert all(v.as_tuple()==Decimal('19.9900').as_tuple() for v in result.origins[0].preparation.original_labels)
    assert result.origins[0].preparation.schema.executor_version=='1.1.0'


def test_aggregate_origin_limit_counts_organizations_and_folds(make_temporal):
    b=make_temporal(organizations=(2,3))
    with pytest.raises(ValueError):change_plan(b,budgets=TemporalBudgets(max_origins=7))


def test_individually_small_outputs_cannot_bypass_aggregate_limit(make_temporal,monkeypatch):
    b=make_temporal();change_plan(b,budgets=TemporalBudgets(max_output_cells=15))
    # Each origin needs only 8 cells; all four need 32. Refuse before reading.
    def unexpected_read(*args,**kwargs):pytest.fail('over-budget plan performed a read')
    monkeypatch.setattr(b['provider'],'open_read',unexpected_read)
    with pytest.raises(TrainingPreparationError):prepare(b)


def test_read_cancellation_closes_session_and_fresh_retry_succeeds(make_temporal):
    b=make_temporal();calls=[]
    with pytest.raises(TrainingPreparationError):prepare(b,cancelled=lambda:(calls.append(1) or len(calls)>2))
    with b['store']._publication_session(StagingBudgets()):pass
    assert len(prepare(b).origins)==4


def test_signed_zero_labels_are_not_clipped_or_resigned(make_temporal):
    def change(raw):raw.loc[raw['from']==pd.Timestamp('2020-02-03',tz='UTC'),'reported']=-0.0
    b=make_temporal(raw_change=change);first=prepare(b).origins[0].preparation
    assert all(math.copysign(1,v)==-1 for v in first.original_labels)
    assert all(math.copysign(1,v)==-1 for v in first.converted_labels)


def test_single_origin_preserves_field_verifier_callback_claims(make_temporal):
    b=make_temporal(exo=True);binding=b['plan'].datasets[0];claims=[]
    verified=b['service'].verify_availability
    def verify(context,items):claims.append(items);return verified(context,items)
    b['service'].verify_availability=verify
    from src.data.contracts.forecast import ForecastConfig
    b['service'].prepare(binding.reference,b['configuration'],forecast_origin=b['at'](4),training_cutoff=b['at'](4),
        forecast_config=ForecastConfig(forecast_frequency='weekly',forecast_horizon=1),series_ids=binding.series_ids)
    assert [len(items) for items in claims]==[48,8,56]
    assert claims[-1]==claims[0]+claims[1]


def _revoke_after_origin(monkeypatch, bundle, origin, revoked, organization=2):
    """Simulate concurrent revocation after computation, without changing data."""
    original=CanonicalFeaturePipeline.prepare_training
    def compute(self,history,*args,**kwargs):
        matrix=original(self,history,*args,**kwargs)
        if kwargs['forecast_origin']==origin and history.organization_id.iloc[0]==UUID(int=organization):
            revoked[0]=True
        return matrix
    monkeypatch.setattr(CanonicalFeaturePipeline,'prepare_training',compute)


@pytest.mark.parametrize('verification',['availability','authorization'])
@pytest.mark.parametrize('origin_number',[5,7],ids=['middle-origin','february-24'])
def test_scoped_origin_revocation_after_computation_rejects_entire_request(make_temporal,monkeypatch,verification,origin_number):
    b=make_temporal();revoked=[False];origin=b['at'](origin_number);seen=[]
    _revoke_after_origin(monkeypatch,b,origin,revoked)
    original=b['service'].verify_availability
    if verification=='availability':
        def verify(context,claims):
            seen.append((context,revoked[0]))
            return not(revoked[0] and context.forecast_origin==origin) and original(context,claims)
        b['service'].verify_availability=verify
    else:
        def authorize(context,phase):
            seen.append((context,revoked[0]))
            return not(revoked[0] and context.fold_id=='fold-1' and context.forecast_origin==origin)
        b['service'].authorize=authorize
    with pytest.raises(TrainingPreparationError):prepare(b)
    assert any(c.forecast_origin==origin and not r for c,r in seen)
    assert any(c.fold_id=='fold-1' and c.forecast_origin==origin and r for c,r in seen)
    assert any(c.forecast_origin==b['at'](4) and r for c,r in seen)
    with b['store']._publication_session(StagingBudgets()):pass


@pytest.mark.parametrize('verification',['availability','authorization'])
def test_later_fold_scoped_revocation_discards_earlier_fold_results(make_temporal,monkeypatch,verification):
    b=make_temporal();revoked=[False];seen=[]
    second=TemporalFold(fold_id='fold-2',training_origins=(b['at'](5),b['at'](6)),validation_origins=(b['at'](8),),
        calibration_start=b['at'](0),categorical_cutoff=b['at'](5),model_fit_cutoff=b['at'](7),assessment_cutoff=b['at'](9))
    change_plan(b,folds=(*b['plan'].folds,second))
    _revoke_after_origin(monkeypatch,b,b['at'](8),revoked)
    original=b['service'].verify_availability
    def check(context):
        seen.append((context.fold_id,context.forecast_origin,revoked[0]))
        return not(revoked[0] and context.fold_id=='fold-2' and context.forecast_origin==b['at'](8))
    if verification=='availability':b['service'].verify_availability=lambda c,claims:check(c) and original(c,claims)
    else:b['service'].authorize=lambda c,phase:check(c)
    with pytest.raises(TrainingPreparationError):prepare(b)
    assert ('fold-2',b['at'](8),True) in seen
    assert ('fold-1',b['at'](4),True) in seen


@pytest.mark.parametrize('verification',['availability','authorization'])
def test_one_organization_scoped_revocation_discards_combined_result(make_temporal,monkeypatch,verification):
    b=make_temporal(organizations=(2,3));revoked=[False];seen=[]
    _revoke_after_origin(monkeypatch,b,b['at'](7),revoked,organization=3)
    original=b['service'].verify_availability
    def check(context):
        org=context.metadata.reference.organization_id
        seen.append((org,context.forecast_origin,revoked[0]))
        return not(revoked[0] and org==UUID(int=3) and context.forecast_origin==b['at'](7))
    if verification=='availability':b['service'].verify_availability=lambda c,claims:check(c) and original(c,claims)
    else:b['service'].authorize=lambda c,phase:check(c)
    with pytest.raises(TrainingPreparationError):prepare(b)
    assert (UUID(int=3),b['at'](7),True) in seen
    assert (UUID(int=2),b['at'](7),True) in seen


def test_all_final_origin_checks_reuse_exact_contexts_and_evidence_without_locks(make_temporal):
    b=make_temporal(exo=True,organizations=(2,3));verified=[];authorized=[]
    original=b['service'].verify_availability
    def verify(context,claims):
        if context.target_period_end is not None:
            with b['store']._publication_session(StagingBudgets()):pass
            verified.append((context,claims))
        return original(context,claims)
    def authorize(context,phase):
        if context.target_period_end is not None:authorized.append((context,phase))
        return True
    b['service'].verify_availability=verify;b['service'].authorize=authorize
    result=prepare(b)
    assert len(result.origins)==8 and len(verified)==16 and len(authorized)==16
    for initial,final in zip(verified[:8],verified[8:]):
        assert initial[0] is final[0] and initial[1] is final[1]
        assert initial[0].target_period_end==initial[0].forecast_origin+pd.DateOffset(days=7)
        assert initial[0].fold_id=='fold-1'
        assert initial[0].plan_digest==b['plan'].digest
        assert initial[0].metadata.reference in tuple(d.reference for d in b['plan'].datasets)
    assert [c for c,p in authorized if p=='origin']==[c for c,p in authorized if p=='return']
    north=next(s.series_id for s in b['metadata'][0].series if s.dimensions['location']=='north')
    assert result.origins[0].preparation.matrix.loc[(UUID(int=2),north,b['at'](4)),['lag1','mean4','std4']].tolist()==[8.,1.5,5.]


@pytest.mark.parametrize('evidence',['calibration','external'])
def test_scoped_calibration_and_external_evidence_revocation_rejects(make_temporal,monkeypatch,evidence):
    b=make_temporal(exo=True);revoked=[False];seen=[]
    if evidence=='external':
        config=b['configuration'].model_dump();config['execution']['exogenous'][0].update(availability='external_forecast',offset=None)
        b['configuration']=FeatureConfiguration.model_validate(config)
        change_plan(b,configuration_digest=CanonicalFeaturePipeline(b['configuration']).digest)
        def external(context,request):
            seen.append((context,request,revoked[0]))
            return not(revoked[0] and context.forecast_origin==b['at'](7))
        b['service'].authorize_external=external
    else:
        original=b['service'].verify_availability
        def verify(context,claims):
            if claims and all(c.field=='promotion' and c.period_end<=context.training_cutoff for c in claims):
                seen.append((context,claims,revoked[0]))
                assert context.training_cutoff==b['at'](4)
                if revoked[0]:return False
            return original(context,claims)
        b['service'].verify_availability=verify
    _revoke_after_origin(monkeypatch,b,b['at'](7),revoked)
    with pytest.raises(TrainingPreparationError):prepare(b)
    assert any(r for c,claims,r in seen)
    assert any(not r for c,claims,r in seen)
    if evidence=='external':
        final=next((c,q) for c,q,r in seen if r and c.forecast_origin==b['at'](7))
        assert any(c is final[0] and q is final[1] for c,q,r in seen if not r)


@pytest.mark.parametrize('mode',['cancel','deadline'])
@pytest.mark.parametrize('callback',['availability','authorization'])
def test_final_scoped_callbacks_cannot_escape_cancellation_or_deadline(make_temporal,mode,callback):
    b=make_temporal();status={'cancelled':False,'elapsed':0};counts={}
    b['service'].elapsed_clock=lambda:status['elapsed']
    original=b['service'].verify_availability
    def interrupt(context):
        if context.target_period_end is None:return
        key=(context.metadata.reference,context.fold_id,context.forecast_origin)
        counts[key]=counts.get(key,0)+1
        if context.forecast_origin==b['at'](7) and counts[key]==2:
            if mode=='cancel':status['cancelled']=True
            else:status['elapsed']=300
    if callback=='availability':
        def verify(c,claims):interrupt(c);return original(c,claims)
        b['service'].verify_availability=verify
    else:
        def authorize(c,phase):interrupt(c);return True
        b['service'].authorize=authorize
    with pytest.raises(TrainingPreparationError):prepare(b,cancelled=lambda:status['cancelled'])
    assert status['cancelled'] or status['elapsed']==300
    with b['store']._publication_session(StagingBudgets()):pass


@pytest.mark.parametrize('callback',['availability','authorization'])
def test_final_scoped_callback_exceptions_are_sanitized(make_temporal,callback):
    b=make_temporal();seen=set();original=b['service'].verify_availability
    def check(context):
        if context.target_period_end is not None:
            key=(context.fold_id,context.forecast_origin)
            if key in seen:raise TrainingPreparationError('SCOPED_SYNTHETIC_SECRET')
            seen.add(key)
        return True
    if callback=='availability':b['service'].verify_availability=lambda c,claims:check(c) and original(c,claims)
    else:b['service'].authorize=lambda c,phase:check(c)
    with pytest.raises(TrainingPreparationError) as exc:prepare(b)
    assert 'SCOPED_SYNTHETIC_SECRET' not in ''.join(traceback.format_exception(exc.value))


def test_same_series_id_cannot_substitute_another_organizations_verified_scope(make_temporal):
    b=make_temporal(organizations=(2,3));data=b['plan'].model_dump()
    # Canonical series IDs are organization-scoped. Reusing the same IDs under
    # the second organization must fail against its independently verified metadata.
    data['datasets'][1]['series_ids']=data['datasets'][0]['series_ids']
    b['plan']=TemporalPreparationPlan.model_validate(data)
    with pytest.raises(TrainingPreparationError):prepare(b)


@pytest.mark.parametrize('period_binding',['missing','mismatched'])
def test_missing_or_mismatched_origin_period_binding_cannot_fall_back_to_fold(make_temporal,monkeypatch,period_binding):
    # Fault injection checks the internal binding guard, not a Python sandbox.
    from dataclasses import replace
    import src.services.canonical_training_preparation_service as module
    b=make_temporal();original=module.TemporalPreparationContext
    def context(*args,**kwargs):
        value=original(*args,**kwargs)
        if value.target_period_end is not None:
            return replace(value,target_period_end=None if period_binding=='missing'
                           else value.target_period_end+pd.DateOffset(days=7))
        return value
    monkeypatch.setattr(module,'TemporalPreparationContext',context)
    with pytest.raises(TrainingPreparationError):prepare(b)


@pytest.mark.parametrize('revoker',['global_availability','global_authorization','read_authorization'])
@pytest.mark.parametrize('origin_number',[4,5,7],ids=['first','middle','february-24'])
def test_broader_callback_revocation_precedes_terminal_origin_checks(make_temporal,monkeypatch,revoker,origin_number):
    b=make_temporal();state={'revoked':False,'read_final':False};events=[];original=b['service'].verify_availability
    target=b['at'](origin_number)
    def availability(context,claims):
        if context.target_period_end is not None:events.append(('availability',context.fold_id,context.forecast_origin,state['revoked']))
        return not(revoker=='global_availability' and state['revoked'] and context.forecast_origin==target) and original(context,claims)
    def authorize(context,phase):
        if phase=='return' and context.forecast_origin is None:
            events.append(('global',None,None,state['revoked']))
            if revoker.startswith('global_'):state['revoked']=True
            state['read_final']=True
        if context.target_period_end is not None:events.append((phase,context.fold_id,context.forecast_origin,state['revoked']))
        return not(revoker!='global_availability' and state['revoked'] and context.fold_id=='fold-1' and context.forecast_origin==target)
    def read(*args):
        if state['read_final']:
            events.append(('read',None,None,state['revoked']))
            if revoker=='read_authorization':state['revoked']=True
        return True
    b['service'].verify_availability=availability;b['service'].authorize=authorize;b['provider'].authorize_read=read
    with pytest.raises(TrainingPreparationError):prepare(b)
    broad=max(i for i,e in enumerate(events) if e[0] in ('global','read'))
    final=[(i,e) for i,e in enumerate(events) if i>broad and e[1]=='fold-1' and e[2]==target]
    assert final and any(e[3] for i,e in final)
    assert any(e[0]=='availability' for i,e in final)
    if revoker!='global_availability':assert any(e[0]=='return' for i,e in final)
    with b['store']._publication_session(StagingBudgets()):pass


@pytest.mark.parametrize('scope',['later_fold','other_organization'])
def test_broader_callback_revocation_retains_later_fold_and_tenant_scope(make_temporal,scope):
    b=make_temporal(organizations=(2,3) if scope=='other_organization' else (2,));state={'revoked':False};seen=[]
    if scope=='later_fold':
        second=TemporalFold(fold_id='fold-2',training_origins=(b['at'](5),b['at'](6)),validation_origins=(b['at'](8),),
            calibration_start=b['at'](0),categorical_cutoff=b['at'](5),model_fit_cutoff=b['at'](7),assessment_cutoff=b['at'](9))
        change_plan(b,folds=(*b['plan'].folds,second))
    target=b['at'](8 if scope=='later_fold' else 7);org=UUID(int=3 if scope=='other_organization' else 2)
    fold='fold-2' if scope=='later_fold' else 'fold-1'
    def authorize(context,phase):
        if phase=='return' and context.forecast_origin is None:state['revoked']=True
        if phase=='return' and context.target_period_end is not None:
            seen.append((context.fold_id,context.forecast_origin,context.metadata.reference.organization_id,state['revoked']))
        return not(state['revoked'] and context.fold_id==fold and context.forecast_origin==target
                   and context.metadata.reference.organization_id==org)
    b['service'].authorize=authorize
    with pytest.raises(TrainingPreparationError):prepare(b)
    assert (fold,target,org,True) in seen


@pytest.mark.parametrize('mode',['valid','external_revokes_origin'])
def test_terminal_scoped_phase_follows_all_broader_callbacks_and_result_assembly(make_temporal,monkeypatch,mode):
    import src.services.canonical_training_preparation_service as module
    b=make_temporal(exo=True);conf=b['configuration'].model_dump()
    conf['execution']['exogenous'][0].update(availability='external_forecast',offset=None)
    b['configuration']=FeatureConfiguration.model_validate(conf)
    change_plan(b,configuration_digest=CanonicalFeaturePipeline(b['configuration']).digest)
    state={'computed':False,'revoked':False};events=[];initial={}
    original_compute=CanonicalFeaturePipeline.prepare_training;original_verify=b['service'].verify_availability
    original_result=module.TemporalPreparationResult
    def compute(self,*args,**kwargs):
        result=original_compute(self,*args,**kwargs)
        if kwargs['forecast_origin']==b['at'](7):state['computed']=True
        return result
    def availability(context,claims):
        if context.target_period_end is not None:
            key=(context.fold_id,context.forecast_origin)
            if not state['computed']:initial[key]=(context,claims)
            else:
                assert context is initial[key][0] and claims is initial[key][1]
                events.append(('scoped_availability',context.forecast_origin))
        elif state['computed']:events.append(('calibration',context.training_cutoff))
        return original_verify(context,claims)
    def authorize(context,phase):
        if state['computed']:
            events.append(('scoped_authorization' if context.target_period_end is not None else 'global',context.forecast_origin))
        return not(state['revoked'] and context.forecast_origin==b['at'](7))
    def external(context,request):
        if state['computed']:
            events.append(('external',context.forecast_origin))
            if mode=='external_revokes_origin' and context.forecast_origin==b['at'](7):state['revoked']=True
        return True
    def read(*args):
        if state['computed']:events.append(('read',None))
        return True
    def result(*args,**kwargs):events.append(('assemble_result',None));return original_result(*args,**kwargs)
    def cancelled():
        if state['computed']:events.append(('checkpoint',None))
        return False
    monkeypatch.setattr(CanonicalFeaturePipeline,'prepare_training',compute)
    monkeypatch.setattr(module,'TemporalPreparationResult',result)
    b['service'].verify_availability=availability;b['service'].authorize=authorize
    b['service'].authorize_external=external;b['provider'].authorize_read=read
    if mode=='valid':
        prepared=prepare(b,cancelled=cancelled)
        assert len(prepared.origins)==4
        north=next(s.series_id for s in b['metadata'][0].series if s.dimensions['location']=='north')
        assert prepared.origins[0].preparation.matrix.loc[(UUID(int=2),north,b['at'](4)),['lag1','mean4','std4']].tolist()==[8.,1.5,5.]
    else:
        with pytest.raises(TrainingPreparationError):prepare(b,cancelled=cancelled)
    terminal=next(i for i,e in enumerate(events) if e[0]=='scoped_availability')
    assert {'calibration','external','global','read','assemble_result'}<=set(e[0] for e in events[:terminal])
    assert all(e[0] in ('scoped_availability','scoped_authorization','checkpoint') for e in events[terminal:])
    assert [e[1] for e in events if e[0]=='scoped_availability']==[b['at'](i) for i in range(4,8)]
    assert [e[1] for e in events if e[0]=='scoped_authorization']==[b['at'](i) for i in range(4,8)]
    if mode=='valid':assert events[-1]==('checkpoint',None)


@pytest.mark.parametrize('declaration',('folds','nested_origin','nested_budget'))
@pytest.mark.parametrize('channel',('warnings','stderr','logging'))
def test_public_temporal_malformed_plan_diagnostics_are_sanitized(make_temporal,capsys,caplog,declaration,channel):
    """Reject before collaborators, without serializer disclosure on any channel."""
    import logging
    import sys
    import warnings

    b=make_temporal();plan=b['plan'];secret='P2KEY927'
    def malformed(obj,**changes):
        return type(obj).model_construct(**{**obj.__dict__,**changes})
    if declaration=='folds':
        plan=malformed(plan,folds=(secret,))
    elif declaration=='nested_origin':
        plan=malformed(plan,folds=(malformed(plan.folds[0],training_origins=(secret,)),))
    else:
        plan=malformed(plan,budgets=malformed(plan.budgets,max_series=secret))
    calls=[]
    def unexpected(*args,**kwargs):
        calls.append(True)
        raise AssertionError('malformed plan reached transformation')
    b['service']._prepare_temporal=unexpected
    capture_enabled=logging._warnings_showwarning is not None
    try:
        if channel=='logging':logging.captureWarnings(True)
        with warnings.catch_warnings(record=channel=='warnings') as emitted:
            warnings.simplefilter('always')
            if channel=='stderr':
                # Exercise Python's stderr handler rather than pytest's warning hook.
                def stderr_warning(message,category,filename,lineno,file=None,line=None):
                    print(warnings.formatwarning(message,category,filename,lineno,line),
                          file=file or sys.stderr,end='')
                warnings.showwarning=stderr_warning
            with pytest.raises(TrainingPreparationError) as error:
                b['service'].prepare_temporal(plan,b['configuration'])
            warning_text='' if emitted is None else '\n'.join(str(w.message) for w in emitted)
    finally:
        logging.captureWarnings(capture_enabled)
    output=capsys.readouterr()
    assert not calls
    assert secret not in warning_text+output.out+output.err+caplog.text+''.join(traceback.format_exception(error.value))
    assert not warning_text and not output.err
    assert str(error.value)=='canonical temporal plan validation failed'
    with warnings.catch_warnings(record=True) as unrelated:
        warnings.simplefilter('always')
        warnings.warn('unrelated temporal diagnostic warning',UserWarning)
    assert len(unrelated)==1 and str(unrelated[0].message)=='unrelated temporal diagnostic warning'


@pytest.mark.parametrize('field',('id','unit'))
@pytest.mark.parametrize('channel',('warnings','stderr','logging'))
def test_public_temporal_malformed_configuration_diagnostics_are_sanitized(make_temporal,monkeypatch,capsys,caplog,field,channel):
    import logging
    import sys
    import warnings

    b=make_temporal();config=b['configuration'];secret='CFGKEY42'
    def malformed(obj,**changes):
        return type(obj).model_construct(**{**obj.__dict__,**changes})
    if field=='id':
        config=malformed(config,configuration_id={secret:secret})
    else:
        config=malformed(config,execution=malformed(config.execution,
            measurement=malformed(config.execution.measurement,unit={secret:secret})))
    calls=[]
    def unexpected(*args,**kwargs):
        calls.append(True)
        raise AssertionError('malformed configuration reached dataset capture')
    monkeypatch.setattr(b['service'],'_consume_capture',unexpected)
    capture_enabled=logging._warnings_showwarning is not None
    try:
        if channel=='logging':logging.captureWarnings(True)
        with warnings.catch_warnings(record=channel=='warnings') as emitted:
            warnings.simplefilter('always')
            if channel=='stderr':
                def stderr_warning(message,category,filename,lineno,file=None,line=None):
                    print(warnings.formatwarning(message,category,filename,lineno,line),file=file or sys.stderr,end='')
                warnings.showwarning=stderr_warning
            with pytest.raises(TrainingPreparationError) as error:
                b['service'].prepare_temporal(b['plan'],config)
            warning_text='' if emitted is None else '\n'.join(str(w.message) for w in emitted)
    finally:
        logging.captureWarnings(capture_enabled)
    output=capsys.readouterr()
    assert not calls
    assert not warning_text and not output.err
    assert secret not in warning_text+output.out+output.err+caplog.text+''.join(traceback.format_exception(error.value))
    assert str(error.value)=='canonical temporal preparation failed'
