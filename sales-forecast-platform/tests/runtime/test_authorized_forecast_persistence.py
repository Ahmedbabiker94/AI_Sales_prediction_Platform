"""Contract-only: original registries/frozen M3 with STUB B3, no DB/native proof."""
import copy
import json
import runpy
from unittest.mock import Mock
import pytest
from src.handoff.host_admission import ProtectedHostAdmissions,AdmissionHandle
from src.handoff.protocol import digest,HandoffError
from src.services.authorized_forecast_persistence import AuthorizedForecastPersistence,CommitCapability
from src.database.offline_forecast_postgres import ForecastStorageConflict,ForecastStorageRejected,ForecastStorageRetryable,AmbiguousForecastCommit

BASE=runpy.run_path('tests/runtime/test_native_handoff.py')

@pytest.fixture(scope='module')
def prepared(tmp_path_factory):
 g=BASE['prepared'].__wrapped__(tmp_path_factory);yield next(g);next(g,None)

@pytest.fixture
def context(prepared,monkeypatch):
 s=BASE['context'].__wrapped__(prepared,monkeypatch)
 control={'wall':100.,'elapsed':0.,'cancel':False}
 operator=object();retainer=ProtectedHostAdmissions(operator=operator,wall=lambda:control['wall'],elapsed=lambda:control['elapsed'])
 e=BASE['exporter'](s);p,r,c=BASE['reserved'](e);channel=object();session=retainer._session_type(BASE['CTX'],channel,deadline=150.,idempotency='e'*64,approve=lambda *_:True,cancelled=lambda:False,wall=lambda:100.,elapsed=lambda:0.)
 session.state='WORKER_VERIFIED';a=session.proposal(p,channel);r.update(approval=a['approval']);ch=session.reserved(r,channel)
 release=dict(ch,grant=r['grant'],expires=150.,release=digest([BASE['CTX'],ch,r['grant']]))
 detached=session.admit(release,channel)
 # PRIVATE test instrumentation only. No claim this is real broker/native origin.
 ticket=object();retainer._pending[ticket]=None;retainer._stage(ticket,session,channel,release);record=retainer._pending.pop(ticket);retainer._records[id(record.handle)]=record
 storage=Mock();commit_operator=object();service=AuthorizedForecastPersistence(retainer,storage,operator=commit_operator,wall=lambda:control['wall'],elapsed=lambda:control['elapsed'])
 return dict(s=s,control=control,retainer=retainer,operator=operator,h=record.handle,record=record,session=session,detached=detached,storage=storage,service=service,op=commit_operator)

def authorize(s,**kwargs):return s['service'].authorize(s['op'],s['h'],deadline=149.,**kwargs)

@pytest.mark.parametrize('value',[None,{},AdmissionHandle(),CommitCapability(),'a'*64])
def test_detached_no_admission_no_database(context,value):
 s=context
 with pytest.raises(ValueError):s['service'].authorize(s['op'],value,deadline=149.)
 s['storage']._append_initial.assert_not_called();s['storage']._lookup.assert_not_called()

@pytest.mark.parametrize('fault',['detached','foreign_retainer','copy','new_handle','wrong_operator','revoked','expired','cancelled'])
def test_original_identity_and_independent_authorization(context,fault):
 s=context;h=s['h'];op=s['op'];kw={}
 if fault=='detached':h=s['detached']
 elif fault=='foreign_retainer':
  other=ProtectedHostAdmissions(operator=object());s['service']._admissions=other
 elif fault=='copy':
  with pytest.raises(TypeError):copy.copy(h)
  return
 elif fault=='new_handle':h=AdmissionHandle()
 elif fault=='wrong_operator':op=object()
 elif fault=='revoked':s['retainer'].revoke(s['operator'],h)
 elif fault=='expired':s['control']['wall']=149.
 elif fault=='cancelled':kw['cancelled']=lambda:True
 with pytest.raises(ValueError):s['service'].authorize(op,h,deadline=149.,**kw)
 s['storage']._append_initial.assert_not_called()

