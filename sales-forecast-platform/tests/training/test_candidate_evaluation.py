"""M8 real local publications/M6-M7 ownership; estimator operations are stubs."""
from copy import copy
from datetime import timedelta
from fractions import Fraction
import json
from pathlib import Path
import runpy
import traceback
import warnings
from uuid import UUID
from unittest.mock import Mock
import pytest

from src.data.contracts.candidate_evaluation import *
from src.data.contracts.dataset import (StoredDatasetReference,DatasetAccessContext,ConsistencyPolicy,
    VerifiedConsistencyEvidence,ScopedWarningAuthorization)
from src.data.processing.forecast_evaluation import numeric_identity
from src.data.processing.candidate_evaluation import metrics,POLICY
from src.providers.canonical_training_data_provider import CanonicalTrainingDataProvider
from src.providers.candidate_evaluation_provider import CandidateEvaluationProvider
from src.repositories.local_dataset_repository import LocalDatasetRepository
from src.services.dataset_ingestion_service import DatasetIngestionService
from src.services.dataset_finalization_service import DatasetFinalizationService
from src.services.canonical_training_preparation_service import CanonicalTrainingPreparationService
from src.services.candidate_evaluation_service import CandidateEvaluationService,CandidateEvaluationError,evidence
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline


@pytest.fixture
def setup(tmp_path):
    original=runpy.run_path('tests/training/test_candidate_packaging.py')['setup'].__wrapped__(tmp_path)
    make=next(original);counter=[0]
    def build(*,families=('xgboost','lightgbm'),partition='validation',change=None):
        b=make(families[0]);results=[b['result']]+[b['run'](f) for f in families[1:]]
        assert all(r.status=='SUCCESS' for r in results)
        bindings=[];manifests=[]
        for r in results:
            tracked=b['packaging'].package(r.candidate,test_only=True)
            manifests.append(b['packaging'].verify(tracked.package_id,candidate=r.candidate))
            bindings.append((b['packaging'],r.candidate,tracked.package_id))
        reference=runpy.run_path('tests/data/adapters/test_integration_foundation.py')
        training=b['metadata'][0];mapping=training.mapping;now=b['service'].provider.clock()
        counter[0]+=1;store=LocalDatasetRepository(tmp_path/('evaluation-'+str(counter[0])))
        flags={'labels':True,'revision':True,'evaluation':True,'cancelled':False,'clock':0.,'denied':set(),'callback':None}
        provider=CanonicalTrainingDataProvider(store,authorize_read=lambda *_:flags['labels'],clock=lambda:now)
        raw=b['raws'][2].copy(deep=True)
        if change:change(raw)
        asset=reference['asset'].__wrapped__().model_copy(update={'organization_id':training.reference.organization_id,'timezone':mapping.timezone})
        publication=DatasetIngestionService(store,authorize_ingestion=lambda *_:True).ingest(reference['MemoryAdapter'](raw,asset),mapping,
            dataset_id=training.reference.dataset_id,batch_size=3)
        ref=StoredDatasetReference(publication.organization_id,publication.dataset_id,publication.attempt_id,publication.version_id)
        scope=DatasetAccessContext(ref,publication.source_id,publication.ingestion_batch_id,mapping.mapping_id,mapping.version,
            publication.mapping_digest,mapping.target_metric,mapping.business_scope,'opaque-source','connection-1','offline_forecast_evaluation')
        consistency=VerifiedConsistencyEvidence(scope,'snapshot','snapshot_external_v1','method-1','1.0',now-timedelta(days=2),
            now-timedelta(hours=1),now-timedelta(minutes=1),now+timedelta(days=1))
        warning=ScopedWarningAuthorization(scope,'NEGATIVE_VALUE','value','keep','m8-synthetic-warning',now-timedelta(hours=1),now+timedelta(days=1))
        DatasetFinalizationService(store,authorize=lambda *_:True,verify_consistency=lambda *_:True,verify_policy=lambda *_:True,
            verify_warning=lambda *_:True,decision_context_reference='m8-test-only',clock=lambda:now).finalize(ref,mapping,
            connection_reference='connection-1',purpose='offline_forecast_evaluation',consistency_policy=ConsistencyPolicy(scope,'SALES_GENERAL','1.0.0'),
            consistency_evidence=consistency,warning_authorizations=(warning,))
        with provider.open_read(ref,purpose='offline_forecast_evaluation') as session:
            meta=session.metadata;rows=[row for frame in session.batches() for row in frame.to_dict('records')]
        ev=CanonicalTrainingPreparationService._temporal_binding(meta,b['plan'].datasets[0].series_ids)
        # Immutable independent revision fixture, not a digest self-approval.
        revision={(row['series_id'],row['period_start']):(numeric_identity(row['value']),row['available_at'],row['availability_reference'],row['source_record_id'],row['lineage_id']) for row in rows}
        events=[]
        def attest(claim):
            expected=revision.get((claim.series_id,claim.period_start))
            return flags['revision'] and expected==(claim.value_identity,claim.available_at,claim.evidence_reference,claim.source_record_id,claim.lineage_id)
        def label_permission(req,stage):events.append(('labels',stage));return flags['labels']
        truth_provider=CandidateEvaluationProvider(provider,authorize_labels=label_permission,verify_label_revision=attest)
        def permission(req,stage):
            events.append(('evaluation',stage))
            if flags['callback']:flags['callback'](req,stage)
            return flags['evaluation'] and stage not in flags['denied']
        service=CandidateEvaluationService(truth_provider,tuple(bindings),authorize_evaluation=permission,elapsed_clock=lambda:flags['clock'])
        engine=CanonicalFeaturePipeline(b['configuration']);fold=b['plan'].folds[0]
        definitions={s.series_id:s for s in meta.series};keys=[]
        for origin in getattr(fold,partition+'_origins'):
            for sid in ev.series_ids:
                s=ForecastScope(plan_digest=b['plan'].digest,dataset_version=ref.version_id,fold_id=fold.fold_id,organization_id=ref.organization_id,
                    series_id=sid,metric=mapping.target_metric,unit=mapping.measurement.unit,currency=mapping.measurement.currency,
                    frequency=b['plan'].frequency,timezone=b['plan'].timezone,dimensions=tuple(sorted(definitions[sid].dimensions.items())),
                    forecast_origin=origin,horizon_step=1,target_period=origin,target_period_end=engine._advance(engine._time(origin),1))
                keys.append(EvaluationKey(scope=s,partition=partition))
        keys=tuple(sorted(keys,key=key_order));batches=[]
        for i,m in enumerate(manifests):
            source=evidence(m)
            obs=[]
            for key in keys:
                actual=next(row['value'] for row in rows if row['series_id']==key.scope.series_id and row['period_start']==key.scope.target_period)
                obs.append(PredictionObservation(source=source,key=key,value=float(actual)+2*(i+1)))
            batches.append(CandidatePredictionBatch(source=source,observations=tuple(obs)))
        batches=tuple(sorted(batches,key=lambda x:x.source.candidate_id));ids=tuple(x.source.candidate_id for x in batches)
        req=CandidateEvaluationRequest(plan=b['plan'],evaluation=ev,partition=partition,keys=keys,batches=batches,
            ensemble=EnsembleConfiguration(mode='equal_mean_v1',members=ids))
        b.update(request=req,evaluator=service,label_provider=provider,truth_provider=truth_provider,flags=flags,events=events,
            results=results,bindings=bindings,manifests=manifests,rows=rows)
        b['evaluate']=lambda req=req:service.evaluate(req,cancelled=lambda:flags['cancelled'])
        return b
    yield build
    try:next(original)
    except StopIteration:pass


