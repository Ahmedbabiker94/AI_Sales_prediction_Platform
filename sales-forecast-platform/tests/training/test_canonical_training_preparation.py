"""Synthetic admitted-store handoffs and explicitly synthetic tolerance approvals."""
from contextlib import contextmanager
from dataclasses import replace, FrozenInstanceError
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
import runpy
import sqlite3
import traceback
from uuid import UUID

import pandas as pd
import pyarrow as pa
import pytest

from src.data.contracts.dataset import (StoredDatasetReference,DatasetAccessContext,ConsistencyPolicy,
    VerifiedConsistencyEvidence,ScopedWarningAuthorization,StagingBudgets)
from src.data.contracts.features import FeatureConfiguration
from src.data.contracts.forecast import ForecastConfig
from src.features.canonical_feature_pipeline import FeatureBudgets
from src.providers.canonical_training_data_provider import CanonicalTrainingDataProvider
from src.providers.dataset_store_provider import StagingError
from src.repositories.local_dataset_repository import LocalDatasetRepository
from src.services.dataset_ingestion_service import DatasetIngestionService
from src.services.dataset_finalization_service import DatasetFinalizationService
from src.services.canonical_training_preparation_service import (CanonicalTrainingPreparationService,
    PreparationBudgets,TrainingPreparationError)

NOW=datetime(2026,1,2,tzinfo=timezone.utc)
ORIGIN=pd.Timestamp('2020-02-03',tz='UTC')


