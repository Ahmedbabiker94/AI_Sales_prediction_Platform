"""Synthetic local R-03C integration; trusted verifiers are controlled doubles."""
from dataclasses import replace
from datetime import datetime,timezone,timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
from uuid import UUID

import pytest

from src.data.contracts.dataset import (StoredDatasetReference,DatasetAccessContext,ConsistencyPolicy,
    VerifiedConsistencyEvidence,ScopedWarningAuthorization,StagingBudgets)
from src.data.processing.sales_quality import build_general_sales_quality_policy
from src.data.processing.training_admission import TrainingAdmissionError
from src.providers.dataset_store_provider import StagingError
from src.repositories.local_dataset_repository import LocalDatasetRepository
from src.services.dataset_ingestion_service import DatasetIngestionService
from src.services.dataset_finalization_service import DatasetFinalizationService

spec=importlib.util.spec_from_file_location('r03c_stage_input',Path('tests/data/staging/test_dataset_ingestion.py'))
reference=importlib.util.module_from_spec(spec);spec.loader.exec_module(reference)
NOW=datetime(2026,1,2,tzinfo=timezone.utc)


@pytest.fixture
def candidate(tmp_path):
    root=tmp_path/'store';store=LocalDatasetRepository(root);asset=reference.ref.asset.__wrapped__()
    mapping=reference.ref.config();adapter=reference.ControlledAdapter(asset)
    manifest=DatasetIngestionService(store,authorize_ingestion=lambda *_:True).ingest(adapter,mapping,dataset_id=UUID(int=99),batch_size=1)
    ref=StoredDatasetReference(manifest.organization_id,manifest.dataset_id,manifest.attempt_id,manifest.version_id)
    scope=DatasetAccessContext(ref,manifest.source_id,manifest.ingestion_batch_id,mapping.mapping_id,mapping.version,
        manifest.mapping_digest,mapping.target_metric,mapping.business_scope,'opaque-source','connection-1','production_training')
    policy=ConsistencyPolicy(scope,'SALES_GENERAL','1.0.0')
    evidence=VerifiedConsistencyEvidence(scope,'snapshot-1','snapshot_external_v1','method-1','1.0',
        NOW-timedelta(days=2),NOW-timedelta(hours=1),NOW-timedelta(minutes=5),NOW+timedelta(days=1))
    warning=ScopedWarningAuthorization(scope,'NEGATIVE_VALUE','value','keep','business-approved-1',NOW-timedelta(hours=1),NOW+timedelta(days=1))
    service=DatasetFinalizationService(store,authorize=lambda *_:True,verify_consistency=lambda *_:True,
        verify_policy=lambda *_:True,verify_warning=lambda *_:True,decision_context_reference='permission-session-1',clock=lambda:NOW)
    return root,store,mapping,ref,scope,policy,evidence,warning,service


def state(root):
    with sqlite3.connect(root/'catalog.sqlite') as c:return c.execute('SELECT state FROM attempts').fetchone()[0]


def publications(root):
    with sqlite3.connect(root/'catalog.sqlite') as c:return c.execute('SELECT COUNT(*) FROM publications').fetchone()[0]


def finalize(c,**overrides):
    _,_,mapping,ref,_,policy,evidence,warning,service=c
    args=dict(connection_reference='connection-1',consistency_policy=policy,consistency_evidence=evidence,warning_authorizations=(warning,))
    args.update(overrides)
    return service.finalize(ref,mapping,**args)


def test_verified_a_finalizes_and_is_idempotent(candidate):
    assert finalize(candidate)==candidate[3] and state(candidate[0])=='finalized'
    assert finalize(candidate)==candidate[3]
    with sqlite3.connect(candidate[0]/'catalog.sqlite') as conn:
        assert conn.execute('SELECT COUNT(*) FROM publications').fetchone()[0]==1
        evidence=json.loads(conn.execute('SELECT admission FROM publications').fetchone()[0])
    assert evidence['consistency_level']=='A' and evidence['quality_result']['metadata']['training_admitted']
    assert evidence['warning_authorizations'][0]['scope']['reference']['version_id']==candidate[3].version_id


