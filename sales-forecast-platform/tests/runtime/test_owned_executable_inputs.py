"""B2 uses real publication/capture/M3 execution; no native operations."""
from contextlib import contextmanager
from copy import copy
from dataclasses import replace
import hashlib
import importlib.abc
import os
from pathlib import Path
import runpy
import sqlite3
import sys
from unittest.mock import Mock
from uuid import UUID
import pandas as pd
import pytest
from src.data.contracts.executable_inputs import ExecutableInputRequest, InputAdapterSelection, OwnedInputEnvelope
from src.data.contracts.runtime_snapshot import RuntimeSnapshotRequest
from src.data.contracts.features import FeatureConfiguration
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
from src.preprocessing.canonical_tabular import FrozenTabularPreprocessor
from src.providers.canonical_training_data_provider import CanonicalTrainingDataProvider
from src.repositories.read_only_dataset_repository import ReadOnlyDatasetRepository
from src.services.canonical_runtime_snapshot_service import CanonicalRuntimeSnapshotService
from src.services.canonical_training_preparation_service import ValueAttestedAvailabilityVerifier, historical_value_identity
from src.services.owned_input_preparation_service import OwnedInputPreparationService
from src.training.canonical_adapters import NativeBackend


@pytest.fixture(scope='module',autouse=True)
def forbid_native_for_entire_b2_module():
    class Guard(importlib.abc.MetaPathFinder):
        def find_spec(self,name,path=None,target=None):
            if name.split('.')[0] in ('xgboost','lightgbm','torch','tensorflow','mlflow'):
                raise AssertionError('B2 never imports native frameworks or registry')
    guard=Guard();sys.meta_path.insert(0,guard)
    patch=pytest.MonkeyPatch()
    def no(*a,**kw):raise AssertionError('B2 forbids native model fitting')
    patch.setattr(NativeBackend,'fit',no)
    try:yield
    finally:patch.undo();sys.meta_path.remove(guard)