@pytest.fixture
def make_bundle(tmp_path):
    def make(*,decimal=False,exo=None,finalized=True,signed_zero=False,availability=True,missing_label=False,negative_label=False,organization_column=False,category_fit_case=None):
        reference=runpy.run_path('tests/data/adapters/test_integration_foundation.py')
        asset=reference['asset'].__wrapped__();mapping=reference['config']()
        data=mapping.model_dump();data['field_mappings'].update(available_at='released',availability_reference='proof')
        data['required_auxiliary_fields']=(*data['required_auxiliary_fields'],'available_at','availability_reference')
        if not availability:
            data['field_mappings'].pop('available_at');data['field_mappings'].pop('availability_reference')
            data['required_auxiliary_fields']=('promotion',)
        if exo:
            data['field_mappings'].update(promotion_available_at='promo_release',promotion_evidence='promo_proof')
        if organization_column:data['field_mappings']['organization_id']='source_org'
        mapping=type(mapping).model_validate(data)
        rows=[]
        for region,delta in [('north',0),('south',100)]:
            for n,value in enumerate([2,-4,0,8,16]):
                if missing_label and region=='south' and n==4:continue
                start=pd.Timestamp('2020-01-06',tz='UTC')+pd.DateOffset(days=7*n)
                target=Decimal('19.99') if decimal and n==4 else Decimal('.1') if decimal and n==0 else Decimal(value+delta) if decimal else float(value+delta)
                if signed_zero and n==4:target=-0.0
                if negative_label and n==4:target=-16.0
                rows.append(dict(branch=region,group='toys',reported=target,**{'from':start,'to':start+pd.DateOffset(days=7)},
                    offer='NEW' if exo=='category' and n==4 else 'A' if exo=='category' else 1.,
                    released=start+pd.DateOffset(days=7),proof=f'approved-release-{region}-{n}',
                    promo_release=start-pd.DateOffset(days=7),promo_proof=f'approved-promotion-{region}-{n}'))
        raw=pd.DataFrame(rows)
        if category_fit_case:
            raw.loc[0,'offer']='0-FUTURE'
            if category_fit_case=='future':raw.loc[0,'promo_release']=ORIGIN+pd.DateOffset(days=7)
            elif category_fit_case=='missing_timestamp':raw.loc[0,'promo_release']=pd.NaT
            elif category_fit_case=='missing_evidence':raw.loc[0,'promo_proof']=None
        if organization_column:raw['source_org']='untrusted-other-organization'
        if decimal:
            raw['reported']=pd.Series(pa.array(raw.reported.tolist(),type=pa.decimal128(12,2)),dtype=pd.ArrowDtype(pa.decimal128(12,2)))
        store=LocalDatasetRepository(tmp_path/str(__import__('uuid').uuid4()))
        manifest=DatasetIngestionService(store,authorize_ingestion=lambda *_:True).ingest(
            reference['MemoryAdapter'](raw,asset),mapping,dataset_id=UUID(int=99),batch_size=2)
        ref=StoredDatasetReference(manifest.organization_id,manifest.dataset_id,manifest.attempt_id,manifest.version_id)
        scope=DatasetAccessContext(ref,manifest.source_id,manifest.ingestion_batch_id,mapping.mapping_id,mapping.version,
            manifest.mapping_digest,mapping.target_metric,mapping.business_scope,'opaque-source','connection-1','production_training')
        if finalized:
            finalizer=DatasetFinalizationService(store,authorize=lambda *_:True,verify_consistency=lambda *_:True,
                verify_policy=lambda *_:True,verify_warning=lambda *_:True,decision_context_reference='synthetic-approval',clock=lambda:NOW)
            evidence=VerifiedConsistencyEvidence(scope,'snapshot','snapshot_external_v1','method-1','1.0',NOW-timedelta(days=2),NOW-timedelta(hours=1),NOW-timedelta(minutes=1),NOW+timedelta(days=1))
            warning=ScopedWarningAuthorization(scope,'NEGATIVE_VALUE','value','keep','synthetic-warning-approval',NOW-timedelta(hours=1),NOW+timedelta(days=1))
            finalizer.finalize(ref,mapping,connection_reference='connection-1',consistency_policy=ConsistencyPolicy(scope,'SALES_GENERAL','1.0.0'),consistency_evidence=evidence,warning_authorizations=(warning,))
        rules=dict(measurement=mapping.measurement.model_dump(),currency_required=True,metric_meaning=mapping.metric_meaning,
            sign_semantics=mapping.sign_semantics,dimension_fields=('location','department'),
            categories=[dict(field='location',unknown='reject',missing='reject')],exogenous=[],
            numeric_conversion='lossless_float64',historical_value_availability='available_at',
            temporal=dict(frequency='weekly',calendar='gregorian',timezone='UTC',period_anchor='start',week_start=0,
                lag_basis='period',gap_policy='reject',minimum_history=4,cold_start='reject',recursive_strategy='provided_predictions'))
        outputs=('location','lag1','mean4','std4')
        config=FeatureConfiguration(configuration_id='synthetic-preparation',version='2.0',target_metric=mapping.target_metric,
            canonical_inputs=('value','period_start','period_end','location','department'),history_order='oldest_first',lag_offsets=(1,),rolling_windows=(4,),
            calculations=[dict(output='lag1',operation='lag',inputs=['value'],parameters={'offset':1}),
                dict(output='mean4',operation='rolling_mean',inputs=['value'],parameters={'window':4,'offset':1,'min_periods':4}),
                dict(output='std4',operation='rolling_std',inputs=['value'],parameters={'window':4,'offset':1,'min_periods':4,'ddof':1})],
            missing_value_behavior={v:'reject' for v in outputs},category_encoding={'location':'ordinal'},ordered_outputs=outputs,
            output_dtypes={v:'int8' if v=='location' else 'float64' for v in outputs},execution=rules)
        if exo:
            c=config.model_dump();c['canonical_inputs']=(*c['canonical_inputs'],'promotion');c['ordered_outputs']=(*c['ordered_outputs'],'promotion')
            c['output_dtypes']['promotion']='int8' if exo=='category' else 'float64';c['missing_value_behavior']['promotion']='reject'
            c['execution']['exogenous']=[dict(field='promotion',availability='known_in_advance',missing='reject',
                availability_field='promotion_available_at',evidence_field='promotion_evidence')]
            if exo=='category':
                c['category_encoding']['promotion']='ordinal';c['execution']['categories']=(*c['execution']['categories'],dict(field='promotion',unknown='use_code',unknown_code=-1,missing='reject'))
            config=FeatureConfiguration.model_validate(c)
        provider=CanonicalTrainingDataProvider(store,authorize_read=lambda *_:True,clock=lambda:NOW)
        with provider.open_read(ref,purpose='production_training') as session:
            metadata=session.metadata;list(session.batches())
        selected=tuple(v.series_id for v in metadata.series)
        phases=[]
        service=CanonicalTrainingPreparationService(provider,authorize_preparation=lambda ctx,phase:(phases.append(phase) or True),verify_availability=lambda *_:True)
        return dict(store=store,ref=ref,mapping=mapping,configuration=config,provider=provider,service=service,
            selected=selected,metadata=metadata,phases=phases,raw=raw)
    return make