@pytest.mark.parametrize('mode',['C','B_unapproved','warning_missing','warning_unverified','consistency_unverified','policy_unverified','expired','replay','purpose'])
def test_fail_closed(candidate,mode):
    evidence=candidate[6];overrides={}
    if mode=='C':overrides['consistency_evidence']=None
    if mode=='B_unapproved':overrides['consistency_evidence']=replace(evidence,method='reconciliation_authoritative_v1')
    if mode=='warning_missing':overrides['warning_authorizations']=()
    if mode=='warning_unverified':candidate[8].verify_warning=lambda *_:False
    if mode=='consistency_unverified':candidate[8].verify_consistency=lambda *_:False
    if mode=='policy_unverified':candidate[8].verify_policy=lambda *_:False
    if mode=='expired':overrides['consistency_evidence']=replace(evidence,expires_at=NOW-timedelta(minutes=1))
    if mode=='replay':overrides['consistency_evidence']=replace(evidence,scope=replace(candidate[4],reference=replace(candidate[3],attempt_id=UUID(int=998))))
    if mode=='purpose':overrides['purpose']='isolated_diagnostics'
    with pytest.raises((StagingError,TrainingAdmissionError)):finalize(candidate,**overrides)
    conclusive=mode in ('C','B_unapproved','warning_missing','consistency_unverified')
    assert state(candidate[0])==('rejected' if conclusive else 'sealed')
    assert publications(candidate[0])==0
    if conclusive:
        with pytest.raises(StagingError):finalize(candidate)


def test_verified_b_requires_scoped_policy_and_preserves_classification(candidate):
    policy=replace(candidate[5],allow_reconciliation=True,reconciliation_approval_reference='approved-reconciliation-policy')
    finalize(candidate,consistency_policy=policy,consistency_evidence=replace(candidate[6],method='reconciliation_authoritative_v1'))
    with sqlite3.connect(candidate[0]/'catalog.sqlite') as conn:data=json.loads(conn.execute('SELECT admission FROM publications').fetchone()[0])
    assert data['consistency_level']=='B' and data['consistency_evidence']['method']=='reconciliation_authoritative_v1'


@pytest.mark.parametrize('role',['authorize','verify_consistency','verify_policy','verify_warning'])
@pytest.mark.parametrize('error_type',[RuntimeError,StagingError])
def test_external_errors_are_sanitized(candidate,role,error_type):
    import traceback
    def fail(*_):raise error_type('CALLBACK_PRIVATE_SECRET')
    setattr(candidate[8],role,fail)
    with pytest.raises(StagingError) as exc:finalize(candidate)
    assert 'CALLBACK_PRIVATE_SECRET' not in ''.join(traceback.format_exception(exc.value))
    assert state(candidate[0])=='sealed'
    assert publications(candidate[0])==0


@pytest.mark.parametrize('damage',['chunk','manifest','lineage','mapping','no_completion'])
def test_storage_failures_cannot_publish(candidate,damage):
    root,_,mapping,ref,*_=candidate
    if damage=='chunk':
        path=root/str(ref.attempt_id)/'2-canonical.parquet';path.chmod(0o600);path.write_bytes(b'corrupt')
    else:
        with sqlite3.connect(root/'catalog.sqlite') as conn:
            if damage=='manifest':conn.execute("UPDATE attempts SET manifest='{}'")
            if damage=='lineage':conn.execute("UPDATE chunks SET envelope='not-json' WHERE ordinal=1")
            if damage=='no_completion':conn.execute("UPDATE attempts SET state='staging'")
    kwargs={'mapping':None} if damage=='mapping' else {}
    if damage=='mapping':
        bad=reference.ref.config(version='2.0')
        with pytest.raises(StagingError):candidate[8].finalize(ref,bad,connection_reference='connection-1',consistency_policy=candidate[5],consistency_evidence=candidate[6])
    else:
        with pytest.raises(StagingError):finalize(candidate)
    with sqlite3.connect(root/'catalog.sqlite') as conn:assert conn.execute('SELECT COUNT(*) FROM publications').fetchone()[0]==0
    assert state(root)==('staging' if damage=='no_completion' else 'sealed')


def test_authorization_revocation_before_publish(candidate):
    candidate[8].authorize=lambda ref,purpose,phase:phase!='publish'
    with pytest.raises(StagingError):finalize(candidate)
    assert state(candidate[0])=='sealed'
    assert publications(candidate[0])==0
    candidate[8].authorize=lambda *_:True
    assert finalize(candidate)==candidate[3]
    assert state(candidate[0])=='finalized' and publications(candidate[0])==1


def test_no_caller_supplied_approval_can_publish(candidate):
    root,store,*_=candidate
    with store._publication_session(StagingBudgets()):
        capture=store._candidate(candidate[3],StagingBudgets())
        with pytest.raises(StagingError):store._publish(capture,'{"approved":true}',object(),lambda:None)
    assert state(root)=='sealed'


