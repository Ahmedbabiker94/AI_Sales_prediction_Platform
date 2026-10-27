"""Genuine B3 experiment in the pinned disposable worker, never production."""
import copy
import hashlib
import importlib.abc
import json
import os
from pathlib import Path
import runpy
import sys
from unittest.mock import Mock
import numpy as np
import pytest
from src.data.contracts.controlled_h1_execution import ControlledH1ExecutionRequest, ExecutionOutcome
from src.data.contracts.executable_inputs import ExecutableInputRequest, OwnedInputEnvelope
from src.data.contracts.native_xgboost_roundtrip import NativeArtifactEnvelope
from src.data.contracts.runtime_snapshot import RuntimeSnapshotRequest
from src.data.contracts.dataset import operational_json
from src.data.contracts.training_adapter import TrainingSpecification
from src.providers.canonical_training_data_provider import CanonicalTrainingDataProvider
from src.providers.owned_execution_provider import OwnedExecutionProvider
from src.repositories.read_only_dataset_repository import ReadOnlyDatasetRepository
from src.services.canonical_adapter_training_service import CanonicalAdapterTrainingService, CandidateReference
from src.services.canonical_runtime_snapshot_service import CanonicalRuntimeSnapshotService
from src.services.canonical_training_preparation_service import ValueAttestedAvailabilityVerifier, historical_value_identity
from src.services.owned_input_preparation_service import OwnedInputPreparationService
from src.services.owned_xgboost_roundtrip_service import OwnedXGBoostRoundTripService
from src.services.controlled_execution_authority import ControlledExecutionAuthority, ExecutionCapability
from src.services.controlled_h1_execution_service import ControlledH1ExecutionService


@pytest.fixture(scope='module',autouse=True)
def isolated_only():
    assert os.environ.get('B1_ISOLATED_NATIVE_TEST')=='1', 'run only through pinned disposable native runner'
    assert os.getuid()==1000
    forbidden=('mlflow','src.inference.predictor','src.database.db','src.ml.production_resolver',
        'src.deployment.production_artifact_sync','src.repositories.forecast_repository',
        'src.services.canonical_h1_predictor_service','src.services.canonical_forecast_persistence_inspection_service')
    class Trap(importlib.abc.MetaPathFinder):
        def find_spec(self,name,path=None,target=None):
            if any(name==p or name.startswith(p+'.') for p in forbidden):raise AssertionError('operational import')
    trap=Trap();sys.meta_path.insert(0,trap)
    yield
    sys.meta_path.remove(trap)