def prepare(bundle,**overrides):
    kwargs=dict(forecast_origin=ORIGIN,training_cutoff=ORIGIN,
        forecast_config=ForecastConfig(forecast_frequency='weekly',forecast_horizon=1),series_ids=bundle['selected'])
    kwargs.update(overrides)
    return bundle['service'].prepare(bundle['ref'],bundle['configuration'],**kwargs)


def bounded(bundle):
    data=bundle['configuration'].model_dump()
    data['execution'].update(numeric_conversion='versioned_float64',numeric_policies=[dict(field='value',
        measurement=bundle['mapping'].measurement.model_dump(),mode='BOUNDED_FLOAT64',absolute_tolerance='1e-12',relative_tolerance='0')])
    bundle['configuration']=FeatureConfiguration.model_validate(data)


def test_success_metadata_labels_causality_and_immutable_result(make_bundle):
    b=make_bundle();r=prepare(b)
    assert b['phases']==['read','after_read','return']
    assert r.context.metadata==b['metadata'] and r.schema.forecast_horizon==1
    assert r.context.metadata.reference==b['ref'] and r.context.metadata.manifest_digest==b['ref'].version_id
    assert r.labels.index.equals(r.matrix.index) and sorted(r.labels)==[16.,116.]
    north=next(v.series_id for v in b['metadata'].series if v.dimensions['location']=='north')
    assert r.matrix.loc[(b['ref'].organization_id,north,ORIGIN),'lag1']==8.
    assert r.matrix.loc[(b['ref'].organization_id,north,ORIGIN),'mean4']==1.5
    assert r.fitted_state.vocabularies['location']==('north','south')
    assert prepare(b).preparation_id==r.preparation_id
    with pytest.raises(FrozenInstanceError):r.preparation_id='changed'
    matrix=r.matrix;matrix.iloc[0,0]=99;labels=r.labels;labels.iloc[0]=999
    assert r.matrix.iloc[0,0]!=99 and r.labels.iloc[0]!=999


def test_reader_closed_and_lock_released_before_transformation(make_bundle):
    b=make_bundle();observed=[]
    def authorize(context,phase):
        if phase in ('after_read','return'):
            with b['store']._publication_session(StagingBudgets()):observed.append(phase)
        return True
    b['service'].authorize=authorize
    prepare(b);assert observed==['after_read','return']
    assert len([p for p in b['store'].root.iterdir() if p.is_dir()])==1


def test_strict_default_and_synthetic_bounded_decimal_preservation(make_bundle):
    b=make_bundle(decimal=True)
    with pytest.raises(TrainingPreparationError):prepare(b)
    bounded(b);r=prepare(b)
    assert r.schema.executor_version=='1.1.0' and r.labels.eq(19.99).all()
    assert all(v.as_tuple()==Decimal('19.99').as_tuple() for v in r.original_labels)
    stored=[v for frame in b['provider'].read_batches(b['ref'],purpose='production_training') for v in frame.value]
    assert any(v.as_tuple()==Decimal('.10').as_tuple() for v in stored)
    assert r.context.configuration_digest==r.schema.configuration_digest