def test_owned_shared_capture_metrics_and_deterministic_result(setup,monkeypatch):
    b=setup();read=b['label_provider'].open_read;captures=[]
    from contextlib import contextmanager
    @contextmanager
    def counted(*a,**kw):
        captures.append(1)
        with read(*a,**kw) as session:yield session
    monkeypatch.setattr(b['label_provider'],'open_read',counted)
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',Mock(side_effect=AssertionError('no preprocessing fitting')))
    monkeypatch.setattr(b['trainer'],'train',Mock(side_effect=AssertionError('no model fitting')))
    report=b['evaluate']()
    assert captures==[1] and report.evidence=='TEST_ONLY' and report.genuine_model_evidence=='UNAVAILABLE'
    by_family={s.family:m for s,m in zip(report.sources,report.metrics)}
    assert by_family['xgboost'].statistics.mae.fraction==2
    assert by_family['lightgbm'].statistics.mae.fraction==4
    assert report.ensemble.metric.statistics.mae.fraction==3
    assert report.ensemble.metric.statistics.rmse_radicand.fraction==9
    assert report.ensemble.metric.mean_signed_error.fraction==3
    assert report.ensemble.metric.statistics.wape.fraction==Fraction(3,50)
    loaded=MultiCandidateComparison.model_validate_json(report.canonical_json)
    assert loaded.canonical_bytes==report.canonical_bytes and loaded.report_id==report.report_id
    again=b['evaluate']();assert again.canonical_bytes==report.canonical_bytes and len(captures)==2
    assert report.evaluated_count==2 and report.excluded_count==0 and len(report.series_metrics)==6


