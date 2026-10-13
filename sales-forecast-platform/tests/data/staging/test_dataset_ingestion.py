"""Synthetic adapter-contract integration; no live DB/API or model operations."""
from dataclasses import replace, FrozenInstanceError
from decimal import Decimal
import importlib.util
from pathlib import Path
import sqlite3
from uuid import UUID

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.data.adapters.base import SourceType
from src.data.contracts.dataset import ConsistencyEvidence, StagingBudgets, StagingState
from src.providers.dataset_store_provider import StagingError
from src.repositories.local_dataset_repository import LocalDatasetRepository, _frame
from src.services.dataset_ingestion_service import DatasetIngestionService

spec=importlib.util.spec_from_file_location('staging_reference', Path('tests/data/adapters/test_integration_foundation.py'))
ref=importlib.util.module_from_spec(spec); spec.loader.exec_module(ref)


class ControlledAdapter(ref.MemoryAdapter):
    def __init__(self, asset, *, kind=SourceType.JSON, fail=None, frame=None):
        super().__init__(ref.raw() if frame is None else frame, asset, kind)
        self.asset=asset;self.fail=fail;self.exhausted=False;self.iterator_closed=False
    def extract(self, *, batch_size=10000):
        try:
            for i in range(len(self.frame)):
                if i==1:
                    if self.fail=='late': raise RuntimeError('SYNTHETIC_SECRET')
                    if self.fail=='cancel': raise KeyboardInterrupt()
                yield ref.batch(self.frame.iloc[i:i+1],self.asset,ids=(str(i),),batch_id=str(i))
            self.exhausted=True
        finally:self.iterator_closed=True
    def close(self):
        self.closed=True
        if self.fail=='cleanup':raise RuntimeError('SYNTHETIC_SECRET')


@pytest.fixture
def setup(tmp_path):
    root=tmp_path/'store';store=LocalDatasetRepository(root)
    asset=ref.asset.__wrapped__()
    service=DatasetIngestionService(store,authorize_ingestion=lambda *_:True)
    return root,store,asset,service


def states(root):
    with sqlite3.connect(root/'catalog.sqlite') as c:
        return [r[0] for r in c.execute('SELECT state FROM attempts')]


@pytest.mark.parametrize('kind', list(SourceType))
def test_all_seven_contract_kinds_seal_without_admission(setup,kind):
    root,store,asset,service=setup;a=ControlledAdapter(asset,kind=kind)
    manifest=service.ingest(a,ref.config(),dataset_id=UUID(int=9),batch_size=1)
    assert manifest.state is StagingState.SEALED and manifest.rows==3 and manifest.batches==3
    assert len(manifest.version_id)==64 and states(root)==['sealed']
    assert a.exhausted and a.iterator_closed and a.closed
    assert not hasattr(manifest,'admitted') and not hasattr(store,'read_dataset')
    with pytest.raises(FrozenInstanceError):manifest.rows=1
    for c in manifest.chunks:
        p=root/str(manifest.attempt_id)/f'{c[0]}-canonical.parquet'
        assert p.stat().st_mode & 0o222==0
    values=[_frame(pq.read_table(root/str(manifest.attempt_id)/f'{i}-canonical.parquet')).value.iloc[0] for i in range(3)]
    assert values==[-12.5,0,42]


@pytest.mark.parametrize('fail', ['late','cleanup','cancel'])
def test_partial_then_failure_never_seals(setup,fail):
    root,_,asset,service=setup;a=ControlledAdapter(asset,fail=fail)
    with pytest.raises(KeyboardInterrupt if fail=='cancel' else StagingError) as exc:
        service.ingest(a,ref.config(),dataset_id=UUID(int=9))
    assert 'SYNTHETIC_SECRET' not in str(exc.value)
    assert states(root)==['aborted'] and a.closed and a.iterator_closed