@pytest.mark.parametrize('field',['body','context','grant','reservation','approval','final_challenge','active','channel','state'])
def test_retained_session_tampering(context,field):
 s=context
 if field=='body':s['session'].body='{}'
 elif field=='context':s['session'].context=dict(s['session'].context,session='0'*64)
 elif field=='active':s['session'].active=False
 elif field=='channel':s['session'].channel=object()
 elif field=='state':s['session'].state='UNKNOWN'
 else:setattr(s['session'],field,'0'*64)
 with pytest.raises(ValueError):authorize(s)
 s['storage']._append_initial.assert_not_called()

def test_one_original_admission_only_one_authorization(context):
 s=context;h=authorize(s)
 assert s['service'].status(h)=='COMMIT_AUTHORIZED'
 with pytest.raises(ValueError):authorize(s)
 s['storage']._append_initial.assert_not_called()

@pytest.mark.parametrize('edge',['cancel','wall','elapsed','admission'])
def test_terminal_callback_revocation_before_reservation(context,edge):
 s=context;h=authorize(s)
 def revoke(value):s['service'].revoke(s['op'],h);return value
 if edge=='wall':s['service']._wall=lambda:revoke(100.)
 elif edge=='elapsed':s['service']._elapsed=lambda:revoke(0.)
 elif edge=='admission':s['retainer'].revoke(s['operator'],s['h'])
 with pytest.raises(ValueError):s['service'].commit(h,cancelled=(lambda:revoke(False)) if edge=='cancel' else lambda:False)
 s['storage']._append_initial.assert_not_called()

@pytest.mark.parametrize('fault',['expiry','elapsed','revoked','cancelled','copied','foreign'])
def test_pre_reservation_rejection_opens_no_db(context,fault):
 s=context;h=authorize(s)
 if fault=='expiry':s['control']['wall']=130.
 elif fault=='elapsed':s['control']['elapsed']=30.
 elif fault=='revoked':s['service'].revoke(s['op'],h)
 elif fault=='copied':
  with pytest.raises(TypeError):copy.copy(h)
  return
 elif fault=='foreign':h=CommitCapability()
 with pytest.raises(ValueError):s['service'].commit(h,cancelled=lambda:fault=='cancelled')
 s['storage']._append_initial.assert_not_called()

@pytest.mark.parametrize('error',[RuntimeError,AmbiguousForecastCommit,KeyboardInterrupt])
def test_unknown_permanently_burns_attempt_and_missing_receipt_no_retry(context,error):
 s=context;h=authorize(s);s['storage']._append_initial.side_effect=error('injected')
 with pytest.raises(BaseException):s['service'].commit(h)
 assert s['service'].status(h)=='COMMIT_OUTCOME_UNKNOWN'
 with pytest.raises(ValueError):s['service'].commit(h)
 s['storage']._lookup.return_value=None
 assert s['service'].reconcile(s['op'],h)==dict(state='COMMIT_OUTCOME_UNKNOWN',receipt=None)
 with pytest.raises(ValueError):s['service'].commit(h)
 assert s['storage']._append_initial.call_count==1

@pytest.mark.parametrize('error',[ForecastStorageConflict,ForecastStorageRejected,ForecastStorageRetryable])
def test_confirmed_rejection_is_not_unknown_and_no_retry(context,error):
 s=context;h=authorize(s);s['storage']._append_initial.side_effect=error('rejected')
 assert s['service'].commit(h)['state']=='COMMIT_REJECTED'
 with pytest.raises(ValueError):s['service'].commit(h)
 with pytest.raises(ValueError):s['service'].reconcile(s['op'],h)
 assert s['storage']._append_initial.call_count==1

def test_invalid_response_is_unknown_not_fabricated_receipt(context):
 s=context;h=authorize(s);s['storage']._append_initial.return_value={'state':'COMMITTED'}
 with pytest.raises(Exception):s['service'].commit(h)
 assert s['service'].status(h)=='COMMIT_OUTCOME_UNKNOWN'

@pytest.mark.parametrize('fault',['foreign_operator','no_attempt','new_handle'])
def test_readonly_reconciliation_requires_original_attempt(context,fault):
 s=context;h=authorize(s)
 if fault=='new_handle':h=CommitCapability()
 with pytest.raises(ValueError):s['service'].reconcile(object() if fault=='foreign_operator' else s['op'],h)
 s['storage']._lookup.assert_not_called()