def test_reject_and_critical_rules_override_warning_authorization(candidate):
    from src.data.processing.quality import QualityAction,QualityRule
    policy=build_general_sales_quality_policy();policy.rules.append(QualityRule(rule_id='reject_negative',issue_code='NEGATIVE_VALUE',action=QualityAction.REJECT))
    with pytest.raises(TrainingAdmissionError):finalize(candidate,quality_policy=policy)
    assert state(candidate[0])=='rejected'
    assert publications(candidate[0])==0


@pytest.mark.parametrize('action',['repair','impute','normalize','deduplicate','quarantine'])
def test_required_unexecuted_actions_are_not_authorized(candidate,action):
    from src.data.processing.quality import QualityRule
    policy=build_general_sales_quality_policy();policy.rules.append(QualityRule(rule_id='required_action',issue_code='NEGATIVE_VALUE',action=action))
    with pytest.raises(TrainingAdmissionError):finalize(candidate,quality_policy=policy)
    assert state(candidate[0])=='rejected'
    assert publications(candidate[0])==0


@pytest.mark.parametrize('severity',['info','critical'])
def test_unknown_and_critical_findings_fail_closed(candidate,severity,monkeypatch):
    import src.services.dataset_finalization_service as module
    from src.data.processing.quality import QualityIssue
    actual=module.detect_canonical_dataset_quality
    def detect(*args):
        issues=actual(*args);issues.append(QualityIssue(issue_code='UNKNOWN_FOUNDATION_ISSUE',issue_type='invalid',severity=severity,affected_count=1,message='bounded issue'))
        return issues
    monkeypatch.setattr(module,'detect_canonical_dataset_quality',detect)
    with pytest.raises(TrainingAdmissionError):finalize(candidate)
    assert state(candidate[0])=='rejected' and publications(candidate[0])==0


@pytest.mark.parametrize('change',['version','organization','metric','field','expired'])
def test_business_warning_authorization_is_exactly_scoped(candidate,change):
    warning=candidate[7]
    if change=='version':warning=replace(warning,scope=replace(warning.scope,reference=replace(candidate[3],version_id='a'*64)))
    if change=='organization':warning=replace(warning,scope=replace(warning.scope,reference=replace(candidate[3],organization_id=UUID(int=9))))
    if change=='metric':warning=replace(warning,scope=replace(warning.scope,metric='returns'))
    if change=='field':warning=replace(warning,field='other')
    if change=='expired':warning=replace(warning,expires_at=NOW-timedelta(minutes=1))
    with pytest.raises((StagingError,TrainingAdmissionError)):finalize(candidate,warning_authorizations=(warning,))


@pytest.mark.parametrize('change',['connection','source','future','window','policy_version'])
def test_consistency_evidence_replay_and_validity(candidate,change):
    evidence=candidate[6]
    if change=='connection':evidence=replace(evidence,scope=replace(evidence.scope,connection_reference='other'))
    if change=='source':evidence=replace(evidence,scope=replace(evidence.scope,source_id=UUID(int=9)))
    if change=='future':evidence=replace(evidence,verified_at=NOW+timedelta(hours=1))
    if change=='window':evidence=replace(evidence,window_start=NOW-timedelta(hours=3),window_end=NOW-timedelta(hours=1))
    if change=='policy_version':
        with pytest.raises(ValueError):replace(evidence,policy_version='2.0')
        return
    with pytest.raises(StagingError):finalize(candidate,consistency_evidence=evidence)


def test_row_count_method_cannot_claim_reconciliation(candidate):
    with pytest.raises(ValueError):replace(candidate[6],method='matching_row_counts_v1')
    with pytest.raises(StagingError):finalize(candidate,consistency_evidence=replace(candidate[6],method='snapshot_transaction_v1'))


def test_new_warning_evidence_cannot_overwrite_finalized_version(candidate):
    finalize(candidate)
    with pytest.raises(StagingError):finalize(candidate,warning_authorizations=(replace(candidate[7],reference='different-approval'),))
    assert state(candidate[0])=='finalized'


def test_recheck_catches_tampering_after_quality(candidate):
    def warning(*_):
        path=candidate[0]/str(candidate[3].attempt_id)/'2-canonical.parquet';path.chmod(0o600);path.write_bytes(b'changed')
        return True
    candidate[8].verify_warning=warning
    with pytest.raises(StagingError):finalize(candidate)
    assert state(candidate[0])=='sealed'
    assert publications(candidate[0])==0