@pytest.mark.parametrize('phase',['read','after_read','return'])
def test_preparation_denial_never_changes_finalized_publication(make_bundle,phase):
    b=make_bundle();b['service'].authorize=lambda _,p:p!=phase
    with pytest.raises(TrainingPreparationError):prepare(b)
    with sqlite3.connect(b['store'].root/'catalog.sqlite') as conn:
        assert conn.execute('SELECT state FROM attempts').fetchone()[0]=='finalized'
        assert conn.execute('SELECT COUNT(*) FROM publications').fetchone()[0]==1
    b['service'].authorize=lambda *_:True;assert prepare(b).labels.size==2


def test_fresh_read_authorization_after_consumption(make_bundle):
    b=make_bundle()
    def authorize(_,phase):
        if phase=='after_read':b['provider'].authorize_read=lambda *_:False
        return True
    b['service'].authorize=authorize
    with pytest.raises(TrainingPreparationError):prepare(b)
    b['provider'].authorize_read=lambda *_:True;b['service'].authorize=lambda *_:True
    assert prepare(b).labels.size==2


@pytest.mark.parametrize('role',['authorize','verify_availability'])
@pytest.mark.parametrize('error',[RuntimeError,TrainingPreparationError,StagingError])
def test_trusted_callback_errors_are_sanitized(make_bundle,role,error):
    b=make_bundle()
    def failed(*_):raise error('PRIVATE_SYNTHETIC_TOKEN')
    setattr(b['service'],role,failed)
    with pytest.raises(TrainingPreparationError) as exc:prepare(b)
    assert 'PRIVATE_SYNTHETIC_TOKEN' not in ''.join(traceback.format_exception(exc.value))


@pytest.mark.parametrize('fault',['organization','version','purpose','series','source','mapping','metric','unit','dimension','state','calendar'])
def test_scope_and_metadata_fail_closed(make_bundle,fault):
    b=make_bundle();overrides={}
    if fault=='organization':b['ref']=replace(b['ref'],organization_id=UUID(int=999))
    if fault=='version':b['ref']=replace(b['ref'],version_id='0'*64)
    if fault=='purpose':overrides['purpose']='diagnostics'
    if fault=='series':overrides['series_ids']=(UUID(int=999),)
    if fault in ('metric','unit','dimension','calendar'):
        config=b['configuration'].model_dump()
        if fault=='metric':config['target_metric']='other';config['execution']['measurement']['metric']='other'
        if fault=='unit':config['execution']['measurement']['unit']='items'
        if fault=='dimension':config['execution']['dimension_fields']=('location','other')
        if fault=='calendar':config['execution']['temporal']['timezone']='Europe/London'
        b['configuration']=FeatureConfiguration.model_validate(config)
    if fault in ('source','mapping','state'):
        with sqlite3.connect(b['store'].root/'catalog.sqlite') as conn:
            if fault=='state':conn.execute("UPDATE attempts SET state='sealed'")
            else:
                data=json.loads(conn.execute('SELECT admission FROM publications').fetchone()[0])
                if fault=='source':data['scope']['source_id']=str(UUID(int=999))
                else:data['mapping']['version']='2.0'
                body=json.dumps(data);conn.execute('UPDATE publications SET admission=?,digest=?',(body,__import__('hashlib').sha256(body.encode()).hexdigest()))
    with pytest.raises(TrainingPreparationError):prepare(b,**overrides)


