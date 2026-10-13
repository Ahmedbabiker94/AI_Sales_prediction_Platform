from uuid import UUID
import sqlite3
import pytest
from src.providers.dataset_store_provider import StagingError
from src.data.contracts.dataset import StagingBudgets, ConsistencyEvidence
from src.data.processing.canonical_mapper import CanonicalMapper
from src.repositories.local_dataset_repository import LocalDatasetRepository
from src.services.dataset_ingestion_service import DatasetIngestionService
from test_dataset_ingestion import ref, ControlledAdapter, states, setup


def begin(store,asset):
    return store._begin(dataset_id=UUID(int=9),asset=asset,mapping=ref.config(),budgets=StagingBudgets(),consistency=ConsistencyEvidence(),owner=DatasetIngestionService(store,authorize_ingestion=lambda *_:True))


def test_cross_batch_source_duplicate_and_release(setup):
    root,store,asset,_=setup;a=begin(store,asset);mapper=CanonicalMapper(ref.config())
    try:
        first=ref.batch(ref.raw().iloc[:1],asset,ids=('same',));store._append(a,first,mapper.map_batch(first))
        second=ref.batch(ref.raw().iloc[1:2],asset,ids=('same',),batch_id='1')
        with pytest.raises(StagingError,match='duplicate'):store._append(a,second,mapper.map_batch(second))
        store._abort(a)
    finally:store._release(a)
    assert states(root)==['aborted']


def test_exclusive_writer_and_incomplete_recovery(setup):
    root,store,asset,_=setup;a=begin(store,asset)
    with pytest.raises(BlockingIOError):begin(LocalDatasetRepository(root),asset)
    store._release(a) # equivalent persistent STAGING state left by a dead owner
    other=LocalDatasetRepository(root);b=begin(other,asset)
    assert states(root)==['aborted','staging']
    other._abort(b);other._release(b)


@pytest.mark.parametrize('corruption', ['checksum','count','write'])
def test_corrupt_chunks_and_write_failure_prevent_seal(setup,corruption,monkeypatch):
    root,store,asset,service=setup
    if corruption=='write':
        import src.repositories.local_dataset_repository as module
        original=module.os.fsync
        monkeypatch.setattr(module.os,'fsync',lambda *_:(_ for _ in ()).throw(OSError('disk full')))
        with pytest.raises(StagingError):service.ingest(ControlledAdapter(asset),ref.config(),dataset_id=UUID(int=9))
    else:
        original=store._seal
        observed=[]
        def corrupt(a, **kwargs):
            if corruption=='checksum':
                p=root/str(a.id)/'0-raw.parquet';p.chmod(0o600);payload=p.read_bytes();p.write_bytes(b'X'+payload[1:])
            else:
                with sqlite3.connect(root/'catalog.sqlite') as conn:conn.execute('UPDATE chunks SET rows=999 WHERE ordinal=0')
            try:
                return original(a, **kwargs)
            except StagingError as error:
                observed.append(str(error));raise
        monkeypatch.setattr(store,'_seal',corrupt)
        with pytest.raises(StagingError):service.ingest(ControlledAdapter(asset),ref.config(),dataset_id=UUID(int=9))
        assert observed==['staged chunk checksum mismatch' if corruption=='checksum' else 'staged row count mismatch']
    assert states(root)==['aborted']


def test_invalid_transitions(setup):
    _,store,asset,_=setup;a=begin(store,asset);store._abort(a)
    with pytest.raises(StagingError):store._seal(a)
    store._release(a)