def test_late_publication_authorization_revocation(candidate):
    calls=[]
    def authorize(ref,purpose,phase):
        if phase=='publish':calls.append(1)
        return phase!='publish' or len(calls)==1
    candidate[8].authorize=authorize
    with pytest.raises(StagingError):finalize(candidate)
    assert state(candidate[0])=='sealed'
    assert publications(candidate[0])==0
    candidate[8].authorize=lambda *_:True
    assert finalize(candidate)==candidate[3]
    assert state(candidate[0])=='finalized' and publications(candidate[0])==1


def test_competing_finalization_lock_fails_without_rejecting(candidate):
    with candidate[1]._publication_session(StagingBudgets()):
        with pytest.raises(StagingError):finalize(candidate)
    assert state(candidate[0])=='sealed'
    finalize(candidate)


@pytest.mark.parametrize('phase',['before_commit','after_commit'])
def test_real_process_publication_crash(candidate,phase):
    import subprocess,sys,os
    script='''
import runpy,json,os
from pathlib import Path
from uuid import UUID
from contextlib import contextmanager
from datetime import timedelta
from src.data.contracts.dataset import *
from src.repositories.local_dataset_repository import LocalDatasetRepository
from src.services.dataset_finalization_service import DatasetFinalizationService
m=runpy.run_path('tests/data/staging/test_dataset_finalization.py');NOW=m['NOW']
store=LocalDatasetRepository(ROOT);mapping=m['reference'].ref.config()
import sqlite3
with sqlite3.connect(Path(ROOT)/'catalog.sqlite') as c:d=json.loads(c.execute('SELECT manifest FROM attempts').fetchone()[0])
ref=StoredDatasetReference(UUID(d['organization_id']),UUID(d['dataset_id']),UUID(d['attempt_id']),d['version_id'])
scope=DatasetAccessContext(ref,UUID(d['source_id']),UUID(d['ingestion_batch_id']),mapping.mapping_id,mapping.version,d['mapping_digest'],mapping.target_metric,mapping.business_scope,'opaque-source','connection-1','production_training')
policy=ConsistencyPolicy(scope,'SALES_GENERAL','1.0.0')
evidence=VerifiedConsistencyEvidence(scope,'snapshot-1','snapshot_external_v1','method-1','1.0',NOW-timedelta(days=2),NOW-timedelta(hours=1),NOW-timedelta(minutes=5),NOW+timedelta(days=1))
warning=ScopedWarningAuthorization(scope,'NEGATIVE_VALUE','value','keep','business-approved-1',NOW-timedelta(hours=1),NOW+timedelta(days=1))
if PHASE=='before_commit':
 original=store._connection
 class Proxy:
  def __init__(self,c):self.c=c
  def execute(self,q,*a):
   if q.startswith('INSERT INTO publications'):os._exit(31)
   return self.c.execute(q,*a)
  def __getattr__(self,name):return getattr(self.c,name)
 @contextmanager
 def connection(b):
  with original(b) as c:yield Proxy(c)
 store._connection=connection
else:
 original=store._publish
 def publish(*a,**kw):original(*a,**kw);os._exit(31)
 store._publish=publish
service=DatasetFinalizationService(store,authorize=lambda *_:True,verify_consistency=lambda *_:True,verify_policy=lambda *_:True,verify_warning=lambda *_:True,decision_context_reference='permission-session-1',clock=lambda:NOW)
service.finalize(ref,mapping,connection_reference='connection-1',consistency_policy=policy,consistency_evidence=evidence,warning_authorizations=(warning,))
'''.replace('ROOT',repr(str(candidate[0]))).replace('PHASE',repr(phase))
    result=subprocess.run([sys.executable,'-c',script],timeout=10,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True)
    assert result.returncode==31,result.stderr
    assert state(candidate[0])==('sealed' if phase=='before_commit' else 'finalized')
    with sqlite3.connect(candidate[0]/'catalog.sqlite') as conn:assert conn.execute('SELECT COUNT(*) FROM publications').fetchone()[0]==(0 if phase=='before_commit' else 1)
    if phase=='after_commit':
        from src.providers.canonical_training_data_provider import CanonicalTrainingDataProvider
        assert sum(len(v) for v in CanonicalTrainingDataProvider(candidate[1],authorize_read=lambda *_:True,clock=lambda:NOW).read_batches(candidate[3],purpose='production_training'))==3