def stored(s):
 from datetime import datetime,timezone
 from uuid import uuid4
 from src.data.contracts.forecast_evidence_storage import StoredForecastBatch,ForecastStorageReceipt
 b=s['retainer'].verify(s['h'],deadline=149.)
 receipt=ForecastStorageReceipt(receipt_id=uuid4(),scope=b.scope,idempotency_id=b.idempotency_id,
  content_sha256=b.content_sha256,transaction_timestamp=datetime.now(timezone.utc),row_sha256=tuple(r.row_sha256 for r in b.rows))
 return StoredForecastBatch(batch=b,receipt=receipt)


def test_original_admission_cannot_be_claimed_by_two_coordinators(context):
 s=context;authorize(s)
 other_op=object();other=AuthorizedForecastPersistence(s['retainer'],Mock(),operator=other_op,wall=lambda:100.,elapsed=lambda:0.)
 with pytest.raises(ValueError):other.authorize(other_op,s['h'],deadline=149.)
 s['storage']._append_initial.assert_not_called()


def test_success_receipt_verified_without_granting_more_authority(context):
 s=context;h=authorize(s);saved=stored(s);s['storage']._append_initial.return_value=saved
 result=s['service'].commit(h)
 assert result['state']=='COMMIT_CONFIRMED' and result['receipt']==saved
 with pytest.raises(ValueError):s['service'].commit(h)
 with pytest.raises(ValueError):s['service'].commit(result)
 assert s['storage']._append_initial.call_count==1


def test_actual_unknown_record_exact_reconciliation(context):
 s=context;h=authorize(s);saved=stored(s)
 s['storage']._append_initial.side_effect=AmbiguousForecastCommit('lost ack')
 with pytest.raises(AmbiguousForecastCommit):s['service'].commit(h)
 s['storage']._lookup.return_value=saved
 assert s['service'].reconcile(s['op'],h)==dict(state='COMMIT_CONFIRMED',receipt=saved)
 with pytest.raises(ValueError):s['service'].commit(h)
 assert s['storage']._append_initial.call_count==1


def test_foreign_receipt_reconciliation_rejects(context):
 s=context;h=authorize(s);saved=stored(s)
 s['storage']._append_initial.side_effect=AmbiguousForecastCommit('unknown')
 with pytest.raises(AmbiguousForecastCommit):s['service'].commit(h)
 # Validly recomputed foreign CONTENT remains insufficient for this attempt.
 from src.data.contracts.forecast_evidence_storage import StoredForecastBatch,ForecastStorageReceipt
 b=saved.batch.model_copy(update={'idempotency_id':'0'*64})
 r=saved.receipt.model_copy(update={'idempotency_id':'0'*64,'content_sha256':b.content_sha256})
 s['storage']._lookup.return_value=StoredForecastBatch(batch=b,receipt=r)
 with pytest.raises(ValueError):s['service'].reconcile(s['op'],h)
 assert s['service'].status(h)=='COMMIT_OUTCOME_UNKNOWN'


def test_revocation_after_documented_cutoff_does_not_fabricate_rollback(context):
 s=context;h=authorize(s);saved=stored(s)
 def append(batch):
  assert s['service'].status(h)=='TRANSACTION_IN_PROGRESS'
  s['service'].revoke(s['op'],h)
  return saved
 s['storage']._append_initial.side_effect=append
 assert s['service'].commit(h)['state']=='COMMIT_CONFIRMED'
 assert s['storage']._append_initial.call_count==1


def test_concurrent_reservation_only_one_storage_call(context):
 from concurrent.futures import ThreadPoolExecutor
 import threading
 s=context;h=authorize(s);saved=stored(s);entered=threading.Event();release=threading.Event()
 def append(batch):entered.set();assert release.wait(3);return saved
 s['storage']._append_initial.side_effect=append
 with ThreadPoolExecutor(max_workers=2) as pool:
  first=pool.submit(s['service'].commit,h);assert entered.wait(3)
  with pytest.raises(ValueError):s['service'].commit(h)
  release.set();assert first.result(timeout=3)['state']=='COMMIT_CONFIRMED'
 assert s['storage']._append_initial.call_count==1