@pytest.fixture(scope="module")
def build(tmp_path_factory):
    tmp_path=tmp_path_factory.mktemp("b2-synthetic")
    factory = runpy.run_path('tests/training/test_canonical_adapter_training.py')['setup'].__wrapped__(tmp_path)
    stores = []; cache = {}
    def create(family='xgboost', rule='known_in_advance'):
        def expand(raw):
            other = raw.copy(deep=True); other['group'] = 'food'; other['reported'] += 1000
            for row in other.to_dict('records'): raw.loc[len(raw)] = row
        key=(family,rule)
        if key not in cache:
            b = factory(raw_change=expand)
            if rule != 'known_in_advance':
                data = b['configuration'].model_dump(); data['execution']['exogenous'][0]['availability'] = rule
                b['configuration'] = FeatureConfiguration.model_validate(data)
                p = b['plan'].model_dump(); p['configuration_digest'] = CanonicalFeaturePipeline(b['configuration']).digest
                b['plan'] = type(b['plan']).model_validate(p)
                if rule == 'external_forecast': b['service'].authorize_external = lambda *_:True
            result = b['run'](family)
            assert result.status == 'SUCCESS' and result.metadata.backend == 'test_stub'
            record = b['trainer']._candidates[id(result.candidate)]
            state = record[4]
            base = state if family in ('xgboost','lightgbm') else state.encoder.base if family=='transformer' else state.base
            # Independent original-value ledger from actual admitted repository rows.
            ledger = {}
            with b['provider'].open_read(base.dataset.reference,purpose='production_training') as session:
                for frame in session.batches():
                    for row in frame.to_dict('records'): ledger[row['source_record_id']] = row
            cache[key]=(b,result,record,state,base,ledger)
        b,result,record,state,base,ledger=cache[key]
        # Restore the original test fixture between adversarial cases only.
        b["trainer"]._candidates[id(result.candidate)]=record
        control = dict(clock=0., source_clock=0., cancel=False, read=True, authority=True, values=True, hook=None)
        repo = ReadOnlyDatasetRepository(str(b['store'].root),reference=base.dataset.reference,series_ids=base.dataset.series_ids)
        stores.append(repo)
        provider = CanonicalTrainingDataProvider(repo,authorize_read=lambda *_:control['read'],clock=b['provider'].clock)
        events = []
        def authorize(req,ctx,stage):
            events.append(stage)
            if control['hook']: control['hook'](stage)
            return control['authority']
        def availability(ctx,claims):
            for claim in claims:
                expected = ledger[claim.source_record_id]
                field = 'available_at' if claim.field=='value' else 'promotion_available_at'
                evidence = 'availability_reference' if claim.field=='value' else 'promotion_evidence'
                if (claim.series_id != expected['series_id'] or claim.lineage_id != expected['lineage_id']
                        or claim.available_at != expected[field] or claim.evidence_reference != expected[evidence]
                        or claim.available_at > ctx.capture_cutoff): return False
            return True
        def values(ctx,claims):
            return control['values'] and all(c.availability.value_identity==historical_value_identity(ledger[c.availability.source_record_id]['value'])
                and c.dataset_reference==base.dataset.reference for c in claims)
        snapshots = CanonicalRuntimeSnapshotService(provider,schema=base.canonical_schema,fitted_state=base.canonical_state,
            authorize_snapshot=authorize,verify_history=ValueAttestedAvailabilityVerifier(verify_availability=availability,verify_captured_values=values),
            authorize_external_forecast=lambda _:control['authority'],elapsed_clock=lambda:control['source_clock'])
        from src.data.contracts.dataset import operational_json
        sr = RuntimeSnapshotRequest(dataset=base.dataset,purpose='production_training',forecast_origin=b['at'](6),capture_cutoff=b['at'](6),
            feature_schema_json=operational_json(base.canonical_schema.model_dump(mode='json')),history_periods=4,
            static_fields=('location','department'),external_fields=('promotion',))
        snapshot = snapshots.capture(sr)
        request = ExecutableInputRequest(candidate_id=result.metadata.candidate_id,family=family,frozen_state_id=state.state_id,
            compatibility_id=result.metadata.representation_compatibility_id,snapshot_id=snapshot.snapshot_id,snapshot_request=sr)
        prep = OwnedInputPreparationService(b['trainer'],snapshots,authorize_inputs=lambda *_:True,elapsed_clock=lambda:control['clock'])
        if family=='xgboost' and 'b2_expected' not in b:
            b['b2_expected']=b['tabular'].prepare(b['plan'],b['configuration'],model_family='xgboost',dtype='float32')
        expected=b.get('b2_expected')
        events.clear()
        return dict(b=b,result=result,repo=repo,provider=provider,snapshots=snapshots,prep=prep,request=request,snapshot=snapshot,
                    control=control,events=events,expected=expected,ledger=ledger)
    yield create
    for store in stores: store.close()


def deny(out):
    assert out.status == 'DENIED' and out.inputs is None


def tree(root):
    return {str(p.relative_to(root)):(p.stat().st_size,p.stat().st_mtime_ns,hashlib.sha256(p.read_bytes()).hexdigest()) for p in root.rglob('*') if p.is_file()}