@pytest.fixture(scope='module')
def genuine(tmp_path_factory):
    factory=runpy.run_path('tests/training/test_canonical_adapter_training.py')['setup'].__wrapped__(tmp_path_factory.mktemp('b3-native'))
    def expand(raw):
        other=raw.copy(deep=True);other['group']='food';other['reported']+=1000
        for row in other.to_dict('records'):raw.loc[len(raw)]=row
    b=factory(raw_change=expand)
    trainer=CanonicalAdapterTrainingService(b['tabular'],b['recurrent'],b['transformer'],authorize_training=lambda _:True)
    spec=TrainingSpecification(family='xgboost',steps=4,input_dtype='float32')
    trained=trainer.train(spec,plan=b['plan'],feature_configuration=b['configuration'])
    assert trained.status=='SUCCESS' and trained.metadata.backend=='native_framework'
    ref=trained.candidate;record=trainer._candidates[id(ref)];state=record[4]
    ledger={}
    with b['provider'].open_read(state.dataset.reference,purpose='production_training') as session:
        for frame in session.batches():
            for row in frame.to_dict('records'):ledger[row['source_record_id']]=row
    repo=ReadOnlyDatasetRepository(str(b['store'].root),reference=state.dataset.reference,series_ids=state.dataset.series_ids)
    provider=CanonicalTrainingDataProvider(repo,authorize_read=lambda *_:True,clock=b['provider'].clock)
    events=[]
    def available(ctx,claims):
        return all(c.series_id==ledger[c.source_record_id]['series_id'] and c.lineage_id==ledger[c.source_record_id]['lineage_id']
            and c.available_at==ledger[c.source_record_id]['available_at' if c.field=='value' else 'promotion_available_at']
            and c.evidence_reference==ledger[c.source_record_id]['availability_reference' if c.field=='value' else 'promotion_evidence']
            and c.available_at<=ctx.capture_cutoff for c in claims)
    def values(ctx,claims):
        return all(c.availability.value_identity==historical_value_identity(ledger[c.availability.source_record_id]['value'])
            and c.dataset_reference==state.dataset.reference for c in claims)
    def authorize(req,ctx,stage):events.append(stage);return True
    snapshots=CanonicalRuntimeSnapshotService(provider,schema=state.canonical_schema,fitted_state=state.canonical_state,
        authorize_snapshot=authorize,verify_history=ValueAttestedAvailabilityVerifier(verify_availability=available,verify_captured_values=values))
    sr=RuntimeSnapshotRequest(dataset=state.dataset,purpose='production_training',forecast_origin=b['at'](6),capture_cutoff=b['at'](6),
        feature_schema_json=operational_json(state.canonical_schema.model_dump(mode='json')),history_periods=4,
        static_fields=('location','department'),external_fields=('promotion',))
    snap=snapshots.capture(sr)
    prep=OwnedInputPreparationService(trainer,snapshots,authorize_inputs=lambda *_:True)
    req=ExecutableInputRequest(candidate_id=trained.metadata.candidate_id,family='xgboost',frozen_state_id=state.state_id,
        compatibility_id=trained.metadata.representation_compatibility_id,snapshot_id=snap.snapshot_id,snapshot_request=sr)
    events.clear();out=prep.prepare(ref,req)
    assert out.status=='PREPARED_INPUTS' and events==['admit','read','after_read','return']*2
    native_owner=OwnedXGBoostRoundTripService(trainer,authorize=lambda *_:True)
    native=native_owner.export(ref)
    original_validation=next(o for o in b['tabular'].prepare(b['plan'],b['configuration'],dtype='float32').outputs
        if o.representation.binding.partition=='validation')
    parity=native_owner.verify(ref,native,original_validation)
    assert parity.original_prediction_sha256==parity.reloaded_prediction_sha256
    provider=OwnedExecutionProvider(trainer,native_owner,prep)
    request=ControlledH1ExecutionRequest(family='xgboost',candidate_id=trained.metadata.candidate_id,artifact_id=native.manifest.artifact_id,payload_sha256=native.manifest.payload_sha256,inputs=out.inputs.binding)
    yield dict(b=b,trainer=trainer,ref=ref,record=record,state=state,native_owner=native_owner,native=native,prep=prep,inputs=out.inputs,
        provider=provider,request=request,trained=trained,parity=parity)
    trainer.discard(ref);repo.close()


@pytest.fixture
def context(genuine):
    s=dict(genuine);s['trainer']._candidates[id(s['ref'])]=s['record']
    control={'wall':100.,'elapsed':0.,'cancel':False};operator=object()
    authority=ControlledExecutionAuthority(s['provider'],operator=operator,wall_clock=lambda:control['wall'])
    service=ControlledH1ExecutionService(authority,elapsed_clock=lambda:control['elapsed'])
    cap=authority.issue(operator,s['ref'],s['native'],s['inputs'],s['request'])
    s.update(control=control,operator=operator,authority=authority,service=service,cap=cap)
    yield s
    s['trainer']._candidates[id(s['ref'])]=s['record']


def execute(s,**kw):
    args=dict(capability=s['cap'],cancelled=lambda:s['control']['cancel']);args.update(kw)
    return s['service'].execute(s['ref'],s['native'],s['inputs'],s['request'],**args)


def denied(result):assert result.status=='DENIED' and result.result is None