def test_supported_transaction_snapshot_a(tmp_path):
    spec=importlib.util.spec_from_file_location('r03c_pg_double',Path('tests/data/adapters/test_network_adapters.py'))
    network=importlib.util.module_from_spec(spec);spec.loader.exec_module(network)
    store=LocalDatasetRepository(tmp_path/'pg-store');asset=network.asset.__wrapped__();adapter,connection,_=network.pg_adapter(asset)
    from src.data.contracts.dataset import ConsistencyEvidence
    manifest=DatasetIngestionService(store,authorize_ingestion=lambda *_:True,verify_consistency=lambda *_:True).ingest(
        adapter,network.mapping(),dataset_id=UUID(int=99),consistency=ConsistencyEvidence('transaction_scoped','pg-transaction-1'),batch_size=1)
    ref=StoredDatasetReference(manifest.organization_id,manifest.dataset_id,manifest.attempt_id,manifest.version_id);mapping=network.mapping()
    scope=DatasetAccessContext(ref,manifest.source_id,manifest.ingestion_batch_id,mapping.mapping_id,mapping.version,manifest.mapping_digest,
        mapping.target_metric,mapping.business_scope,'sales-import','pg-connection-1','production_training')
    evidence=VerifiedConsistencyEvidence(scope,'transaction-1','snapshot_transaction_v1','pg-readonly-method-1','1.0',NOW-timedelta(days=2),NOW-timedelta(hours=1),NOW-timedelta(minutes=1),NOW+timedelta(days=1))
    warning=ScopedWarningAuthorization(scope,'NEGATIVE_VALUE','value','keep','approved-signed-sales',NOW-timedelta(hours=1),NOW+timedelta(days=1))
    service=DatasetFinalizationService(store,authorize=lambda *_:True,verify_consistency=lambda *_:True,verify_policy=lambda *_:True,verify_warning=lambda *_:True,decision_context_reference='pg-policy-context',clock=lambda:NOW)
    assert service.finalize(ref,mapping,connection_reference='pg-connection-1',consistency_policy=ConsistencyPolicy(scope,'SALES_GENERAL','1.0.0'),consistency_evidence=evidence,warning_authorizations=(warning,))==ref
    assert connection.closed and connection.rollbacks==1


def test_expiry_before_atomic_publication(candidate):
    def authorization(ref,purpose,phase):
        if phase=='publish':candidate[8].clock=lambda:NOW+timedelta(days=2)
        return True
    candidate[8].authorize=authorization
    with pytest.raises(StagingError):finalize(candidate)
    assert state(candidate[0])=='sealed'
    assert publications(candidate[0])==0


def test_quality_duplicate_found_across_chunk_boundary(candidate,monkeypatch):
    actual=candidate[1]._verified_batches
    def duplicated(*args):
        for i,frame in enumerate(actual(*args)):
            yield frame
            if i==0:yield frame.copy(deep=True)
    monkeypatch.setattr(candidate[1],'_verified_batches',duplicated)
    with pytest.raises(TrainingAdmissionError) as error:finalize(candidate)
    assert 'BUSINESS_KEY_DUPLICATE' in error.value.result.metadata['blocking_issue_codes']
    assert state(candidate[0])=='rejected' and publications(candidate[0])==0


def test_unknown_quality_action_rejects_at_contract_boundary():
    from src.data.processing.quality import QualityRule
    with pytest.raises(ValueError):QualityRule(rule_id='unknown',issue_code='NEGATIVE_VALUE',action='invented_action')


@pytest.mark.parametrize('failure',['verify_consistency','verify_policy','verify_warning','wrong_mapping'])
def test_request_and_verifier_failure_preserve_sealed_and_retry_fresh(candidate,failure):
    service=candidate[8];calls=[]
    for role in ('verify_consistency','verify_policy','verify_warning'):
        def verified(*_,role=role):
            calls.append(role)
            return True
        setattr(service,role,verified)
    if failure=='wrong_mapping':
        with pytest.raises(StagingError):service.finalize(candidate[3],reference.ref.config(version='2.0'),
            connection_reference='connection-1',consistency_policy=candidate[5],
            consistency_evidence=candidate[6],warning_authorizations=(candidate[7],))
    else:
        original=getattr(service,failure)
        def unavailable(*_):raise RuntimeError('TRANSIENT_PRIVATE_SECRET')
        setattr(service,failure,unavailable)
        with pytest.raises(StagingError) as error:finalize(candidate)
        import traceback
        assert 'TRANSIENT_PRIVATE_SECRET' not in ''.join(traceback.format_exception(error.value))
        setattr(service,failure,original)
    assert state(candidate[0])=='sealed' and publications(candidate[0])==0
    calls.clear()
    assert finalize(candidate)==candidate[3]
    assert calls==['verify_policy','verify_consistency','verify_warning']
    assert state(candidate[0])=='finalized' and publications(candidate[0])==1