@contextmanager
def traps(monkeypatch):
    forbidden = ('xgboost','torch','tensorflow','lightgbm','mlflow','src.database.db','src.inference.predictor',
        'src.services.canonical_h1_predictor_service','src.services.canonical_forecast_persistence_inspection_service',
        'src.deployment.production_artifact_sync','src.ml.production_resolver')
    class Guard(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if any(name==m or name.startswith(m+'.') for m in forbidden): raise AssertionError('operational import')
    guard=Guard();sys.meta_path.insert(0,guard)
    opened=os.open; connect=sqlite3.connect; sql=[]
    def ro_open(path, flags, *a, **kw):
        assert not flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)
        return opened(path,flags,*a,**kw)
    def ro_connect(path,*a,**kw):
        assert type(path) is str and path.startswith('file:/proc/self/fd/') and path.endswith('?mode=ro') and kw.get('uri') is True
        c=connect(path,*a,**kw)
        def trace(statement):
            sql.append(statement)
            assert statement.startswith(('SELECT ','BEGIN'))
        c.set_trace_callback(trace)
        return c
    def no(*a,**kw): raise AssertionError('write/fitting operation')
    with monkeypatch.context() as patch:
        patch.setattr(os,'open',ro_open);patch.setattr(sqlite3,'connect',ro_connect)
        for name in ('mkdir','unlink','write_text','write_bytes','touch','rmdir','rename','replace'):
            patch.setattr(Path,name,no)
        patch.setattr(CanonicalFeaturePipeline,'fit_state',no)
        patch.setattr(NativeBackend,'fit',no)
        try: yield sql
        finally: sys.meta_path.remove(guard)


def test_real_four_series_read_only_frozen_m3_exact_inputs(build,monkeypatch):
    s=build();root=s['b']['store'].root;before=tree(root);seen=[]
    real=FrozenTabularPreprocessor.transform
    def observed(self,frame,requests,*args,**kw):
        seen.append((frame.copy(),requests.copy()))
        return real(self,frame,requests,*args,**kw)
    monkeypatch.setattr(FrozenTabularPreprocessor,'transform',observed)
    with traps(monkeypatch) as sql:
        out=s['prep'].prepare(s['result'].candidate,s['request'])
    assert out.status=='PREPARED_INPUTS',out.reason
    assert tree(root)==before and sql and all(q.startswith(('SELECT ','BEGIN')) for q in sql)
    assert len(seen)==1 and len(set(seen[0][0].series_id))==4
    assert seen[0][0].period_end.max()<=s['b']['at'](6)
    expected=next(o for o in s['expected'].outputs if o.representation.binding.partition=='validation')
    actual=s['prep'].require(out.inputs)
    assert actual.values==expected.values and actual.state==expected.state
    assert actual.representation.compatibility_id==expected.representation.compatibility_id
    assert tuple(actual.matrix.columns)==('location','lag1','promotion')
    assert tuple(str(d) for d in actual.matrix.dtypes)==('int8','float32','int8')
    for grid,row in zip(actual.representation.samples,actual.values):
        point=grid[0]
        raw=next(v for v in s['ledger'].values() if v['series_id']==point.series_id and v['period_start']==s['b']['at'](5))
        assert row[1]==raw['value']
    assert s['events']==['admit','read','after_read','return']*2
    assert not out.inputs.binding.runtime_permission and not out.inputs.binding.production_permission
    again=s['prep'].prepare(s['result'].candidate,s['request'])
    assert again.inputs.binding.canonical_bytes==out.inputs.binding.canonical_bytes
    with pytest.raises(Exception):out.inputs.output.values=()
    with pytest.raises(ValueError):s['prep'].require(OwnedInputEnvelope(out.inputs.binding,out.inputs.output))


@pytest.mark.parametrize('fault',['copy','new','candidate','family','state','compatibility','snapshot','tenant','series','publication','origin','cutoff','schema'])
def test_substitutions_before_transformation(build,monkeypatch,fault):
    s=build();ref=s['result'].candidate;r=s['request'];sr=r.snapshot_request
    if fault=='copy':ref=copy(ref)
    elif fault=='new':ref=type(ref)()
    elif fault in ('candidate','state','compatibility','snapshot'):
        key={'candidate':'candidate_id','state':'frozen_state_id','compatibility':'compatibility_id','snapshot':'snapshot_id'}[fault]
        r=r.model_copy(update={key:'f'*64})
    elif fault=='family':r=r.model_copy(update={'family':'lightgbm'})
    else:
        if fault=='tenant':sr=sr.model_copy(update={'dataset':sr.dataset.model_copy(update={'reference':replace(sr.dataset.reference,organization_id=UUID(int=999))})})
        elif fault=='series':sr=sr.model_copy(update={'dataset':sr.dataset.model_copy(update={'series_ids':sr.dataset.series_ids[:1]})})
        elif fault=='publication':sr=sr.model_copy(update={'dataset':sr.dataset.model_copy(update={'admission_digest':'f'*64})})
        elif fault=='origin':sr=sr.model_copy(update={'forecast_origin':s['b']['at'](8),'capture_cutoff':s['b']['at'](8)})
        elif fault=='cutoff':sr=sr.model_copy(update={'capture_cutoff':s['b']['at'](5)})
        else:sr=sr.model_copy(update={'feature_schema_json':'{}'})
        r=r.model_copy(update={'snapshot_request':sr})
    with traps(monkeypatch):deny(s['prep'].prepare(ref,r))