@pytest.mark.parametrize('bad', ['mapping','unicode','nested','scope','duplicate'])
def test_bad_content_aborts(setup,bad):
    root,_,asset,service=setup;frame=ref.raw()
    if bad=='mapping':frame.loc[frame.index[1],'to']='not-a-date'
    if bad=='unicode':frame['offer']=frame.offer.astype(object);frame.loc[frame.index[1],'offer']='\ud800'
    if bad=='nested':frame['offer']=frame.offer.astype(object);frame.at[frame.index[1],'offer']=[1,2]
    if bad=='duplicate':frame.loc[frame.index[1],['from','to']]=frame.iloc[0][['from','to']].values
    if bad=='scope':asset=asset.model_copy(update={'organization_id':UUID(int=88)})
    a=ControlledAdapter(asset,frame=frame)
    with pytest.raises(StagingError):service.ingest(a,ref.config(),dataset_id=UUID(int=9))
    assert a.closed and (not (root/'catalog.sqlite').exists() if bad=='scope' else states(root)==['aborted'])


@pytest.mark.parametrize('budgets', [StagingBudgets(max_rows=1),StagingBudgets(max_batches=1),
    StagingBudgets(max_unique_keys=2),StagingBudgets(max_chunk_bytes=64),
    StagingBudgets(max_raw_bytes=64),StagingBudgets(max_canonical_bytes=64),
    StagingBudgets(max_metadata_bytes=64),StagingBudgets(max_columns=2),StagingBudgets(max_cell_bytes=5)])
def test_quotas_fail_closed(setup,budgets):
    root,_,asset,service=setup;a=ControlledAdapter(asset)
    with pytest.raises(StagingError):service.ingest(a,ref.config(),dataset_id=UUID(int=9),budgets=budgets)
    assert a.closed
    if (root/'catalog.sqlite').exists():assert 'sealed' not in states(root)


def test_decimal_raw_and_canonical_exact_roundtrip(setup):
    root,_,asset,service=setup;frame=ref.raw()
    frame['reported']=pd.Series(pa.array([Decimal('-12345678901234567890.1234'),Decimal('-0.0000'),Decimal('12.0000')],type=pa.decimal128(25,4)),index=frame.index,dtype=pd.ArrowDtype(pa.decimal128(25,4)))
    expected=frame.reported.tolist()
    manifest=service.ingest(ControlledAdapter(asset,frame=frame),ref.config(),dataset_id=UUID(int=9))
    for i,value in enumerate(expected):
        for kind,column in [('raw','reported'),('canonical','value')]:
            actual=_frame(pq.read_table(root/str(manifest.attempt_id)/f'{i}-{kind}.parquet'))[column].iloc[0]
            assert actual.as_tuple()==value.as_tuple()


def test_consistency_requires_trusted_verification(setup):
    _,store,asset,service=setup;e=ConsistencyEvidence(kind='transaction_scoped',reference='trusted-session')
    with pytest.raises(StagingError):service.ingest(ControlledAdapter(asset),ref.config(),dataset_id=UUID(int=9),consistency=e)
    service=DatasetIngestionService(store,authorize_ingestion=lambda *_:True,verify_consistency=lambda *_:True)
    assert service.ingest(ControlledAdapter(asset),ref.config(),dataset_id=UUID(int=9),consistency=e).consistency==e


def test_cancellation_deadline_and_denial(setup):
    root,store,asset,service=setup
    with pytest.raises(StagingError):service.ingest(ControlledAdapter(asset),ref.config(),dataset_id=UUID(int=9),cancelled=lambda:True)
    clock=iter([0,0,0,301])
    service=DatasetIngestionService(store,authorize_ingestion=lambda *_:True,clock=lambda:next(clock))
    with pytest.raises(StagingError):service.ingest(ControlledAdapter(asset),ref.config(),dataset_id=UUID(int=9))
    if (root/'catalog.sqlite').exists():assert 'sealed' not in states(root)