def test_genuine_h1_prediction_same_owner_native_reference(context,monkeypatch):
    from src.data.contracts.model_candidate import strict_float32
    from src.training.canonical_adapters import NativeBackend
    from src.preprocessing.canonical_tabular import FrozenTabularPreprocessor
    from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
    s=context;output=s['prep'].require(s['inputs']);root=s['b']['store'].root
    inventory=lambda:{str(p):(p.stat().st_mtime_ns,hashlib.sha256(p.read_bytes()).hexdigest()) for p in root.rglob('*') if p.is_file()}
    before=inventory()
    for cls,name in ((NativeBackend,'fit'),(FrozenTabularPreprocessor,'transform'),(CanonicalFeaturePipeline,'fit_state')):
        monkeypatch.setattr(cls,name,Mock(side_effect=AssertionError('no refit or preparation during execution')))
    result=execute(s)
    assert result.status=='EXECUTED',result.reason
    import xgboost as xgb
    assert xgb.__version__=='3.2.0'
    matrix=strict_float32(output.values)
    reference=s['record'][3].predict(matrix)
    # Independent direct native API reload, same authorized rows and no labels.
    loaded=xgb.Booster(params={'nthread':1});loaded.load_model(bytearray(s['native'].payload))
    second=loaded.predict(xgb.DMatrix(matrix,feature_names=list(output.matrix.columns),nthread=1),validate_features=True)
    actual=np.array(result.result.predictions,dtype=np.float32)
    assert reference.tobytes()==second.tobytes()==actual.tobytes()
    assert result.result.prediction_sha256==hashlib.sha256(actual.astype('<f4').tobytes()).hexdigest()
    assert sorted(row[1] for row in output.values)==[32.,132.,1032.,1132.]
    assert tuple(output.matrix.columns)==('location','lag1','promotion')
    assert tuple(str(d) for d in output.matrix.dtypes)==('int8','float32','int8')
    assert result.result.grant.request==s['request'] and not result.result.persistable and not result.result.production_permission
    assert inventory()==before
    denied(execute(s)) # one-shot, not a reusable receipt
    print('B3_NATIVE_EVIDENCE',json.dumps({'framework':xgb.__version__,'artifact_id':s['native'].manifest.artifact_id,
        'payload_bytes':len(s['native'].payload),'payload_sha256':s['native'].manifest.payload_sha256,
        'candidate_id':s['trained'].metadata.candidate_id,'snapshot_id':s['request'].inputs.request.snapshot_id,
        'input_binding_id':s['inputs'].binding.binding_id,'result_id':result.result.result_id,
        'predictions':result.result.predictions,'prediction_sha256':result.result.prediction_sha256,
        'comparison':'bitwise_float32; absolute tolerance 0; relative tolerance 0','rows':len(reference)},sort_keys=True))


@pytest.mark.parametrize('fault',['missing','forged','copy','foreign','revoked','expired','deadline','cancel','owner_revoked','copied_owner'])
def test_authority_rejection_before_load(context,monkeypatch,fault):
    s=context;cap=s['cap'];ref=s['ref']
    if fault=='missing':cap=None
    elif fault=='forged':cap=ExecutionCapability()
    elif fault=='copy':cap=object()
    elif fault=='foreign':
        other=ControlledExecutionAuthority(s['provider'],operator=s['operator'],wall_clock=lambda:100.)
        cap=other.issue(s['operator'],ref,s['native'],s['inputs'],s['request'])
    elif fault=='revoked':s['authority'].revoke(s['operator'],cap)
    elif fault=='expired':s['control']['wall']=131.
    elif fault=='deadline':s['control']['wall']=130.
    elif fault=='cancel':s['control']['cancel']=True
    elif fault=='owner_revoked':s['trainer'].discard(ref)
    else:ref=copy.copy(ref)
    spy=Mock(side_effect=AssertionError('unauthorized parser'))
    monkeypatch.setattr('src.execution.controlled_h1_adapters.XGBoostH1Adapter.load',spy)
    denied(s['service'].execute(ref,s['native'],s['inputs'],s['request'],capability=cap,cancelled=lambda:s['control']['cancel']))
    spy.assert_not_called()