def test_all_five_families_are_test_only_and_no_operational_imports(setup,monkeypatch):
    b=setup(families=('xgboost','lightgbm','lstm','gru','transformer'))
    import builtins
    original=builtins.__import__
    def forbidden(name,*a,**kw):
        if name.startswith(('xgboost','lightgbm','torch','tensorflow','mlflow','src.deployment','src.ml.model_registry')):raise AssertionError('operational import')
        return original(name,*a,**kw)
    monkeypatch.setattr(builtins,'__import__',forbidden)
    report=b['evaluate']()
    assert {s.family for s in report.sources}=={'xgboost','lightgbm','lstm','gru','transformer'}
    assert all(s.deployability=='TEST_ONLY_NON_DEPLOYABLE' for s in report.sources)


@pytest.mark.parametrize('fault',['missing','duplicate','reordered','series','origin','horizon','partition','unit','transform','family','state','representation','package','genuine','nan','bool','target_end'])
def test_alignment_and_evidence_substitution_rejects(setup,fault,monkeypatch):
    b=setup();d=b['request'].model_dump(mode='json');point=d['batches'][0]['observations'][0]
    if fault=='missing':d['batches'][0]['observations'].pop()
    elif fault=='duplicate':d['batches'][0]['observations'][1]=point
    elif fault=='reordered':d['batches'][0]['observations'].reverse()
    elif fault=='series':point['key']['scope']['series_id']=str(UUID(int=123))
    elif fault=='origin':point['key']['scope']['forecast_origin']=b['at'](5).isoformat()
    elif fault=='horizon':point['key']['scope']['horizon_step']=2
    elif fault=='partition':point['key']['partition']='test'
    elif fault=='unit':point['key']['scope']['unit']='wrong-unit'
    elif fault=='transform':point['key']['transformation']='scaled-target'
    elif fault in ('family','state','representation','package'):
        field={'family':'family','state':'fitted_state_id','representation':'representation_binding_id','package':'package_id'}[fault]
        value='gru' if fault=='family' else 'f'*64
        d['batches'][0]['source'][field]=value
        for p in d['batches'][0]['observations']:p['source'][field]=value
    elif fault=='genuine':d['evidence']='GENUINE_MODEL'
    elif fault=='nan':point['value']=float('nan')
    elif fault=='bool':point['value']=True
    else:point['key']['scope']['target_period_end']=b['at'](9).isoformat()
    read=Mock(side_effect=AssertionError('no labels before alignment admission'))
    monkeypatch.setattr(b['label_provider'],'open_read',read)
    with pytest.raises(CandidateEvaluationError):b['evaluate'](d)
    read.assert_not_called()


@pytest.mark.parametrize('fault',['training','wrong_target','wrong_unit','wrong_fold','wrong_publication','training_publication'])
def test_coherent_grid_rewrite_rejects_before_capture(setup,monkeypatch,fault):
    b=setup();d=b['request'].model_dump(mode='json')
    keys=d['keys']
    if fault=='training':
        for k in keys:k['scope'].update(forecast_origin=b['at'](5).isoformat(),target_period=b['at'](5).isoformat(),target_period_end=b['at'](6).isoformat())
    elif fault=='wrong_target':
        for k in keys:k['scope']['target_period_end']=b['at'](9).isoformat()
    elif fault=='wrong_unit':
        for k in keys:k['scope']['unit']='wrong-unit'
    elif fault=='wrong_fold':
        for k in keys:k['scope']['fold_id']='foreign-fold'
    elif fault=='wrong_publication':d['evaluation']['reference']['dataset_id']=str(UUID(int=123))
    else:d['evaluation']=d['plan']['datasets'][0]
    for batch in d['batches']:
        for p,k in zip(batch['observations'],keys):p['key']=k
    monkeypatch.setattr(b['label_provider'],'open_read',Mock(side_effect=AssertionError('no capture')))
    with pytest.raises(CandidateEvaluationError):b['evaluate'](d)
    b['label_provider'].open_read.assert_not_called()


