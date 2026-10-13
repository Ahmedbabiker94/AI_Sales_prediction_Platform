from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import sqlite3
import pytest
from src.providers.canonical_training_data_provider import CanonicalTrainingDataProvider
from src.providers.dataset_store_provider import StagingError
from test_dataset_finalization import candidate,finalize,NOW,state


def reader(c,**kwargs):
    return CanonicalTrainingDataProvider(c[1],authorize_read=lambda *_:True,clock=lambda:NOW,**kwargs)


def test_read_preserves_values_and_isolation(candidate):
    finalize(candidate);provider=reader(candidate)
    stream=provider.read_batches(candidate[3],purpose='production_training')
    batches=list(stream);assert [v.value.iloc[0] for v in batches]==[-12.5,0.,42.]
    batches[0].loc[batches[0].index[0],'value']=999
    assert list(provider.read_batches(candidate[3],purpose='production_training'))[0].value.iloc[0]==-12.5


@pytest.mark.parametrize('mode',['sealed','rejected','cross_org','purpose','revoked','expired','admission','late_chunk'])
def test_failures_before_first_visible_batch(candidate,mode):
    if mode=='rejected':
        with pytest.raises(Exception):finalize(candidate,consistency_evidence=None)
    elif mode!='sealed':finalize(candidate)
    root=candidate[0];ref=candidate[3];provider=reader(candidate);purpose='production_training'
    if mode=='cross_org':ref=replace(ref,organization_id=__import__('uuid').UUID(int=991))
    if mode=='purpose':purpose='isolated_diagnostics'
    if mode=='revoked':provider.authorize_read=lambda *_:False
    if mode=='expired':provider.clock=lambda:NOW+timedelta(days=2)
    if mode=='admission':
        with sqlite3.connect(root/'catalog.sqlite') as conn:conn.execute("UPDATE publications SET admission='{}'")
    if mode=='late_chunk':
        path=root/str(ref.attempt_id)/'2-canonical.parquet';path.chmod(0o600);path.write_bytes(b'corrupt')
    stream=provider.read_batches(ref,purpose=purpose)
    with pytest.raises(StagingError):next(stream)


def test_original_mutation_after_first_batch_cannot_change_read_snapshot(candidate):
    finalize(candidate);provider=reader(candidate);stream=provider.read_batches(candidate[3],purpose='production_training')
    first=next(stream);assert first.value.iloc[0]==-12.5
    path=candidate[0]/str(candidate[3].attempt_id)/'2-canonical.parquet';path.chmod(0o600);path.write_bytes(b'corrupt')
    assert [v.value.iloc[0] for v in stream]==[0.,42.]
    with pytest.raises(StagingError):next(provider.read_batches(candidate[3],purpose='production_training'))


def test_revocation_on_later_access_closes_snapshot(candidate):
    finalize(candidate);provider=reader(candidate);stream=provider.read_batches(candidate[3],purpose='production_training')
    next(stream);provider.authorize_read=lambda *_:False
    with pytest.raises(StagingError):next(stream)
    # Read snapshot cleanup leaves exactly the original capture directory.
    assert len([p for p in candidate[0].iterdir() if p.is_dir()])==1


@pytest.mark.parametrize('error_type',[RuntimeError,StagingError])
def test_read_callback_exceptions_are_sanitized(candidate,error_type):
    import traceback
    finalize(candidate);provider=reader(candidate)
    def denied(*_):raise error_type('READ_PRIVATE_SECRET')
    provider.authorize_read=denied
    with pytest.raises(StagingError) as exc:next(provider.read_batches(candidate[3],purpose='production_training'))
    assert 'READ_PRIVATE_SECRET' not in ''.join(traceback.format_exception(exc.value))


def test_read_is_chunked_and_does_not_concatenate(candidate,monkeypatch):
    import pandas as pd
    finalize(candidate)
    monkeypatch.setattr(pd,'concat',lambda *_a,**_kw:(_ for _ in ()).throw(AssertionError('full materialization')))
    assert [len(v) for v in reader(candidate).read_batches(candidate[3],purpose='production_training')]==[1,1,1]


def test_reader_cancellation_releases_snapshot_and_lock(candidate):
    finalize(candidate);provider=reader(candidate);stream=provider.read_batches(candidate[3],purpose='production_training')
    next(stream);stream.close()
    assert len([p for p in candidate[0].iterdir() if p.is_dir()])==1
    assert len(list(provider.read_batches(candidate[3],purpose='production_training')))==3