def test_real_process_crash_recovery(tmp_path):
    import subprocess,sys,os
    root=tmp_path/'crashed'
    script='''
import runpy,os
from uuid import UUID
from src.repositories.local_dataset_repository import LocalDatasetRepository
from src.services.dataset_ingestion_service import DatasetIngestionService
from src.data.contracts.dataset import StagingBudgets,ConsistencyEvidence
from src.data.processing.canonical_mapper import CanonicalMapper
from src.services.dataset_ingestion_service import DatasetIngestionService
m=runpy.run_path('tests/data/adapters/test_integration_foundation.py')
a=m['asset'].__wrapped__();store=LocalDatasetRepository(ROOT)
attempt=store._begin(dataset_id=UUID(int=9),asset=a,mapping=m['config'](),budgets=StagingBudgets(),consistency=ConsistencyEvidence(),owner=DatasetIngestionService(store,authorize_ingestion=lambda *_:True))
b=m['batch'](m['raw']().iloc[:1],a,ids=('one',))
store._append(attempt,b,CanonicalMapper(m['config']()).map_batch(b))
os._exit(7)
'''.replace('ROOT',repr(str(root)))
    result=subprocess.run([sys.executable,'-c',script],env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},timeout=10)
    assert result.returncode==7 and states(root)==['staging']
    store=LocalDatasetRepository(root);attempt=begin(store,ref.asset.__wrapped__())
    assert states(root)==['aborted','staging']
    store._abort(attempt);store._release(attempt)


def test_repeated_sealing_and_unknown_state_reject(setup):
    root,store,asset,service=setup
    manifest=service.ingest(ControlledAdapter(asset),ref.config(),dataset_id=UUID(int=9))
    # No public sealing API; a released writer cannot transition again.
    attempt=begin(store,asset);store._abort(attempt);store._release(attempt)
    with pytest.raises(StagingError):store._seal(attempt)
    with sqlite3.connect(root/'catalog.sqlite') as conn:conn.execute("UPDATE attempts SET state='unknown' WHERE id=?",(str(attempt.id),))
    with pytest.raises(StagingError):begin(store,asset)
    assert manifest.version_id is not None


def test_sealed_reader_copy_does_not_alias_payload(setup):
    from src.repositories.local_dataset_repository import _frame
    import pyarrow.parquet as pq
    root,_,asset,service=setup
    manifest=service.ingest(ControlledAdapter(asset),ref.config(),dataset_id=UUID(int=9))
    path=root/str(manifest.attempt_id)/'0-canonical.parquet'
    first=_frame(pq.read_table(path));first.iloc[0,first.columns.get_loc('value')]=999
    assert _frame(pq.read_table(path)).value.iloc[0]==-12.5


@pytest.mark.parametrize('assertion', [None,True,False,'complete'])
def test_repository_cannot_seal_on_caller_completion_assertion(setup,assertion):
    root,store,asset,_=setup;a=begin(store,asset)
    raw=ref.batch(ref.raw().iloc[:1],asset,ids=('one',))
    store._append(a,raw,CanonicalMapper(ref.config()).map_batch(raw))
    with pytest.raises(StagingError):store._seal(a,owner=assertion)
    assert states(root)==['staging']
    store._abort(a);store._release(a)


def test_completion_factory_removed_and_owner_cannot_complete_partial(setup):
    import src.providers.dataset_store_provider as boundary
    assert not hasattr(boundary,'_completion_after_cleanup')
    assert not hasattr(boundary,'_ExtractionCompletion')
    assert not hasattr(boundary,'_COMPLETION_KEY')
    root,store,asset,_=setup;attempt=begin(store,asset)
    raw=ref.batch(ref.raw().iloc[:1],asset,ids=('one',))
    store._append(attempt,raw,CanonicalMapper(ref.config()).map_batch(raw))
    with pytest.raises(StagingError,match='lifecycle has not completed'):
        store._seal(attempt,owner=attempt.owner)
    assert states(root)==['staging']
    store._abort(attempt);store._release(attempt)


def test_leaked_owner_cannot_rebind_or_replay(setup):
    root,store,asset,_=setup;first=begin(store,asset);leaked_owner=first.owner
    store._abort(first);store._release(first);second=begin(store,asset)
    raw=ref.batch(ref.raw().iloc[:1],asset,ids=('one',))
    store._append(second,raw,CanonicalMapper(ref.config()).map_batch(raw))
    for attempt,owner in [(second,leaked_owner),(first,leaked_owner),(second,second.owner)]:
        with pytest.raises(StagingError):store._seal(attempt,owner=owner)
    assert states(root)==['aborted','staging']
    store._abort(second);store._release(second)