def test_explicit_common_subset_reports_coverage_and_rejects_implicit_drop(setup):
    b=setup();d=b['request'].model_dump(mode='json');d['keys']=d['keys'][:1]
    for batch in d['batches']:batch['observations']=batch['observations'][:1]
    with pytest.raises(CandidateEvaluationError):b['evaluate'](d)
    d['coverage']='declared_common_subset_v1';report=b['evaluate'](d)
    assert (report.eligible_count,report.evaluated_count,report.excluded_count)==(2,1,1)


@pytest.mark.parametrize('fault',['labels','revision','evaluation','cancel','late_return','late_cancel','deadline','discard','reentrant'])
def test_authorization_and_late_denial_are_fail_closed(setup,fault):
    b=setup()
    if fault in ('labels','revision','evaluation'):b['flags'][fault]=False
    elif fault=='cancel':b['flags']['cancelled']=True
    def action(req,stage):
        if stage=='return':
            if fault=='late_return':b['flags']['evaluation']=False
            elif fault=='late_cancel':b['flags']['cancelled']=True
            elif fault=='deadline':b['flags']['clock']=30.
            elif fault=='discard':b['trainer'].discard(b['results'][0].candidate)
        if stage=='admit' and fault=='reentrant':b['evaluate']()
    b['flags']['callback']=action
    with pytest.raises(CandidateEvaluationError):b['evaluate']()


def test_label_denial_reads_zero_and_revision_denial_never_scores(setup,monkeypatch):
    b=setup();b['flags']['labels']=False
    spy=Mock(side_effect=AssertionError('label denial before read'));monkeypatch.setattr(b['label_provider'],'open_read',spy)
    with pytest.raises(CandidateEvaluationError):b['evaluate']()
    spy.assert_not_called()


@pytest.mark.parametrize('field,value',[('max_keys',1),('max_capture_rows',1),('max_decoded_bytes',1),('max_intermediate_bytes',1),('max_work',1),('max_groups',1),('max_output_bytes',1)])
def test_resource_limits_fail_closed(setup,field,value):
    b=setup();d=b['request'].model_dump(mode='json');d['resources'][field]=value
    with pytest.raises(CandidateEvaluationError):b['evaluate'](d)


@pytest.mark.parametrize('mode,weights',[('fixed_mean_v1',[(1,4),(3,4)]),('equal_mean_v1',[])])
def test_fixed_and_equal_ensembles_exact_without_rounding(setup,mode,weights):
    b=setup();d=b['request'].model_dump(mode='json');d['ensemble']['mode']=mode
    d['ensemble']['weights']=[{'numerator':str(n),'denominator':str(q)} for n,q in weights]
    report=b['evaluate'](d)
    for p,key in zip(report.ensemble.points,b['request'].keys):
        values=[Fraction.from_float(next(p.value for p in batch.observations if p.key==key)) for batch in b['request'].batches]
        expected=sum(v*w for v,w in zip(values,report.ensemble.configuration.effective_weights))
        assert p.value.fraction==expected


@pytest.mark.parametrize('weights',[[('1','3'),('1','3')],[('-1','1'),('2','1')],[('1','0'),('0','1')]])
def test_invalid_weights_reject_without_capture(setup,weights):
    b=setup();d=b['request'].model_dump(mode='json');d['ensemble']['mode']='fixed_mean_v1'
    d['ensemble']['weights']=[{'numerator':n,'denominator':q} for n,q in weights]
    with pytest.raises(CandidateEvaluationError):b['evaluate'](d)