@pytest.mark.parametrize('fault',['missing_series','wrong_dimensions','wrong_organization'])
def test_verified_envelope_corruption_blocks_preparation(make_bundle,fault):
    b=make_bundle()
    with sqlite3.connect(b['store'].root/'catalog.sqlite') as conn:
        env=json.loads(conn.execute('SELECT envelope FROM chunks WHERE ordinal=0').fetchone()[0])
        if fault=='missing_series':env['canonical_dataset']['series']=[]
        if fault=='wrong_dimensions':env['canonical_dataset']['series'][0]['dimensions']['location']='other'
        if fault=='wrong_organization':env['canonical_dataset']['asset']['organization_id']=str(UUID(int=88))
        conn.execute('UPDATE chunks SET envelope=? WHERE ordinal=0',(json.dumps(env),))
    with pytest.raises(TrainingPreparationError):prepare(b)


def test_late_chunk_corruption_returns_no_result(make_bundle):
    b=make_bundle();p=b['store'].root/str(b['ref'].attempt_id)/'4-canonical.parquet';p.chmod(0o600);p.write_bytes(b'corrupt')
    with pytest.raises(TrainingPreparationError):prepare(b)


def test_cancellation_and_partial_session_release_resources(make_bundle):
    b=make_bundle();calls=[]
    with pytest.raises(TrainingPreparationError):prepare(b,cancelled=lambda:(calls.append(1) or len(calls)>1))
    with b['store']._publication_session(StagingBudgets()):pass
    with pytest.raises(StagingError):
        with b['provider'].open_read(b['ref'],purpose='production_training') as session:next(session.batches())
    assert prepare(b).labels.size==2


@pytest.mark.parametrize('budgets',[PreparationBudgets(max_decoded_bytes=1),FeatureBudgets(max_rows=2),FeatureBudgets(max_result_bytes=1)])
def test_budgets_reject_before_result(make_bundle,budgets):
    b=make_bundle();kwargs={'budgets':budgets} if type(budgets) is PreparationBudgets else {'feature_budgets':budgets}
    with pytest.raises(TrainingPreparationError):prepare(b,**kwargs)


def test_horizon_and_unverified_availability_reject(make_bundle):
    b=make_bundle();b['service'].verify_availability=lambda *_:False
    with pytest.raises(TrainingPreparationError):prepare(b)
    b['service'].verify_availability=lambda *_:True
    with pytest.raises(TrainingPreparationError):prepare(b,forecast_config=ForecastConfig(forecast_frequency='weekly',forecast_horizon=2))


def test_category_vocabulary_excludes_future_target_category(make_bundle):
    b=make_bundle(exo='category');r=prepare(b)
    assert r.fitted_state.vocabularies['promotion']==('A',) and r.matrix.promotion.eq(-1).all()
    changed=b['configuration'].model_dump();changed['execution']['categories'][1].update(unknown='reject',unknown_code=None)
    b['configuration']=FeatureConfiguration.model_validate(changed)
    with pytest.raises(TrainingPreparationError):prepare(b)


def test_observed_only_exogenous_cannot_use_future_actuals(make_bundle):
    b=make_bundle(exo='numeric');c=b['configuration'].model_dump();c['execution']['exogenous'][0]['availability']='observed_only'
    b['configuration']=FeatureConfiguration.model_validate(c)
    with pytest.raises(TrainingPreparationError):prepare(b)
    c['execution']['exogenous'][0]['availability']='known_in_advance';b['configuration']=FeatureConfiguration.model_validate(c)
    assert prepare(b).matrix.promotion.eq(1).all()


def test_external_forecast_requires_scoped_trusted_authorization(make_bundle):
    b=make_bundle(exo='numeric');c=b['configuration'].model_dump();c['execution']['exogenous'][0]['availability']='external_forecast'
    b['configuration']=FeatureConfiguration.model_validate(c)
    with pytest.raises(TrainingPreparationError):prepare(b)
    calls=[]
    b['service'].authorize_external=lambda ctx,r:(calls.append(r) or ctx.metadata.reference==b['ref'] and r.organization_id==b['ref'].organization_id and r.forecast_origin==ORIGIN)
    assert prepare(b).matrix.promotion.eq(1).all() and len(calls)==4