@pytest.mark.parametrize('family',['lightgbm','lstm','gru','transformer'])
def test_future_adapter_unavailable_without_capture(build,monkeypatch,family):
    s=build(family)
    monkeypatch.setattr(s['provider'],'open_read',Mock(side_effect=AssertionError('unsupported data access')))
    out=s['prep'].prepare(s['result'].candidate,s['request'])
    assert out.status=='UNAVAILABLE' and out.inputs is None


@pytest.mark.parametrize('fault',['read','authority','values','cancel','deadline','revoke','source_clock_revoke','source_clock_deadline','snapshot_change'])
def test_terminal_failures_have_no_partial_inputs(build,monkeypatch,fault):
    s=build();c=s['control'];r=s['request'];ref=s['result'].candidate;returns=[]
    # A smaller outer deadline also applies to the accepted capture's final clock.
    r=r.model_copy(update={'limits':r.limits.model_copy(update={'max_elapsed_seconds':1})})
    def hook(stage):
        if stage=='return':
            returns.append(1)
            if len(returns)==2:
                if fault in ('read','authority','values'):c[fault]=False
                elif fault=='cancel':c['cancel']=True
                elif fault=='deadline':c['clock']=1.
                elif fault=='revoke':s['b']['trainer'].discard(ref)
                elif fault=='snapshot_change':c['source_clock']=float('nan')
    c['hook']=hook
    if fault in ('read','values'):
        def inputs_authority(metadata, request, stage):
            if stage=='return':c[fault]=False
            return True
        s['prep']._authorize=inputs_authority
    if fault.startswith('source_clock_'):
        def clock():
            if len(returns)==2:
                if fault=='source_clock_revoke':s['b']['trainer'].discard(ref)
                else:c['clock']=1.
            return 0.
        s['snapshots']._clock=clock
    with traps(monkeypatch):
        out=s['prep'].prepare(ref,r,cancelled=lambda:c['cancel'])
    deny(out)


def test_external_forecast_policy_unavailable_not_inferred_from_publication(build,monkeypatch):
    s=build(rule='external_forecast')
    monkeypatch.setattr(s['provider'],'open_read',Mock(side_effect=AssertionError('unsupported policy read')))
    out=s['prep'].prepare(s['result'].candidate,s['request'])
    assert out.status=='UNAVAILABLE' and out.inputs is None


def test_read_only_repository_cannot_create_missing_catalog_or_schema(build,tmp_path):
    s=build();root=tmp_path/'absent';root.mkdir(mode=0o700)
    repo=ReadOnlyDatasetRepository(str(root),reference=s['request'].snapshot_request.dataset.reference,series_ids=s['request'].snapshot_request.dataset.series_ids)
    provider=CanonicalTrainingDataProvider(repo,authorize_read=lambda *_:True,clock=s['provider'].clock)
    try:
        with pytest.raises(ValueError):
            with provider.open_read(s['request'].snapshot_request.dataset.reference,purpose='production_training') as session:list(session.batches())
        assert not tuple(root.iterdir())
    finally:repo.close()