def test_exact_metric_reference_and_zero_denominators():
    pid='a'*64;population='b'*64
    m=metrics(pid,population,[(Fraction(1),0.),(Fraction(-1),2.)])
    s=m.statistics
    assert (s.sae.fraction,s.sse.fraction,s.saa.fraction)==(4,10,2)
    assert s.mae.fraction==2 and s.mse.fraction==s.rmse_radicand.fraction==5
    assert s.wape.fraction==2 and m.smape.fraction==2 and m.mean_signed_error.fraction==-1
    zero=metrics(pid,population,[(Fraction(),0.),(Fraction(),0.)])
    assert zero.statistics.wape is None and zero.statistics.wape_status=='undefined_zero_denominator'
    assert zero.smape.fraction==zero.mean_signed_error.fraction==0


def test_test_split_is_separate_and_ties_are_stable(setup):
    b=setup(partition='test');d=b['request'].model_dump(mode='json')
    for batch in d['batches']:
        for p in batch['observations']:p['value']=0.
    report=b['evaluate'](d)
    assert report.ranking==tuple(sorted(s.candidate_id for s in report.sources))
    assert {k.partition for k in report.keys}=={'test'}


def test_secret_input_errors_are_detached(setup,capsys,caplog):
    b=setup();d=b['request'].model_dump(mode='json');secret='M8_PRIVATE_KEY_829371';value='M8_PRIVATE_VALUE_628539'
    d['batches'][0][secret]=value
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        with pytest.raises(CandidateEvaluationError) as caught:b['evaluate'](d)
        warnings.warn('unrelated warning',UserWarning)
    e=caught.value;text=str(e)+repr(e)+repr(e.args)+''.join(traceback.format_exception(e))+caplog.text+str(capsys.readouterr())+str([str(w.message) for w in seen])
    assert secret not in text and value not in text and e.__context__ is None and e.__cause__ is None
    assert [str(w.message) for w in seen]==['unrelated warning']


@pytest.mark.parametrize('fault',['discarded','copied_service','tampered_package','foreign_reference','wrong_label_provider','wrong_admission'])
def test_original_owner_packages_and_publication_boundaries(setup,monkeypatch,fault):
    b=setup()
    if fault=='discarded':b['trainer'].discard(b['results'][0].candidate)
    elif fault=='copied_service':b['evaluator']=copy(b['evaluator'])
    elif fault=='tampered_package':
        m=b['manifests'][0];p=b['directory']/(m.package_id+'.candidate');p.chmod(0o600);p.write_bytes(b'corrupt');p.chmod(0o400)
    elif fault=='foreign_reference':
        from src.services.canonical_adapter_training_service import CandidateReference
        bind=b['evaluator']._bindings[b['request'].batches[0].source.candidate_id]
        from dataclasses import replace
        b['evaluator']._bindings[b['request'].batches[0].source.candidate_id]=replace(bind,candidate=CandidateReference())
    elif fault=='wrong_label_provider':b['truth_provider']._provider=b['service'].provider
    else:
        d=b['request'].model_dump(mode='json');d['evaluation']['admission_digest']='f'*64
        with pytest.raises(CandidateEvaluationError):b['evaluate'](d)
        return
    with pytest.raises(CandidateEvaluationError):b['evaluator'].evaluate(b['request'])


@pytest.mark.parametrize('fault',['missing','duplicate','nonfinite','wrong_value','late_availability','target_geometry'])
def test_captured_labels_fail_closed_under_reader_corruption(setup,monkeypatch,fault):
    b=setup();original=b['label_provider'].open_read
    from contextlib import contextmanager
    from types import SimpleNamespace
    @contextmanager
    def corrupt(*args,**kwargs):
        with original(*args,**kwargs) as session:
            frames=[f.copy(deep=True) for f in session.batches()]
            import pandas as pd
            all_rows=pd.concat(frames,ignore_index=True)
            key=b['request'].keys[0];s=key.scope
            at=all_rows.index[(all_rows.series_id==s.series_id)&(all_rows.period_start==s.target_period)][0]
            if fault=='missing':all_rows=all_rows.drop(at)
            elif fault=='duplicate':all_rows=pd.concat([all_rows,all_rows.loc[[at]]],ignore_index=True)
            elif fault=='nonfinite':all_rows.loc[at,'value']=float('inf')
            elif fault=='wrong_value':all_rows.loc[at,'value']+=1.
            elif fault=='late_availability':all_rows.loc[at,'available_at']=b['at'](10)
            else:all_rows.loc[at,'period_end']=b['at'](10)
            wrapped=SimpleNamespace(metadata=session.metadata,completed=False)
            def batches():
                yield all_rows
                wrapped.completed=True
            wrapped.batches=batches
            yield wrapped
    monkeypatch.setattr(b['label_provider'],'open_read',corrupt)
    with pytest.raises(CandidateEvaluationError):b['evaluate']()