@pytest.mark.parametrize('field',['records','projection','scope','origin','candidate','values'])
def test_no_caller_projection_override(context,field):
 s=context;h=authorize(s)
 with pytest.raises(TypeError):s['service'].commit(h,**{field:{'forged':True}})
 s['storage']._append_initial.assert_not_called()

@pytest.mark.parametrize('field',['session','channel','source','release','grant'])
def test_no_public_admission_reconstruction_api(context,field):
 s=context
 with pytest.raises(TypeError):s['retainer'].capture(s['operator'],approve=lambda *_:True,**{field:{'forged':True}})
 s['storage']._append_initial.assert_not_called()

@pytest.mark.parametrize('component',['admission','commit_grant'])
def test_revocation_in_final_check_to_reservation_window(context,monkeypatch,component):
 s=context;h=authorize(s);original=s['retainer']._require_claim
 def interleave(*args):
  original(*args)
  # Deterministic scheduler interleaving immediately before reservation locks.
  if component=='admission':s['retainer'].revoke(s['operator'],s['h'])
  else:s['service'].revoke(s['op'],h)
 monkeypatch.setattr(s['retainer'],'_require_claim',interleave)
 with pytest.raises(ValueError):s['service'].commit(h)
 s['storage']._append_initial.assert_not_called()
 assert s['service'].status(h)=='COMMIT_AUTHORIZED'


# Focused repair tests. These use the existing STUB-origin contract fixture;
# genuine SIGINT/PG verification is in tests/persistence/test_interruption_recovery.py.
def interruption_line(function, fragment):
 import inspect
 lines,start=inspect.getsourcelines(function)
 matches=[start+i for i,line in enumerate(lines) if fragment in line]
 assert len(matches)==1,(fragment,matches)
 return matches[0]


@pytest.mark.parametrize('error',[KeyboardInterrupt,SystemExit,RuntimeError])
@pytest.mark.parametrize('fragment,committed',[
 ("r.state='COMMIT_ATTEMPT_RESERVED'",False),
 ("r.state='TRANSACTION_IN_PROGRESS'",False),
 ('saved=self._append(batch)',False),
 ('saved=StoredForecastBatch.model_validate_json(saved.canonical_json)',True),
 ("if saved.batch.canonical_json!=r.projection:raise",True),
 ('r.result=saved',True),
 ("r.state='COMMIT_CONFIRMED'",True),
 ('return dict(state=r.state,receipt=saved)',True),
])
def test_every_reserved_transition_propagates_and_is_readonly_recoverable(context,fragment,committed,error):
 import sys
 s=context;h=authorize(s);receipt=stored(s)
 def append(batch):s['storage']._lookup.return_value=receipt;return receipt
 s['storage']._lookup.return_value=None;s['storage']._append_initial.side_effect=append
 method=s['service'].commit;target=interruption_line(method,fragment)
 def trace(frame,event,arg):
  if event=='line' and frame.f_code is method.__func__.__code__ and frame.f_lineno==target:raise error('injected edge')
  return trace
 sys.settrace(trace)
 try:
  with pytest.raises(error):method(h)
 finally:sys.settrace(None)
 expected_calls=1 if committed else 0
 assert s['storage']._append_initial.call_count==expected_calls
 assert s['service'].status(h) in ('COMMIT_OUTCOME_UNKNOWN','COMMIT_CONFIRMED')
 assert s['service']._records[id(h)].reservation is not None
 for _ in range(2):
  found=s['service'].reconcile(s['op'],h)
  assert found['state']==('COMMIT_CONFIRMED' if committed else 'COMMIT_OUTCOME_UNKNOWN')
  assert found['receipt']==(receipt if committed else None)
 with pytest.raises(ValueError):method(h)
 assert s['storage']._append_initial.call_count==expected_calls