def test_late_chunk_corruption_stays_private_and_rechecks_on_retry(candidate):
    from src.providers.canonical_training_data_provider import CanonicalTrainingDataProvider
    path=candidate[0]/str(candidate[3].attempt_id)/'2-canonical.parquet'
    path.chmod(0o600);path.write_bytes(b'corrupted-synthetic-chunk')
    for _ in range(2):
        with pytest.raises(StagingError):finalize(candidate)
        assert state(candidate[0])=='sealed' and publications(candidate[0])==0
        reader=CanonicalTrainingDataProvider(candidate[1],authorize_read=lambda *_:True,clock=lambda:NOW)
        with pytest.raises(StagingError):next(reader.read_batches(candidate[3],purpose='production_training'))


@pytest.mark.parametrize('stage',['detector','evaluator'])
def test_unexpected_internal_error_is_not_a_conclusive_denial(candidate,monkeypatch,stage):
    import src.services.dataset_finalization_service as module
    if stage=='detector':
        name='detect_canonical_dataset_quality'
        error=RuntimeError('INTERNAL_PRIVATE_SECRET')
    else:
        # Even a typed admission error from outside the enforcement boundary is
        # not proof of a completed assessment of this candidate.
        name='evaluate_training_admission'
        error=TrainingAdmissionError([],module.evaluate_training_admission([],build_general_sales_quality_policy(),execution_context='synthetic'))
    original=getattr(module,name)
    def fail(*_a,**_kw):raise error
    monkeypatch.setattr(module,name,fail)
    with pytest.raises(StagingError):finalize(candidate)
    assert state(candidate[0])=='sealed' and publications(candidate[0])==0
    monkeypatch.setattr(module,name,original)
    assert finalize(candidate)==candidate[3]
    assert state(candidate[0])=='finalized' and publications(candidate[0])==1


def test_transaction_failure_after_readiness_claim_allows_fresh_retry(candidate,monkeypatch):
    from contextlib import contextmanager
    store=candidate[1];original=store._connection;claims=[]
    claim=candidate[8]._claim_publication
    def claimed(*args):
        claim(*args);claims.append(1)
    monkeypatch.setattr(candidate[8],'_claim_publication',claimed)
    class Connection:
        def __init__(self,conn):self.conn=conn
        def execute(self,sql,*args):
            result=self.conn.execute(sql,*args)
            if sql.startswith('INSERT INTO publications'):
                raise sqlite3.OperationalError('SYNTHETIC_TRANSACTION_FAILURE')
            return result
        def __getattr__(self,name):return getattr(self.conn,name)
    @contextmanager
    def unavailable(budgets):
        with original(budgets) as conn:yield Connection(conn)
    monkeypatch.setattr(store,'_connection',unavailable)
    with pytest.raises(StagingError):finalize(candidate)
    assert claims==[1]
    assert state(candidate[0])=='sealed' and publications(candidate[0])==0
    monkeypatch.setattr(store,'_connection',original)
    assert finalize(candidate)==candidate[3]
    assert claims==[1,1]
    assert state(candidate[0])=='finalized' and publications(candidate[0])==1


def test_malformed_verifier_response_establishes_no_denial(candidate):
    candidate[8].verify_consistency=lambda *_:{'verified':False}
    with pytest.raises(StagingError):finalize(candidate)
    assert state(candidate[0])=='sealed' and publications(candidate[0])==0
    candidate[8].verify_consistency=lambda *_:True
    assert finalize(candidate)==candidate[3]
    assert state(candidate[0])=='finalized' and publications(candidate[0])==1


def test_verifier_exceeding_deadline_cannot_establish_rejection(candidate):
    elapsed=[0]
    candidate[8].elapsed_clock=lambda:elapsed[0]
    def late_denial(*_):
        elapsed[0]=301
        return False
    candidate[8].verify_consistency=late_denial
    with pytest.raises(StagingError):finalize(candidate)
    assert state(candidate[0])=='sealed' and publications(candidate[0])==0
    elapsed[0]=0;candidate[8].verify_consistency=lambda *_:True
    assert finalize(candidate)==candidate[3]
    assert state(candidate[0])=='finalized' and publications(candidate[0])==1