def test_sqlite_mode_ro_denies_writes_even_without_authorizer(build):
    from src.data.contracts.dataset import StagingBudgets
    s=build();before=tree(s['b']['store'].root)
    with s['repo']._publication_session(StagingBudgets()):
        connection=s['repo']._active.connection
        connection.set_authorizer(None)
        with pytest.raises(sqlite3.OperationalError):connection.execute("UPDATE attempts SET state='aborted'")
    assert tree(s['b']['store'].root)==before


@pytest.mark.parametrize('fault',['catalog','schema','recovery','catalog_symlink','chunk_directory_symlink'])
def test_failed_read_does_not_initialize_or_repair_storage(build,tmp_path,monkeypatch,fault):
    s=build();root=tmp_path/'read-only-case';root.mkdir(mode=0o700);(root/'writer.lock').touch(mode=0o600)
    original=s['b']['store'].root
    if fault=='schema':
        with sqlite3.connect(root/'catalog.sqlite') as c:c.execute('CREATE TABLE unrelated (x INTEGER)')
    elif fault=='catalog_symlink':(root/'catalog.sqlite').symlink_to(original/'catalog.sqlite')
    elif fault in ('recovery','chunk_directory_symlink'):
        (root/'catalog.sqlite').write_bytes((original/'catalog.sqlite').read_bytes())
        if fault=='recovery':(root/'catalog.sqlite-journal').write_bytes(b'incomplete')
        else:(root/str(s['request'].snapshot_request.dataset.reference.attempt_id)).symlink_to(original/str(s['request'].snapshot_request.dataset.reference.attempt_id),target_is_directory=True)
    before=tree(root);repo=ReadOnlyDatasetRepository(str(root),reference=s['request'].snapshot_request.dataset.reference,series_ids=s['request'].snapshot_request.dataset.series_ids)
    provider=CanonicalTrainingDataProvider(repo,authorize_read=lambda *_:True,clock=s['provider'].clock)
    try:
        with traps(monkeypatch):
            with pytest.raises(ValueError):
                with provider.open_read(s['request'].snapshot_request.dataset.reference,purpose='production_training') as session:list(session.batches())
        assert tree(root)==before
    finally:repo.close()


def test_root_ancestor_symlink_rejected_without_creation(build,tmp_path):
    s=build();alias=tmp_path/'alias';alias.symlink_to(s['b']['store'].root,target_is_directory=True)
    with pytest.raises(ValueError):ReadOnlyDatasetRepository(str(alias),reference=s['request'].snapshot_request.dataset.reference,series_ids=s['request'].snapshot_request.dataset.series_ids)


@pytest.mark.parametrize('fault',['request_size','output_size','closed_store','provider_substitution'])
def test_resource_and_composition_fail_closed(build,monkeypatch,fault):
    s=build();r=s['request'];original_store=s['provider'].store
    if fault=='request_size':r=r.model_copy(update={'limits':r.limits.model_copy(update={'max_request_bytes':1})})
    elif fault=='output_size':r=r.model_copy(update={'limits':r.limits.model_copy(update={'max_output_bytes':1})})
    elif fault=='closed_store':s['repo'].close()
    else:
        def approve(meta,request,stage):
            s['provider'].store=s['b']['store']
            return True
        s['prep']._authorize=approve
    try:
        with traps(monkeypatch):deny(s['prep'].prepare(s['result'].candidate,r))
        assert not s['prep']._issued
    finally:s['provider'].store=original_store


@pytest.mark.parametrize('fault',['lossy','nonfinite','boolean'])
def test_actual_frozen_kernel_rejects_unsupported_numeric_cells(build,monkeypatch,fault):
    from src.data.contracts.dataset import operational_json
    from src.preprocessing.runtime_input_adapters import adapter_for,selection_for
    s=build();snap=s['snapshot'];item=snap.history[3]
    value={'lossy':1.1,'nonfinite':float('inf'),'boolean':True}[fault]
    encoded=historical_value_identity(value) if fault=='lossy' else ['float64','7ff0000000000000'] if fault=='nonfinite' else ['boolean',True]
    replacement=item.model_copy(update={'value':value,'original_value_identity':operational_json(encoded)})
    tampered=snap.model_copy(update={'history':snap.history[:3]+(replacement,)+snap.history[4:]})
    record=s['b']['trainer']._candidates[id(s['result'].candidate)]
    selection,state=selection_for(record[2],record[4])
    with traps(monkeypatch):
        with pytest.raises(ValueError):adapter_for(selection).transform(tampered,state,record[2])