@pytest.mark.parametrize('error',[KeyboardInterrupt,SystemExit,RuntimeError])
def test_interruption_during_provider_is_unknown_and_never_retried(context,error):
 s=context;h=authorize(s);s['storage']._append_initial.side_effect=error('during provider');s['storage']._lookup.return_value=None
 with pytest.raises(error):s['service'].commit(h)
 assert s['service'].status(h)=='COMMIT_OUTCOME_UNKNOWN'
 assert s['service'].reconcile(s['op'],h)['state']=='COMMIT_OUTCOME_UNKNOWN'
 with pytest.raises(ValueError):s['service'].commit(h)
 assert s['storage']._append_initial.call_count==1


def test_second_interrupt_preventing_unknown_assignment_still_allows_readonly_recovery(context):
 import sys
 s=context;h=authorize(s);s['storage']._append_initial.side_effect=KeyboardInterrupt('first');s['storage']._lookup.return_value=None
 method=s['service'].commit;target=interruption_line(method,"r.state='COMMIT_OUTCOME_UNKNOWN'")
 def trace(frame,event,arg):
  if event=='line' and frame.f_code is method.__func__.__code__ and frame.f_lineno==target:raise SystemExit('second')
  return trace
 sys.settrace(trace)
 try:
  with pytest.raises(SystemExit):method(h)
 finally:sys.settrace(None)
 assert s['service'].status(h)=='TRANSACTION_IN_PROGRESS'
 assert not s['service']._records[id(h)].operation.locked()
 assert s['service'].reconcile(s['op'],h)==dict(state='COMMIT_OUTCOME_UNKNOWN',receipt=None)
 with pytest.raises(ValueError):method(h)
 assert s['storage']._append_initial.call_count==1


def test_readonly_recovery_rejects_still_running_provider(context):
 from concurrent.futures import ThreadPoolExecutor
 import threading
 s=context;h=authorize(s);receipt=stored(s);entered=threading.Event();release=threading.Event()
 def append(batch):entered.set();assert release.wait(3);return receipt
 s['storage']._append_initial.side_effect=append
 with ThreadPoolExecutor(max_workers=1) as pool:
  future=pool.submit(s['service'].commit,h);assert entered.wait(3)
  with pytest.raises(ValueError):s['service'].reconcile(s['op'],h)
  s['storage']._lookup.assert_not_called()
  release.set();assert future.result(timeout=3)['state']=='COMMIT_CONFIRMED'
 assert s['storage']._append_initial.call_count==1


@pytest.mark.parametrize('edge',['cancel','wall','elapsed'])
@pytest.mark.parametrize('error',[KeyboardInterrupt,SystemExit,RuntimeError])
def test_callback_exception_before_reservation_never_opens_database(context,edge,error):
 s=context;h=authorize(s)
 def fail():raise error('callback')
 if edge=='wall':s['service']._wall=fail
 elif edge=='elapsed':s['service']._elapsed=fail
 with pytest.raises(error):s['service'].commit(h,cancelled=fail if edge=='cancel' else lambda:False)
 assert s['service']._records[id(h)].reservation is None
 with pytest.raises(ValueError):s['service'].reconcile(s['op'],h)
 s['storage']._append_initial.assert_not_called();s['storage']._lookup.assert_not_called()


def test_interrupted_readonly_confirmation_can_be_repeated_without_writes(context):
 import sys
 s=context;h=authorize(s);receipt=stored(s);s['storage']._append_initial.side_effect=KeyboardInterrupt('lost result')
 with pytest.raises(KeyboardInterrupt):s['service'].commit(h)
 s['storage']._lookup.return_value=receipt
 method=s['service'].reconcile;target=interruption_line(method,"r.state='COMMIT_CONFIRMED'")
 def trace(frame,event,arg):
  if event=='line' and frame.f_code is method.__func__.__code__ and frame.f_lineno==target:raise KeyboardInterrupt('read confirmation')
  return trace
 sys.settrace(trace)
 try:
  with pytest.raises(KeyboardInterrupt):method(s['op'],h)
 finally:sys.settrace(None)
 assert method(s['op'],h)==dict(state='COMMIT_CONFIRMED',receipt=receipt)
 with pytest.raises(ValueError):s['service'].commit(h)
 assert s['storage']._append_initial.call_count==1