def test_no_csv_or_external_fitted_state_fallback(make_bundle):
    b=make_bundle();b['ref']='unadmitted.csv'
    with pytest.raises(TrainingPreparationError):prepare(b)
    b=make_bundle()
    with pytest.raises(TypeError):prepare(b,fitted_state=object())


def test_missing_availability_and_labels_reject(make_bundle):
    for options in ({'availability':False},{'missing_label':True}):
        b=make_bundle(**options)
        with pytest.raises(TrainingPreparationError):prepare(b)


def test_signed_zero_label_and_original_values_preserved(make_bundle):
    import math
    b=make_bundle(signed_zero=True);r=prepare(b)
    assert all(math.copysign(1,v)==-1 for v in r.original_labels)
    assert all(math.copysign(1,v)==-1 for v in r.converted_labels)
    assert r.labels.eq(0).all()


def test_signed_negative_labels_are_not_reinterpreted(make_bundle):
    b=make_bundle(negative_label=True);r=prepare(b)
    assert r.labels.eq(-16).all() and r.original_labels==(-16.,-16.)
    assert r.schema.configuration.target_metric=='reported_sales'
    assert r.schema.configuration.execution.sign_semantics==b['mapping'].sign_semantics


@pytest.mark.parametrize('state',['staging','aborted','rejected'])
def test_nonfinalized_states_cannot_prepare(make_bundle,state):
    b=make_bundle()
    with sqlite3.connect(b['store'].root/'catalog.sqlite') as conn:
        conn.execute('UPDATE attempts SET state=?',(state,))
    with pytest.raises(TrainingPreparationError):prepare(b)


def test_approximation_requires_trusted_preparation_approval(make_bundle):
    b=make_bundle(decimal=True);bounded(b)
    def approve(context,phase):
        config=FeatureConfiguration.model_validate_json(context.configuration_json)
        return not any(p.mode=='BOUNDED_FLOAT64' for p in config.execution.numeric_policies)
    b['service'].authorize=approve
    with pytest.raises(TrainingPreparationError):prepare(b)


def test_untrusted_row_organization_cannot_shadow_asset_scope(make_bundle):
    b=make_bundle(organization_column=True)
    with pytest.raises(TrainingPreparationError):prepare(b)


def test_required_closure_and_late_stream_failure_block_result(make_bundle):
    for failure in ('closure','late'):
        b=make_bundle();original=b['provider'].open_read
        @contextmanager
        def failed(*args,**kwargs):
            with original(*args,**kwargs) as session:
                if failure=='late':
                    batches=session.batches
                    def broken():
                        for i,frame in enumerate(batches()):
                            if i==1:raise StagingError('SYNTHETIC_READ_SECRET')
                            yield frame
                    session.batches=broken
                yield session
            if failure=='closure':raise StagingError('SYNTHETIC_CLOSE_SECRET')
        b['provider'].open_read=failed
        with pytest.raises(TrainingPreparationError) as exc:prepare(b)
        assert 'SECRET' not in ''.join(traceback.format_exception(exc.value))
        with b['store']._publication_session(StagingBudgets()):pass


def test_revoked_availability_and_external_forecast_rechecked_before_return(make_bundle):
    b=make_bundle();calls=[]
    b['service'].verify_availability=lambda *_:(calls.append(1) or len(calls)==1)
    with pytest.raises(TrainingPreparationError):prepare(b)
    b=make_bundle(exo='numeric');data=b['configuration'].model_dump()
    data['execution']['exogenous'][0]['availability']='external_forecast'
    b['configuration']=FeatureConfiguration.model_validate(data);calls=[]
    b['service'].authorize_external=lambda *_:(calls.append(1) or len(calls)<=2)
    with pytest.raises(TrainingPreparationError):prepare(b)