def test_real_file_connectors(tmp_path):
    from src.data.adapters.csv_adapter import CSVAdapter, CSVConfiguration
    from src.data.adapters.excel_adapter import ExcelAdapter, ExcelConfiguration
    from src.data.adapters.json_adapter import JSONAdapter, JSONConfiguration
    from src.data.adapters.parquet_adapter import ParquetAdapter, ParquetConfiguration
    frame=ref.raw().reset_index(drop=True);asset=ref.asset.__wrapped__()
    for kind in ('csv','xlsx','json','parquet'):
        path=tmp_path/f'input.{kind}'
        if kind=='csv':
            frame.to_csv(path,index=False);adapter=CSVAdapter(CSVConfiguration(path=str(path),column_types=(('reported','float'),('offer','float'))),asset)
        elif kind=='xlsx':
            frame.to_excel(path,index=False);adapter=ExcelAdapter(ExcelConfiguration(path=str(path)),asset)
        elif kind=='json':
            path.write_text(frame.to_json(orient='records'));adapter=JSONAdapter(JSONConfiguration(path=str(path)),asset)
        else:
            frame.to_parquet(path,index=False);adapter=ParquetAdapter(ParquetConfiguration(path=str(path)),asset)
        store=LocalDatasetRepository(tmp_path/f'store-{kind}')
        result=DatasetIngestionService(store,authorize_ingestion=lambda *_:True).ingest(adapter,ref.config(),dataset_id=UUID(int=9),batch_size=1)
        assert result.rows==3 and result.state is StagingState.SEALED and adapter.closed