def test_ensemble_membership_and_optimized_weights_are_not_implicit(setup):
    b=setup();d=b['request'].model_dump(mode='json');d['ensemble']['members']=d['ensemble']['members'][:1]
    with pytest.raises(CandidateEvaluationError):b['evaluate'](d)
    d=b['request'].model_dump(mode='json');d['ensemble']['mode']='test_label_optimized_v1'
    with pytest.raises(CandidateEvaluationError):b['evaluate'](d)


def test_fraction_ensemble_does_not_round_nonrepresentable_mean(setup):
    b=setup();d=b['request'].model_dump(mode='json')
    for i,batch in enumerate(d['batches']):
        for p in batch['observations']:p['value']=float(i)
    d['ensemble']['mode']='fixed_mean_v1'
    d['ensemble']['weights']=[{'numerator':'2','denominator':'3'},{'numerator':'1','denominator':'3'}]
    report=b['evaluate'](d)
    assert all(p.value.fraction==Fraction(1,3) for p in report.ensemble.points)


@pytest.mark.parametrize('raw',[b'\xff','{"version":"1.0","version":"1.0"}','{"a":{"b":1,"\\u0062":2}}'])
def test_service_json_ingress_uses_shared_strict_boundary(setup,raw):
    b=setup()
    with pytest.raises(CandidateEvaluationError) as e:b['evaluate'](raw)
    assert e.value.__context__ is None and e.value.__cause__ is None


def test_final_scoped_denial_and_late_label_revocation_publish_nothing(setup):
    b=setup();b['flags']['denied'].add('return:'+b['request'].keys[-1].key_id)
    with pytest.raises(CandidateEvaluationError):b['evaluate']()
    b['flags']['denied'].clear()
    def revoke(req,stage):
        if stage=='return':b['flags']['labels']=False
    b['flags']['callback']=revoke
    with pytest.raises(CandidateEvaluationError):b['evaluate']()


def test_direct_result_population_and_group_substitution_rejects(setup):
    b=setup();report=b['evaluate']()
    for fault in ('population','missing_group','group_count','ranking'):
        d=report.model_dump(mode='json')
        if fault=='population':d['population_id']='f'*64
        elif fault=='missing_group':d['series_metrics'].pop()
        elif fault=='group_count':d['series_metrics'][0]['metric']['statistics']['count']=2
        else:d['ranking'].reverse()
        with pytest.raises(Exception):MultiCandidateComparison.model_validate_json(json.dumps(d))


def test_nested_versions_public_graph_and_immutable_roundtrips(setup):
    from dataclasses import is_dataclass,fields
    b=setup();report=b['evaluate']();original=report.canonical_bytes
    objects=(b['request'].batches[0].source,b['request'].batches[0].observations[0],b['request'].resources,
        report.metrics[0],report.series_metrics[0],report.ensemble,report.ensemble.points[0],
        GroundTruthObservation(key=b['request'].keys[0],value=0.,revision_claim_id='a'*64))
    for obj in objects:
        assert obj.version=='1.0' and type(obj).model_validate_json(obj.canonical_json)==obj
        bad=obj.model_dump(mode='json');bad['version']='9.0'
        with pytest.raises(Exception):type(obj).model_validate_json(json.dumps(bad))
    forbidden={'labels','actuals','truth','model','artifact','canonical_state','targets','provider','candidate','trainer'}
    def visit(value):
        if hasattr(type(value),'model_fields'):
            for name in type(value).model_fields:
                assert name not in forbidden;visit(getattr(value,name))
        elif is_dataclass(value):
            for field in fields(value):visit(getattr(value,field.name))
        elif isinstance(value,tuple):
            for item in value:visit(item)
        else:assert not isinstance(value,(bytes,bytearray))
    visit(report)
    with pytest.raises(Exception):report.metrics[0].statistics.count=100
    dumped=report.model_dump(mode='json');dumped['sources'][0]['family']='foreign'
    assert report.canonical_bytes==original
    req=b['request'].model_dump(mode='json');req['truth']=[1.,2.]
    with pytest.raises(CandidateEvaluationError):b['evaluate'](req)