def test_multi_auxiliary_mapping_order_survives_publication_serialization(make_bundle):
    b=make_bundle(exo='numeric');r=prepare(b)
    assert r.labels.size==2 and r.matrix.promotion.eq(1).all()
    first=next(b['provider'].read_batches(b['ref'],purpose='production_training'))
    assert set(('promotion','available_at','availability_reference','promotion_available_at','promotion_evidence'))<=set(first)


@pytest.mark.parametrize('case',['future','missing_timestamp','missing_evidence','unverifiable'])
def test_categorical_field_availability_rejects_before_fit(make_bundle,monkeypatch,case):
    from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
    b=make_bundle(exo='category',category_fit_case=case)
    config=b['configuration'].model_dump()
    config['execution']['exogenous'][0].update(availability='lagged',offset=1)
    b['configuration']=FeatureConfiguration.model_validate(config)
    seen=[]
    def verify(context,claims):
        seen.extend(claims)
        if case=='unverifiable' and claims and all(c.field=='promotion' for c in claims):return False
        return all(c.available_at==b['raw'].iloc[int(c.source_record_id)][
            'released' if c.field=='value' else 'promo_release'] for c in claims)
    b['service'].verify_availability=verify
    fit_calls=[]
    original=CanonicalFeaturePipeline.fit_state
    def tracked_fit(self,*args,**kwargs):
        fit_calls.append(True)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',tracked_fit)
    with pytest.raises(TrainingPreparationError,match='canonical training preparation failed') as exc:
        prepare(b)
    assert not fit_calls
    assert '0-FUTURE' not in ''.join(traceback.format_exception(exc.value))
    if case=='future':
        assert any(c.field=='promotion' and c.period_start==pd.Timestamp('2020-01-06',tz='UTC')
            and c.available_at==pd.Timestamp('2020-02-10',tz='UTC') for c in seen)


def test_eligible_categorical_field_availability_verified_before_fit(make_bundle,monkeypatch):
    from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
    b=make_bundle(exo='category');config=b['configuration'].model_dump()
    config['execution']['exogenous'][0].update(availability='lagged',offset=1)
    b['configuration']=FeatureConfiguration.model_validate(config)
    verified=[]
    def verify(context,claims):
        assert context.training_cutoff==ORIGIN
        assert all(c.available_at==b['raw'].iloc[int(c.source_record_id)][
            'released' if c.field=='value' else 'promo_release'] for c in claims)
        verified.append(tuple(claims))
        return True
    b['service'].verify_availability=verify
    original=CanonicalFeaturePipeline.fit_state
    def checked_fit(self,*args,**kwargs):
        category_claims=verified[-1]
        assert len(category_claims)==8
        assert all(c.field=='promotion' and c.available_at<=ORIGIN for c in category_claims)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',checked_fit)
    result=prepare(b)
    assert result.fitted_state.vocabularies['promotion']==('A',)
    assert result.matrix.promotion.tolist()==[0,0]


@pytest.mark.parametrize('mode',['cancel','deadline'])
def test_final_callback_interruption_prevents_result(make_bundle,mode):
    b=make_bundle();status={'cancelled':False,'elapsed':0};phases=[]
    b['service'].elapsed_clock=lambda:status['elapsed']
    def authorize(context,phase):
        phases.append(phase)
        if phase=='return':
            if mode=='cancel':status['cancelled']=True
            else:status['elapsed']=301
        return True
    b['service'].authorize=authorize
    with pytest.raises(TrainingPreparationError,match='canonical training preparation failed'):
        prepare(b,cancelled=lambda:status['cancelled'],budgets=PreparationBudgets(max_elapsed_seconds=300))
    assert phases==['read','after_read','return']
    with b['store']._publication_session(StagingBudgets()):pass
    status.update(cancelled=False,elapsed=0)
    b['service'].authorize=lambda *_:True
    assert prepare(b).labels.tolist()==[16.,116.]