def test_actual_network_adapter_classes_with_controlled_doubles(tmp_path):
    def load(name):
        spec=importlib.util.spec_from_file_location('staging_'+name,Path('tests/data/adapters')/('test_'+name+'.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
    network=load('network_adapters');odata=load('odata_adapter')
    asset=network.asset.__wrapped__()
    pg,connection,_=network.pg_adapter(asset)
    rest,transport=network.rest_adapter(asset,[network.response(network.ROWS[:2],more=True),network.response(network.ROWS[2:])])
    oa,ot,_=odata.adapter(odata.asset.__wrapped__(),[odata.response(odata.METADATA,metadata=True),odata.page()])
    for name,adapter,mapping,rows in [('pg',pg,network.mapping(),3),('rest',rest,network.mapping(),3),('odata',oa,odata.mapping(),2)]:
        result=DatasetIngestionService(LocalDatasetRepository(tmp_path/name),authorize_ingestion=lambda *_:True).ingest(adapter,mapping,dataset_id=UUID(int=9),batch_size=1)
        assert result.rows==rows and adapter.closed
    assert connection.closed and connection.rollbacks==1 and transport.closed and ot.closed


def test_source_and_mapping_drift(setup):
    root,_,asset,service=setup
    class Drifting(ControlledAdapter):
        def extract(self,**kwargs):
            for i,batch in enumerate(super().extract(**kwargs)):
                if i==1: batch.asset.source_id=UUID(int=989)
                yield batch
    with pytest.raises(StagingError):service.ingest(Drifting(asset),ref.config(),dataset_id=UUID(int=9))
    assert states(root)==['aborted']


def test_authorizer_secrets_and_revocation(setup):
    root,store,asset,_=setup
    def denied(*_):raise RuntimeError('AUTH_PRIVATE_SECRET')
    adapter=ControlledAdapter(asset)
    with pytest.raises(StagingError) as failure:
        DatasetIngestionService(store,authorize_ingestion=denied).ingest(adapter,ref.config(),dataset_id=UUID(int=9))
    import traceback
    assert 'AUTH_PRIVATE_SECRET' not in ''.join(traceback.format_exception(failure.value))
    assert adapter.closed
    calls=[]
    def revoke(*_):calls.append(1);return len(calls)<3
    with pytest.raises(StagingError):DatasetIngestionService(store,authorize_ingestion=revoke).ingest(ControlledAdapter(asset),ref.config(),dataset_id=UUID(int=9))
    assert states(root)==['aborted']


def test_context_cannot_suppress_incomplete_extraction(setup):
    root,_,asset,service=setup
    class Suppressing(ControlledAdapter):
        def __exit__(self,*args):self.close();return True
    with pytest.raises(StagingError):service.ingest(Suppressing(asset,fail='late'),ref.config(),dataset_id=UUID(int=9))
    assert states(root)==['aborted']


def test_mapping_drift_and_unknown_catalog_state(setup):
    root,store,asset,service=setup;mapping=ref.config()
    class Drifting(ControlledAdapter):
        def extract(self,**kwargs):
            for i,b in enumerate(super().extract(**kwargs)):
                if i==1:object.__setattr__(mapping,'version','2.0')
                yield b
    with pytest.raises(StagingError):service.ingest(Drifting(asset),mapping,dataset_id=UUID(int=9))
    assert states(root)==['aborted']


@pytest.mark.parametrize('location', ['identity','record_id','metadata','mapping'])
def test_unicode_at_all_boundaries(setup,location):
    root,_,asset,service=setup;frame=ref.raw();mapping=ref.config()
    if location=='identity':frame.loc[frame.index[1],'branch']='\ud800'
    if location=='metadata':asset.metadata={'nested':{'text':'\ud800'}}
    if location=='mapping':object.__setattr__(mapping,'sign_semantics','signed\ud800')
    class BadID(ControlledAdapter):
        def extract(self,**kwargs):
            for i,b in enumerate(super().extract(**kwargs)):
                if location=='record_id' and i==1:
                    b=ref.batch(b.records,asset,ids=('\ud800',),batch_id='1')
                yield b
    with pytest.raises(StagingError):service.ingest(BadID(asset,frame=frame),mapping,dataset_id=UUID(int=9))
    if (root/'catalog.sqlite').exists():assert 'sealed' not in states(root)


def test_metadata_depth_and_index_budget(setup):
    root,_,asset,service=setup;asset.metadata={'x':{'x':{'x':{'x':1}}}}
    with pytest.raises(StagingError):service.ingest(ControlledAdapter(asset),ref.config(),dataset_id=UUID(int=9),budgets=StagingBudgets(max_nesting_depth=2))
    with pytest.raises(StagingError):service.ingest(ControlledAdapter(asset),ref.config(),dataset_id=UUID(int=9),budgets=StagingBudgets(max_index_bytes=4096))
    if (root/'catalog.sqlite').exists():assert 'sealed' not in states(root)


def test_cancellation_during_seal_prevents_commit(setup,monkeypatch):
    root,store,asset,service=setup;stop=[];original=store._seal
    def seal(a,*,checkpoint,owner):
        stop.append(True);return original(a,checkpoint=checkpoint,owner=owner)
    monkeypatch.setattr(store,'_seal',seal)
    with pytest.raises(StagingError):service.ingest(ControlledAdapter(asset),ref.config(),dataset_id=UUID(int=9),cancelled=lambda:bool(stop))
    assert states(root)==['aborted']


def test_empty_extraction_never_seals(setup):
    root,_,asset,service=setup
    with pytest.raises(StagingError):service.ingest(ControlledAdapter(asset,frame=ref.raw().iloc[:0]),ref.config(),dataset_id=UUID(int=9))
    assert states(root)==['aborted']


@pytest.mark.parametrize('storage', ['python','pyarrow'])
def test_string_backend_fidelity_against_original(setup,storage):
    root,_,asset,service=setup;frame=ref.raw()
    frame['offer']=pd.Series(['α','β','γ'],index=frame.index,dtype=pd.StringDtype(storage=storage))
    manifest=service.ingest(ControlledAdapter(asset,frame=frame),ref.config(),dataset_id=UUID(int=9))
    for i in range(3):
        raw=_frame(pq.read_table(root/str(manifest.attempt_id)/f'{i}-raw.parquet'))
        canonical=_frame(pq.read_table(root/str(manifest.attempt_id)/f'{i}-canonical.parquet'))
        pd.testing.assert_frame_equal(frame.iloc[i:i+1],raw,check_exact=True)
        assert raw.offer.dtype.storage==canonical.promotion.dtype.storage==storage


@pytest.mark.parametrize('timestamp', [False,True])
def test_named_fixed_offset_timezone_fidelity(setup,timestamp):
    from datetime import datetime,timezone,timedelta
    root,_,asset,service=setup;frame=ref.raw();tz=timezone(timedelta(hours=3),'BusinessTZ')
    value=datetime(2026,1,1,12,34,56,123456,tzinfo=tz)
    if timestamp:value=pd.Timestamp(value)
    frame['offer']=pd.Series([value]*3,index=frame.index,dtype=object)
    manifest=service.ingest(ControlledAdapter(asset,frame=frame),ref.config(),dataset_id=UUID(int=9))
    raw=_frame(pq.read_table(root/str(manifest.attempt_id)/'0-raw.parquet'))
    assert raw.offer.iloc[0].tzname()=='BusinessTZ'
    assert type(raw.offer.iloc[0]) is type(value) and raw.offer.iloc[0]==value


def test_scalar_fidelity_and_original_comparison():
    from src.repositories.local_dataset_repository import _table,_frame,_verify_original
    from datetime import datetime,timezone
    import struct
    from decimal import localcontext
    from zoneinfo import ZoneInfo
    values=[Decimal('-0.0000'),Decimal('123456789012345678901234567890.12345678'),
            -0.0,struct.unpack('!d',bytes.fromhex('7ff8000000000017'))[0],None,pd.NA,pd.NaT,
            pd.Timestamp('2026-01-01T12:34:56.123456789',tz=ZoneInfo('Europe/Berlin')),
            datetime(2026,1,1,tzinfo=timezone.utc)]
    frame=pd.DataFrame({'v':pd.Series(values,dtype=object)})
    with localcontext() as context:
        context.prec=3
        restored=_frame(_table(frame,StagingBudgets()))
    _verify_original(frame,restored,StagingBudgets())
    assert [v.as_tuple() for v in restored.v.iloc[:2]]==[v.as_tuple() for v in values[:2]]
    assert struct.pack('!d',restored.v.iloc[3])==bytes.fromhex('7ff8000000000017')
    assert restored.v.iloc[4] is None and restored.v.iloc[5] is pd.NA and restored.v.iloc[6] is pd.NaT
    broken=restored.copy();broken.at[0,'v']=Decimal('0.0000')
    with pytest.raises(StagingError):_verify_original(frame,broken,StagingBudgets())


def test_unsupported_datetime_timezone_rejects_before_write(setup):
    from datetime import datetime,tzinfo,timedelta
    class CustomTZ(tzinfo):
        def utcoffset(self,dt):return timedelta(hours=1)
        def dst(self,dt):return timedelta(0)
    root,_,asset,service=setup;frame=ref.raw()
    frame['offer']=pd.Series([datetime(2026,1,1,tzinfo=CustomTZ())]*3,index=frame.index,dtype=object)
    with pytest.raises(StagingError):service.ingest(ControlledAdapter(asset,frame=frame),ref.config(),dataset_id=UUID(int=9))
    assert states(root)==['aborted'] and not list(root.glob('*/*.parquet'))


@pytest.mark.parametrize('metadata', [
    {'endpoint':'https://example.test/data?access_token=PROVENANCE_PRIVATE'},
    {'Authorization':'Bearer PROVENANCE_PRIVATE'},
    {'source_reference':'Bearer PROVENANCE_PRIVATE'},
    {'csv_column_types':[['amount',{'Authorization':'Bearer PROVENANCE_PRIVATE'}]]},
    {'source_reference':'https://example.test/data?$skiptoken=PROVENANCE_PRIVATE'},
    {'source_reference':{'secret':'PROVENANCE_PRIVATE'}},
])
def test_sensitive_provenance_rejects_safely(setup,metadata):
    import traceback
    root,_,asset,service=setup;asset.metadata=metadata
    with pytest.raises(StagingError) as failure:service.ingest(ControlledAdapter(asset),ref.config(),dataset_id=UUID(int=9))
    assert 'PROVENANCE_PRIVATE' not in ''.join(traceback.format_exception(failure.value))
    assert states(root)==['aborted']


def test_safe_provenance_and_private_raw_urls_are_distinct(setup):
    import json
    root,_,asset,service=setup;asset.metadata={'source_reference':'opaque-approved-connection'}
    frame=ref.raw();frame['offer']=pd.Series(['https://business.example.test/item?access_token=RAW_VALUE']*3,index=frame.index,dtype=object)
    manifest=service.ingest(ControlledAdapter(asset,frame=frame),ref.config(),dataset_id=UUID(int=9))
    assert 'RAW_VALUE' not in ''.join(manifest.envelopes)
    assert json.loads(manifest.envelopes[0])['raw_asset']['metadata']==asset.metadata
    restored=_frame(pq.read_table(root/str(manifest.attempt_id)/'0-raw.parquet'))
    assert restored.offer.iloc[0]==frame.offer.iloc[0]


@pytest.mark.parametrize('storage', ['python','pyarrow'])
@pytest.mark.parametrize('missing', [pd.NA,float('nan')])
def test_string_missing_value_parameter_is_preserved(storage,missing):
    from src.repositories.local_dataset_repository import _table,_frame,_verify_original
    original=pd.DataFrame({'v':pd.Series(['é',None],dtype=pd.StringDtype(storage=storage,na_value=missing))})
    restored=_frame(_table(original,StagingBudgets()))
    _verify_original(original,restored,StagingBudgets())
    assert original.v.dtype==restored.v.dtype


def test_timezone_dtype_and_datetime_index_parameters():
    from src.repositories.local_dataset_repository import _table,_frame,_verify_original
    from datetime import timezone,timedelta
    from zoneinfo import ZoneInfo
    for tz in (timezone.utc,timezone(timedelta(hours=3),'BusinessTZ'),ZoneInfo('Europe/Berlin'),'America/New_York'):
        index=pd.date_range('2026-01-01',periods=2,tz=tz,freq='D',name='time')
        original=pd.DataFrame({'v':index},index=index)
        restored=_frame(_table(original,StagingBudgets()))
        _verify_original(original,restored,StagingBudgets())
        assert restored.index.freqstr==original.index.freqstr


def test_clock_failure_is_sanitized_and_closes_source(setup):
    import traceback
    _,store,asset,_=setup;adapter=ControlledAdapter(asset)
    def broken():raise RuntimeError('CLOCK_PRIVATE_SECRET')
    with pytest.raises(StagingError) as error:DatasetIngestionService(store,authorize_ingestion=lambda *_:True,clock=broken).ingest(adapter,ref.config(),dataset_id=UUID(int=9))
    assert adapter.closed and 'CLOCK_PRIVATE_SECRET' not in ''.join(traceback.format_exception(error.value))


def test_native_windows_is_explicitly_unsupported(tmp_path,monkeypatch):
    import src.repositories.local_dataset_repository as repository
    monkeypatch.setattr(repository,'fcntl',None)
    with pytest.raises(StagingError,match='Linux/WSL'):repository.LocalDatasetRepository(tmp_path/'unsupported')


@pytest.mark.parametrize('bits,dtype', [('7f800017','float32'),('7ff0000000000017','float64')])
def test_numpy_float_signalling_nan_bits_are_preserved(bits,dtype):
    import numpy as np
    from src.repositories.local_dataset_repository import _table,_frame,_verify_original
    width=4 if dtype=='float32' else 8
    data=bytes.fromhex(bits)
    values=np.frombuffer(data,dtype='>f'+str(width)).astype(dtype)
    original=pd.DataFrame({'v':values})
    restored=_frame(_table(original,StagingBudgets()))
    _verify_original(original,restored,StagingBudgets())
    assert restored.v.to_numpy().tobytes()==original.v.to_numpy().tobytes()