@pytest.mark.parametrize('field',['candidate','family','tenant','series','publication','origin','cutoff','snapshot','state','compatibility','artifact','digest','order','dtype'])
def test_bound_request_substitution_rejected(context,field):
    s=context;r=s['request'];b=r.inputs;inp=b.request;sr=inp.snapshot_request
    if field in ('artifact','digest'):r=r.model_copy(update={('artifact_id' if field=='artifact' else 'payload_sha256'):'f'*64})
    elif field=='family':b=b.model_copy(update={'selection':b.selection.model_copy(update={'family':'lightgbm'})})
    elif field in ('tenant','series','publication','origin','cutoff'):
        from dataclasses import replace
        from uuid import UUID
        if field=='tenant':sr=sr.model_copy(update={'dataset':sr.dataset.model_copy(update={'reference':replace(sr.dataset.reference,organization_id=UUID(int=999))})})
        elif field=='series':sr=sr.model_copy(update={'dataset':sr.dataset.model_copy(update={'series_ids':sr.dataset.series_ids[:1]})})
        elif field=='publication':sr=sr.model_copy(update={'dataset':sr.dataset.model_copy(update={'admission_digest':'f'*64})})
        else:sr=sr.model_copy(update={('forecast_origin' if field=='origin' else 'capture_cutoff'):s['b']['at'](8)})
        inp=inp.model_copy(update={'snapshot_request':sr});b=b.model_copy(update={'request':inp})
    elif field in ('order','dtype'):b=b.model_copy(update={'representation_id':'f'*64})
    else:
        name={'candidate':'candidate_id','snapshot':'snapshot_id','state':'frozen_state_id','compatibility':'compatibility_id'}[field]
        b=b.model_copy(update={'request':inp.model_copy(update={name:'f'*64})})
    if field not in ('artifact','digest'):r=r.model_copy(update={'inputs':b})
    denied(s['service'].execute(s['ref'],s['native'],s['inputs'],r,capability=s['cap']))


@pytest.mark.parametrize('fault',['native_missing','input_missing','native_copy','native_digest','input_copy','input_values','input_order','input_dtype','input_h1'])
def test_issued_envelope_tampering(context,monkeypatch,fault):
    s=context;n=s['native'];i=s['inputs'];undo=lambda:None
    if fault=='native_missing':n=None
    elif fault=='input_missing':i=None
    elif fault=='native_copy':n=NativeArtifactEnvelope(n.manifest,n.payload)
    elif fault=='native_digest':
        raw=n.payload;object.__setattr__(n,'payload',raw[:-1]+b'x');undo=lambda:object.__setattr__(n,'payload',raw)
    elif fault=='input_copy':i=OwnedInputEnvelope(i.binding,i.output)
    else:
        old=i.output;o=old
        if fault=='input_values':o=o.model_copy(update={'values':tuple(tuple(float('nan') for _ in row) for row in o.values)})
        elif fault=='input_order':o=o.model_copy(update={'representation':o.representation.model_copy(update={'feature_schema':o.representation.feature_schema.model_copy(update={'features':tuple(reversed(o.representation.feature_schema.features))})})})
        elif fault=='input_dtype':o=o.model_copy(update={'values':tuple(tuple(True for _ in row) for row in o.values)})
        else:o=o.model_copy(update={'representation':o.representation.model_copy(update={'shape':(1,999)})})
        object.__setattr__(i,'output',o);undo=lambda:object.__setattr__(i,'output',old)
    trap=Mock(side_effect=AssertionError('tampered parser'))
    monkeypatch.setattr('src.execution.controlled_h1_adapters.XGBoostH1Adapter.load',trap)
    try:denied(s['service'].execute(s['ref'],n,i,s['request'],capability=s['cap']))
    finally:undo()
    trap.assert_not_called()


@pytest.mark.parametrize('edge',['cancel','wall','elapsed'])
@pytest.mark.parametrize('action',['revoke','owner_revoke','cancel','deadline'])
def test_actual_final_callback_edge_rejects_without_result(context,edge,action):
    s=context;counts={'cancel':0,'wall':0,'elapsed':0}
    def count(k,value):counts[k]+=1;return value
    s['authority']._clock=lambda:count('wall',100.)
    s['service']._clock=lambda:count('elapsed',0.)
    first=execute(s,cancelled=lambda:count('cancel',False));assert first.status=='EXECUTED'
    total=counts[edge];assert total>1
    s['authority']._clock=lambda:100.
    s['cap']=s['authority'].issue(s['operator'],s['ref'],s['native'],s['inputs'],s['request'])
    n=0
    def callback():
        nonlocal n
        n+=1
        if n==total:
            if action=='revoke':s['authority'].revoke(s['operator'],s['cap'])
            elif action=='owner_revoke':s['trainer'].discard(s['ref'])
            elif action=='cancel' and edge!='cancel':s['authority'].revoke(s['operator'],s['cap'])
            elif action=='deadline' and edge=='cancel':s['control']['wall']=130.
        if edge=='cancel':return n==total and action=='cancel'
        if edge=='wall':return 130. if n==total and action=='deadline' else 100.
        return 30. if n==total and action=='deadline' else 0.
    s['authority']._clock=(callback if edge=='wall' else lambda:s['control']['wall'])
    s['service']._clock=(callback if edge=='elapsed' else lambda:0.)
    denied(execute(s,cancelled=callback if edge=='cancel' else lambda:False))
    assert n==total