@pytest.mark.parametrize('edge',['cancel','clock'])
def test_dynamic_final_callback_revocation_rejects(build,monkeypatch,edge):
    s=build();counts={'cancel':0,'clock':0};armed={'at':None};ref=s['result'].candidate
    record=s['b']['trainer']._candidates[id(ref)]
    def poll(kind):
        counts[kind]+=1
        if armed['at']==(kind,counts[kind]):s['b']['trainer'].discard(ref)
    def clock():poll('clock');return 0.
    def cancelled():poll('cancel');return False
    s['prep']._clock=clock
    with traps(monkeypatch):
        assert s['prep'].prepare(ref,s['request'],cancelled=cancelled).status=='PREPARED_INPUTS'
        last=counts[edge];counts.update(cancel=0,clock=0);armed['at']=(edge,last)
        deny(s['prep'].prepare(ref,s['request'],cancelled=cancelled))
    s['b']['trainer']._candidates[id(ref)]=record


def test_selection_contract_and_json_are_strict(build):
    from src.data.contracts.executable_inputs import InputPreparationOutcome
    s=build();r=s['request']
    assert ExecutableInputRequest.model_validate_json(r.canonical_json).canonical_bytes==r.canonical_bytes
    for data in (r.canonical_json.replace('"version":"1.0"','"version":"1.0","version":"1.0"',1),b'\xff'):
        with pytest.raises(ValueError):ExecutableInputRequest.model_validate_json(data)
    with pytest.raises(ValueError):InputAdapterSelection(family='gru',representation='tabular',frozen_state_id=r.frozen_state_id,compatibility_id=r.compatibility_id,adapter='canonical_m3_xgboost_inputs_v1')
    with pytest.raises(ValueError):InputPreparationOutcome('EXECUTED','anything')
    with pytest.raises(ValueError):InputPreparationOutcome('PREPARED_INPUTS','INPUT_DATA_ONLY')


@pytest.mark.parametrize('fault',['training_origin','h2','unknown_family','purpose'])
def test_unsupported_geometry_or_purpose_never_reads(build,monkeypatch,fault):
    s=build();r=s['request']
    if fault=='training_origin':r=r.model_copy(update={'snapshot_request':r.snapshot_request.model_copy(update={'forecast_origin':s['b']['at'](5),'capture_cutoff':s['b']['at'](5)})})
    elif fault=='h2':r=r.model_copy(update={'snapshot_request':r.snapshot_request.model_copy(update={'forecast_horizon':2})})
    elif fault=='unknown_family':r=r.model_copy(update={'family':'catboost'})
    else:r=r.model_copy(update={'purpose':'production_inference'})
    with traps(monkeypatch):deny(s['prep'].prepare(s['result'].candidate,r))


def test_imports_do_not_initialize_data_or_native_dependencies():
    import subprocess
    code='''
import sqlite3,pathlib,socket,importlib.abc,sys
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self,name,path=None,target=None):
  if name.split('.')[0] in ('xgboost','torch','tensorflow','lightgbm','mlflow'):raise AssertionError('native import')
def forbidden(*a,**kw):raise AssertionError('operational import-time effect')
sys.meta_path.insert(0,Guard());sqlite3.connect=forbidden;pathlib.Path.mkdir=forbidden;socket.socket.connect=forbidden
import src.data.contracts.executable_inputs
import src.repositories.read_only_dataset_repository
import src.preprocessing.runtime_input_adapters
import src.services.owned_input_preparation_service
print('inert imports')
'''
    p=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True,timeout=20)
    assert p.returncode==0,p.stderr
    assert p.stdout.strip()=='inert imports'