def test_high_precision_decimal_targets_roundtrip(tmp_path):
    from decimal import Decimal
    from uuid import UUID
    from datetime import timedelta
    import pandas as pd
    import pyarrow as pa
    from src.repositories.local_dataset_repository import LocalDatasetRepository
    from src.services.dataset_ingestion_service import DatasetIngestionService
    from src.services.dataset_finalization_service import DatasetFinalizationService
    from src.data.contracts.dataset import StoredDatasetReference,DatasetAccessContext,ConsistencyPolicy,VerifiedConsistencyEvidence,ScopedWarningAuthorization
    from test_dataset_finalization import reference
    asset=reference.ref.asset.__wrapped__();mapping=reference.ref.config();frame=reference.ref.raw()
    frame['reported']=pd.Series(pa.array([Decimal('-12345678901234567890.1234'),Decimal('0.0000'),Decimal('4.1234')],type=pa.decimal128(25,4)),index=frame.index,dtype=pd.ArrowDtype(pa.decimal128(25,4)))
    expected=frame.reported.tolist();store=LocalDatasetRepository(tmp_path/'decimals')
    manifest=DatasetIngestionService(store,authorize_ingestion=lambda *_:True).ingest(reference.ControlledAdapter(asset,frame=frame),mapping,dataset_id=UUID(int=99))
    ref=StoredDatasetReference(manifest.organization_id,manifest.dataset_id,manifest.attempt_id,manifest.version_id)
    scope=DatasetAccessContext(ref,manifest.source_id,manifest.ingestion_batch_id,mapping.mapping_id,mapping.version,manifest.mapping_digest,mapping.target_metric,mapping.business_scope,'opaque-source','connection-1','production_training')
    evidence=VerifiedConsistencyEvidence(scope,'decimal-snapshot','snapshot_external_v1','method-1','1.0',NOW-timedelta(days=2),NOW-timedelta(hours=1),NOW-timedelta(minutes=1),NOW+timedelta(days=1))
    warning=ScopedWarningAuthorization(scope,'NEGATIVE_VALUE','value','keep','decimal-business-authorization',NOW-timedelta(hours=1),NOW+timedelta(days=1))
    service=DatasetFinalizationService(store,authorize=lambda *_:True,verify_consistency=lambda *_:True,verify_policy=lambda *_:True,verify_warning=lambda *_:True,decision_context_reference='decimal-context',clock=lambda:NOW)
    service.finalize(ref,mapping,connection_reference='connection-1',consistency_policy=ConsistencyPolicy(scope,'SALES_GENERAL','1.0.0'),consistency_evidence=evidence,warning_authorizations=(warning,))
    actual=[batch.value.iloc[0] for batch in CanonicalTrainingDataProvider(store,authorize_read=lambda *_:True,clock=lambda:NOW).read_batches(ref,purpose='production_training')]
    assert [v.as_tuple() for v in actual]==[v.as_tuple() for v in expected]


def test_read_budget_and_missing_payload_fail_before_visibility(candidate):
    from src.data.contracts.dataset import StagingBudgets
    finalize(candidate)
    with pytest.raises(StagingError):next(reader(candidate).read_batches(candidate[3],purpose='production_training',budgets=StagingBudgets(max_canonical_bytes=64)))
    path=candidate[0]/str(candidate[3].attempt_id)/'1-canonical.parquet';path.parent.chmod(0o700);path.unlink()
    with pytest.raises(StagingError):next(reader(candidate).read_batches(candidate[3],purpose='production_training'))


def test_unknown_admission_cannot_be_forged_with_recomputed_hash(candidate):
    import json,hashlib
    finalize(candidate)
    with sqlite3.connect(candidate[0]/'catalog.sqlite') as conn:
        row=conn.execute('SELECT admission FROM publications').fetchone()[0];data=json.loads(row)
        data['scope']['reference']['organization_id']='00000000-0000-0000-0000-000000000099'
        body=json.dumps(data)
        conn.execute('UPDATE publications SET admission=?,digest=?',(body,hashlib.sha256(body.encode()).hexdigest()))
    with pytest.raises(StagingError):next(reader(candidate).read_batches(candidate[3],purpose='production_training'))


def test_empty_quality_evidence_with_recomputed_hash_cannot_hide_actual_issues(candidate):
    import json,hashlib
    from src.data.processing.quality import QualityPolicy
    from src.data.processing.training_admission import evaluate_training_admission
    finalize(candidate)
    with sqlite3.connect(candidate[0]/'catalog.sqlite') as conn:
        data=json.loads(conn.execute('SELECT admission FROM publications').fetchone()[0])
        data['issues']=[]
        data['warning_authorizations']=[]
        data['quality_result']=evaluate_training_admission([],QualityPolicy.model_validate(data['quality_policy']),
            execution_context=candidate[2].policy_context).model_dump(mode='json')
        body=json.dumps(data)
        conn.execute('UPDATE publications SET admission=?,digest=?',(body,hashlib.sha256(body.encode()).hexdigest()))
    # A structurally valid PASS for an empty issue list cannot conceal the
    # negative/zero values present in the actual, integrity-verified payload.
    with pytest.raises(StagingError):next(reader(candidate).read_batches(candidate[3],purpose='production_training'))