@pytest.mark.parametrize('fault',['dependency','version','timeout','nonfinite','shape','dtype','native_schema','rows','cells','bytes','exception'])
def test_native_and_resource_failures_consume_attempt(context,monkeypatch,fault):
    s=context
    import xgboost as xgb
    if fault=='dependency':monkeypatch.setattr('src.execution.controlled_h1_adapters.importlib.import_module',Mock(side_effect=ModuleNotFoundError('PRIVATE')))
    elif fault=='version':monkeypatch.setattr(xgb,'__version__','0.0')
    elif fault=='timeout':monkeypatch.setattr(xgb.Booster,'load_model',Mock(side_effect=TimeoutError('PRIVATE')))
    elif fault in ('nonfinite','shape','dtype'):
        real=xgb.Booster.predict
        def wrong(self,*a,**kw):
            values=real(self,*a,**kw)
            return np.full(values.shape,np.nan,dtype=np.float32) if fault=='nonfinite' else values[:,None] if fault=='shape' else values.astype('float64')
        monkeypatch.setattr(xgb.Booster,'predict',wrong)
    elif fault=='native_schema':
        real=xgb.Booster.load_model
        def wrong(self,*a,**kw):real(self,*a,**kw);self.feature_names=list(reversed(self.feature_names))
        monkeypatch.setattr(xgb.Booster,'load_model',wrong)
    elif fault=='exception':s['service']._clock=Mock(side_effect=RuntimeError('PRIVATE'))
    else:
        limits=s['request'].limits.model_copy(update={{'rows':'max_rows','cells':'max_cells','bytes':'max_artifact_bytes'}[fault]:1})
        s['request']=s['request'].model_copy(update={'limits':limits})
        if fault=='bytes':
            with pytest.raises(ValueError):s['authority'].issue(s['operator'],s['ref'],s['native'],s['inputs'],s['request'])
            return
        s['cap']=s['authority'].issue(s['operator'],s['ref'],s['native'],s['inputs'],s['request'])
    out=execute(s);assert out.status in ('DENIED','UNAVAILABLE') and out.result is None and 'PRIVATE' not in out.canonical_json
    denied(execute(s))


def test_operator_identity_and_unrelated_approvals_not_authority(context):
    s=context
    for operator in ('operator',object(),None):
        with pytest.raises(ValueError):s['authority'].issue(operator,s['ref'],s['native'],s['inputs'],s['request'])
    for cap in ('APPROVED_FOR_FURTHER_VALIDATION',s['parity'],{'status':'UNAVAILABLE'},object()):denied(execute(s,capability=cap))
    with pytest.raises(ValueError):ExecutionOutcome(status='EXECUTED',reason='CONTROLLED_VALIDATION_ONLY')
    assert not hasattr(s['cap'],'request')


def test_independent_execution_services_cannot_replay(context):
    s=context;assert execute(s).status=='EXECUTED'
    other=ControlledH1ExecutionService(s['authority'])
    denied(other.execute(s['ref'],s['native'],s['inputs'],s['request'],capability=s['cap']))


def test_different_genuine_m6_owner_rejected(context):
    s=context
    other=s['trainer'].train(TrainingSpecification(family='xgboost',steps=4,seed=43),plan=s['b']['plan'],feature_configuration=s['b']['configuration'])
    assert other.status=='SUCCESS' and other.metadata.backend=='native_framework'
    try:
        with pytest.raises(ValueError):s['authority'].issue(s['operator'],other.candidate,s['native'],s['inputs'],s['request'])
        denied(s['service'].execute(other.candidate,s['native'],s['inputs'],s['request'],capability=s['cap']))
    finally:s['trainer'].discard(other.candidate)