def test_valid_completion_claim_is_single_use(setup,monkeypatch):
    root,store,asset,service=setup;original=store._seal;failures=[]
    def twice(attempt,**kwargs):
        result=original(attempt,**kwargs)
        with pytest.raises(StagingError) as error:original(attempt,**kwargs)
        failures.append(str(error.value));return result
    monkeypatch.setattr(store,'_seal',twice)
    assert service.ingest(ControlledAdapter(asset),ref.config(),dataset_id=UUID(int=9)).state.value=='sealed'
    assert failures==['invalid staging state transition']


@pytest.mark.parametrize('damage', ['malformed','missing','organization','source','ingestion','mapping','lineage','attempt','record_ids'])
def test_semantically_invalid_envelope_rejects_even_with_matching_checksums(setup,damage,monkeypatch):
    import json,hashlib
    import pyarrow as pa
    import pyarrow.parquet as pq
    root,store,asset,service=setup;original=store._seal;observed=[]
    def damage_envelope(attempt,**kwargs):
        with sqlite3.connect(root/'catalog.sqlite') as conn:
            envelope=json.loads(conn.execute('SELECT envelope FROM chunks WHERE ordinal=0 AND attempt=?',(str(attempt.id),)).fetchone()[0])
            if damage=='missing':del envelope['raw_asset']
            if damage=='organization':envelope['raw_asset']['organization_id']=str(UUID(int=99))
            if damage=='source':envelope['canonical_dataset']['lineage'][0]['source_id']=str(UUID(int=99))
            if damage=='ingestion':envelope['canonical_dataset']['lineage'][0]['ingestion_batch_id']=str(UUID(int=99))
            if damage=='mapping':envelope['canonical_dataset']['asset']['metadata']['mapping_version']='2.0'
            if damage=='lineage':envelope['canonical_dataset']['lineage']=[]
            if damage=='attempt':envelope['attempt_id']=str(UUID(int=99))
            if damage=='record_ids':envelope['record_ids']=['stale-source-id']
            encoded='not-json' if damage=='malformed' else json.dumps(envelope)
            conn.execute('UPDATE chunks SET envelope=? WHERE ordinal=0 AND attempt=?',(encoded,str(attempt.id)))
            # Recompute physical checksums to exercise semantic validation independently.
            for kind in ('raw','canonical'):
                path=root/str(attempt.id)/f'0-{kind}.parquet'
                table=pq.read_table(path).replace_schema_metadata({**pq.read_schema(path).metadata,
                    b'envelope_sha256':hashlib.sha256(encoded.encode()).hexdigest().encode()})
                sink=pa.BufferOutputStream();pq.write_table(table,sink,compression='NONE');payload=sink.getvalue().to_pybytes()
                path.chmod(0o600);path.write_bytes(payload);path.chmod(0o400)
                # SQL column names are fixed test constants, never source input.
                conn.execute(f'UPDATE chunks SET {kind}_hash=?,{kind}_bytes=? WHERE ordinal=0 AND attempt=?',
                    (hashlib.sha256(payload).hexdigest(),len(payload),str(attempt.id)))
        try:return original(attempt,**kwargs)
        except StagingError as error:observed.append(str(error));raise
    monkeypatch.setattr(store,'_seal',damage_envelope)
    with pytest.raises(StagingError):service.ingest(ControlledAdapter(asset),ref.config(),dataset_id=UUID(int=9))
    assert observed==['staged lineage envelope validation failed']
    assert states(root)==['aborted']


def test_changed_envelope_without_payload_binding_rejects(setup,monkeypatch):
    root,store,asset,service=setup;original=store._seal;seen=[]
    def corrupt(attempt,**kwargs):
        with sqlite3.connect(root/'catalog.sqlite') as conn:conn.execute("UPDATE chunks SET envelope='not-json' WHERE ordinal=0")
        try:return original(attempt,**kwargs)
        except StagingError as error:seen.append(str(error));raise
    monkeypatch.setattr(store,'_seal',corrupt)
    with pytest.raises(StagingError):service.ingest(ControlledAdapter(asset),ref.config(),dataset_id=UUID(int=9))
    assert seen==['staged lineage envelope checksum mismatch']