def test_m7_native_publication_still_unavailable(context,tmp_path):
    from src.services.candidate_packaging_service import CandidatePackagingService
    from src.providers.candidate_package_provider import CandidatePackageProvider
    from src.repositories.candidate_package_repository import CandidatePackageRepository
    s=context;p=CandidatePackageProvider(CandidatePackageRepository(str(tmp_path)))
    service=CandidatePackagingService(s['trainer'],p,authorize_packaging=lambda *_:True)
    assert service.package(s['ref'],test_only=True).status=='UNAVAILABLE'


@pytest.mark.parametrize('family',['lightgbm','lstm','gru','transformer'])
def test_future_family_unavailable_original_m6_metadata_only(tmp_path_factory,family):
    # Contract-only family dispatch. These owners are M6 estimator-stub candidates,
    # never represented as native or executed; B1 is not called for these families.
    cases=runpy.run_path('tests/runtime/test_owned_executable_inputs.py')
    gen=cases['build'].__wrapped__(tmp_path_factory);build=next(gen)
    try:
        s=build(family);trainer=s['b']['trainer']
        from src.training.canonical_adapters import NativeBackend
        trainer._backend_factory=NativeBackend
        native_owner=OwnedXGBoostRoundTripService(trainer,authorize=lambda *_:False)
        provider=OwnedExecutionProvider(trainer,native_owner,s['prep'])
        authority=ControlledExecutionAuthority(provider,operator=object())
        r=ControlledH1ExecutionRequest(family=family,candidate_id=s['result'].metadata.candidate_id,
            artifact_id='a'*64,payload_sha256='b'*64,inputs=None)
        out=ControlledH1ExecutionService(authority).execute(s['result'].candidate,None,None,r)
        assert out.status=='UNAVAILABLE' and out.result is None
    finally:
        try:next(gen)
        except StopIteration:pass



def test_reentrant_second_worker_cannot_consume_same_grant(context,monkeypatch):
    from src.execution.controlled_h1_adapters import XGBoostH1Adapter
    s=context;load=XGBoostH1Adapter.load;attempts=[]
    def reentrant(adapter,evidence):
        other=ControlledH1ExecutionService(s['authority'])
        attempts.append(other.execute(s['ref'],s['native'],s['inputs'],s['request'],capability=s['cap']))
        return load(adapter,evidence)
    monkeypatch.setattr(XGBoostH1Adapter,'load',reentrant)
    assert execute(s).status=='EXECUTED' and len(attempts)==1
    denied(attempts[0])


@pytest.mark.parametrize('field',['format','framework_version','feature_schema','frozen_state_id'])
def test_native_manifest_substitution_before_parser(context,monkeypatch,field):
    s=context;n=s['native'];old=n.manifest
    if field=='feature_schema':value=old.feature_schema.model_copy(update={'features':tuple(reversed(old.feature_schema.features))})
    else:value={'format':'pickle','framework_version':'3.1.0','frozen_state_id':'f'*64}[field]
    object.__setattr__(n,'manifest',old.model_copy(update={field:value}))
    spy=Mock(side_effect=AssertionError('untrusted parser'));monkeypatch.setattr('src.execution.controlled_h1_adapters.XGBoostH1Adapter.load',spy)
    try:denied(execute(s))
    finally:object.__setattr__(n,'manifest',old)
    spy.assert_not_called()


def test_revocation_after_load_prevents_prediction(context,monkeypatch):
    from src.execution.controlled_h1_adapters import XGBoostH1Adapter
    s=context;load=XGBoostH1Adapter.load
    def revoke(adapter,evidence):
        loaded=load(adapter,evidence);s['authority'].revoke(s['operator'],s['cap']);return loaded
    monkeypatch.setattr(XGBoostH1Adapter,'load',revoke)
    spy=Mock(side_effect=AssertionError('revoked prediction'));monkeypatch.setattr(XGBoostH1Adapter,'predict',spy)
    denied(execute(s));spy.assert_not_called()


def test_no_unknown_family_or_positive_outcome_coercion(context):
    s=context
    with pytest.raises(ValueError):ControlledH1ExecutionRequest.model_validate_json(s['request'].canonical_json.replace('"xgboost"','"unknown"'))
    with pytest.raises(ValueError):ExecutionOutcome(status='EXECUTED',reason='CONTROLLED_VALIDATION_ONLY',result=None)
    with pytest.raises(ValueError):s['authority'].issue(s['operator'],s['ref'],s['native'],s['inputs'],s['request'],lifetime_seconds=0)
    with pytest.raises(ValueError):s['authority'].issue(s['operator'],s['ref'],s['native'],s['inputs'],s['request'],execution_seconds=31)


def test_actual_disposable_worker_boundary():
    import resource
    assert os.getuid()==1000 and not Path('/var/run/docker.sock').exists()
    mounts=Path('/proc/mounts').read_text().splitlines()
    for target in ('/','/app/src','/app/tests'):
        entries=[line.split() for line in mounts if line.split()[1]==target]
        assert len(entries)==1 and 'ro' in entries[0][3].split(',')
    assert int(Path('/sys/fs/cgroup/memory.max').read_text())==1073741824
    assert int(Path('/sys/fs/cgroup/pids.max').read_text())==64
    quota,period=map(int,Path('/sys/fs/cgroup/cpu.max').read_text().split());assert quota==period
    status=Path('/proc/self/status').read_text()
    assert 'CapEff:\t0000000000000000' in status and 'NoNewPrivs:\t1' in status
    assert resource.getrlimit(resource.RLIMIT_CPU)==(180,180)
    assert not any(line.split()[1]=='00000000' for line in Path('/proc/net/route').read_text().splitlines()[1:])
    assert not any(k.startswith(('AWS_','AZURE_','GOOGLE_','HTTP_PROXY','HTTPS_PROXY')) for k in os.environ)
    assert set(os.listdir('/app'))=={'src','tests'}


def test_worker_timeout_and_cancellation_cleanup_contract(monkeypatch):
    import subprocess
    runner=runpy.run_path('tests/native/run_b1.py')['run'];calls=[]
    monkeypatch.setattr(subprocess,'check_output',lambda *a,**kw:'sha256:PINNED')
    def fake(command,**kw):
        calls.append(command)
        if 'start' in command:raise subprocess.TimeoutExpired(command,kw['timeout'])
        return type('Result',(),{'returncode':0})()
    monkeypatch.setattr(subprocess,'run',fake)
    assert runner(['tests/runtime/test_controlled_h1_execution.py::test_actual_disposable_worker_boundary'],timeout=.1)==124
    create=next(c for c in calls if 'create' in c)
    for pair in (('--network','none'),('--cap-drop','ALL'),('--user','1000:1000'),('--memory','1g'),('--cpus','1'),('--pids-limit','64')):
        pos=create.index(pair[0]);assert create[pos+1]==pair[1]
    assert '--read-only' in create and not any('docker.sock' in arg for arg in create if arg.startswith('type=bind'))
    assert calls[-1][2:4]==['rm','-f']
    calls.clear()
    def interrupted(command,**kw):
        calls.append(command)
        if 'start' in command:raise KeyboardInterrupt()
        return type('Result',(),{'returncode':0})()
    monkeypatch.setattr(subprocess,'run',interrupted)
    with pytest.raises(KeyboardInterrupt):runner(['tests/runtime/test_controlled_h1_execution.py::test_actual_disposable_worker_boundary'])
    assert calls[-1][2:4]==['rm','-f']


def test_expiry_cannot_be_reset_by_clock_rollback(context):
    s=context;s['control']['wall']=131.;denied(execute(s))
    s['control']['wall']=100.;denied(execute(s))


def test_equivalent_complete_execution_evidence_is_deterministic(context):
    s=context;first=execute(s);assert first.status=='EXECUTED'
    s['cap']=s['authority'].issue(s['operator'],s['ref'],s['native'],s['inputs'],s['request'])
    second=execute(s);assert first.canonical_bytes==second.canonical_bytes
    # Equal declarations are permitted, but neither is an executable capability.
    denied(execute(s,capability=first.result.grant))


def test_actor_strings_cannot_be_configured_as_host_issuer(context):
    s=context
    for operator in ('admin',None,{},True):
        with pytest.raises(ValueError):ControlledExecutionAuthority(s['provider'],operator=operator)
    with pytest.raises(TypeError):copy.copy(s['cap'])
