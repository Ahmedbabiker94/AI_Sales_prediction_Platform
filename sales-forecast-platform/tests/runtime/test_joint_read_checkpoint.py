"""Focused joint-clock/handle tests. Storage mocked; real PG is tested separately."""
import copy
import inspect
import pickle
import runpy
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
import pytest
from src.services.canonical_forecast_read_service import (
    HostReadClock, JointReadCheckpoint, build_canonical_forecast_reader,
)
from src.services.stored_forecast_accuracy_service import build_stored_forecast_accuracy, LabelReadDenied
F=runpy.run_path('tests/runtime/test_stored_forecast_accuracy.py')

@pytest.fixture
def context(monkeypatch):return F['context'].__wrapped__(monkeypatch)


def checkpoint(s, ownership):
 return s.reader._state.issue_joint(s.service._joint_issuer,s.fg,s.fs.canonical_json,s.lg,s.ls.canonical_json,_lifetime=ownership)

@pytest.mark.parametrize('phase',['initial','final'])
@pytest.mark.parametrize('grant',['label','forecast','both'])
@pytest.mark.parametrize('offset',[-.001,0.,.001])
def test_fresh_callback_boundary_deadlines(context,phase,grant,offset):
 s=context
 if grant in ('label','both'):s.lg=s.la.authorize(s.op,s.lp,s.ls,lifetime_seconds=1)
 if grant in ('forecast','both'):s.fg=s.fa.authorize(s.op,s.fp,s.fs,lifetime_seconds=1)
 calls=0
 def cancelled():
  nonlocal calls
  calls+=1
  if calls==(2 if phase=='initial' else 6):s.clock.update(wall=101.+offset,elapsed=1.+offset)
  return False
 out=F['evaluate'](s,cancelled=cancelled)
 assert out.status==('VERIFIED_EVALUATION' if offset<0 else 'DENIED')
 assert s.lookup.call_count==(1 if phase=='final' or offset<0 else 0)
 assert not s.reader._state.joint_records


@pytest.mark.parametrize('phase',['initial','final'])
@pytest.mark.parametrize('kind',['label_clock','forecast_clock'])
def test_callback_from_opposite_authority_cannot_leave_stale_expiry(context,phase,kind):
 s=context;calls=0
 s.lg=s.la.authorize(s.op,s.lp,s.ls,lifetime_seconds=1)
 s.fg=s.fa.authorize(s.op,s.fp,s.fs,lifetime_seconds=1)
 state=s.la._state if kind=='label_clock' else s.reader._state
 original=state.wall
 def clock():
  nonlocal calls
  calls+=1
  if calls==(1 if phase=='initial' else (2 if kind=='label_clock' else 4)):
   s.clock.update(wall=101.,elapsed=1.)
  return original()
 state.wall=clock
 assert F['evaluate'](s).status=='DENIED'
 assert s.lookup.call_count==(0 if phase=='initial' else 1)


def test_original_reader_internal_clock_before_dispatch_checks_joint(context):
 s=context;s.lg=s.la.authorize(s.op,s.lp,s.ls,lifetime_seconds=1);calls=0
 original=s.reader._state.wall
 def clock():
  nonlocal calls
  calls+=1
  if calls==2:s.clock.update(wall=101.,elapsed=1.)
  return original()
 s.reader._state.wall=clock
 assert F['evaluate'](s).status=='DENIED';s.lookup.assert_not_called()

@pytest.mark.parametrize('handle',[JointReadCheckpoint(),object(),{},lambda:True])
def test_foreign_or_callable_checkpoint_not_authority(context,handle):
 s=context
 assert s.reader.read(s.fg,s.fs,_joint_checkpoint=handle).status=='DENIED';s.lookup.assert_not_called()

@pytest.mark.parametrize('operation',[copy.copy,copy.deepcopy,pickle.dumps])
def test_original_checkpoint_cannot_copy_or_serialize(context,operation):
 s=context
 with s.reader._state.joint_scope(s.service._joint_issuer) as ownership:
  h=checkpoint(s, ownership)
  with pytest.raises(TypeError):operation(h)
  s.reader._state.close_joint(h)


def test_checkpoint_binding_close_and_standalone_default(context):
 s=context
 with s.reader._state.joint_scope(s.service._joint_issuer) as ownership:
  h=checkpoint(s, ownership)
  assert s.reader.read(s.fg,s.fs,_joint_checkpoint=h).status=='VERIFIED_RECORD_SET'
  assert s.reader.read(s.fg,s.fs,_joint_checkpoint=h).status=='VERIFIED_RECORD_SET'
  changed=s.fs.model_copy(update={'expected_content_sha256':'f'*64})
  assert s.reader.read(s.fg,changed,_joint_checkpoint=h).status=='DENIED'
  s.reader._state.close_joint(h)
  s.lookup.reset_mock()
  assert s.reader.read(s.fg,s.fs,_joint_checkpoint=h).status=='DENIED';s.lookup.assert_not_called()
  s.la.revoke(s.op,s.lg)
  # Standalone forecast read permission remains independent of label permission.
  assert s.reader.read(s.fg,s.fs).status=='VERIFIED_RECORD_SET'
  assert F['evaluate'](s).status=='DENIED'


@pytest.mark.parametrize('edge',['label','forecast'])
@pytest.mark.parametrize('error',[RuntimeError,KeyboardInterrupt,SystemExit])
def test_callback_errors_close_checkpoint_without_lookup(context,edge,error):
 s=context
 def fail():raise error('injected')
 (s.la._state if edge=='label' else s.reader._state).wall=fail
 if error is RuntimeError:assert F['evaluate'](s).status=='DENIED'
 else:
  with pytest.raises(error):F['evaluate'](s)
 assert not s.reader._state.joint_records;s.lookup.assert_not_called()


@pytest.mark.parametrize('phase',['initial','final'])
@pytest.mark.parametrize('authority',['label','forecast'])
def test_revoke_from_final_callbacks_checked_under_joint_locks(context,phase,authority):
 s=context;calls=0
 def cancelled():
  nonlocal calls
  calls+=1
  if calls==(2 if phase=='initial' else 6):
   (s.la if authority=='label' else s.fa).revoke(s.op,s.lg if authority=='label' else s.fg)
  return False
 assert F['evaluate'](s,cancelled=cancelled).status=='DENIED'
 assert s.lookup.call_count==(0 if phase=='initial' else 1)


def test_distinct_authoritative_domains_no_epoch_translation(context):
 s=context;clock=F['ClockControl'](s.op,{'wall':1000.,'elapsed':500.})
 a,e=build_stored_forecast_accuracy(s.reader,s.batch.scope,(s.snap,),operator=s.op,
  wall_clock=lambda:clock['wall'],elapsed_clock=lambda:clock['elapsed'],clock_domain=clock.domain)
 principal=a.establish_principal(s.op,subject_reference='synthetic:separate-label-domain',scope=s.batch.scope)
 grant=a.authorize(s.op,principal,s.ls,lifetime_seconds=1)
 assert e.evaluate(s.fg,s.fs,grant,s.ls).status=='VERIFIED_EVALUATION'
 s.lookup.reset_mock();clock.update(wall=1001.,elapsed=501.)
 assert e.evaluate(s.fg,s.fs,grant,s.ls).status=='DENIED';s.lookup.assert_not_called()


def test_concurrent_calls_multiple_label_states_same_original_reader(context):
 s=context
 a,e=build_stored_forecast_accuracy(s.reader,s.batch.scope,(s.snap,),operator=s.op,clock_domain=s.clock.domain)
 p=a.establish_principal(s.op,subject_reference='synthetic:second-issuer',scope=s.batch.scope)
 h=a.authorize(s.op,p,s.ls)
 def task(i):return F['evaluate'](s) if i%2 else e.evaluate(s.fg,s.fs,h,s.ls)
 with ThreadPoolExecutor(max_workers=6) as pool:
  assert all(r.status=='VERIFIED_EVALUATION' for r in pool.map(task,range(24)))
 assert not s.reader._state.joint_records


def test_legacy_standalone_reader_unchanged_and_no_grant_rebinding(context):
 s=context;op=object()
 a,r=build_canonical_forecast_reader(s.target,s.batch.scope,operator=op,wall_clock=lambda:100.,elapsed_clock=lambda:0.)
 p=a.establish_principal(op,subject_reference='synthetic:legacy',scope=s.batch.scope);g=a.authorize(op,p,s.fs)
 assert r.read(g,s.fs).status=='VERIFIED_RECORD_SET'
 with pytest.raises((LabelReadDenied,ValueError)):
  build_stored_forecast_accuracy(r,s.batch.scope,(s.snap,),operator=op)
 assert r._state.clock_domain is None


def test_no_callback_runs_in_joint_final_phase(context):
 s=context
 with s.reader._state.joint_scope(s.service._joint_issuer) as ownership:
  h=checkpoint(s, ownership)
  def forbidden():raise AssertionError('callback during callback-free checkpoint')
  s.reader._state.wall=s.reader._state.elapsed=s.la._state.wall=s.la._state.elapsed=forbidden
  assert s.reader._state.check_joint(h,s.fg,s.fs).handle is h
  s.reader._state.close_joint(h)
  assert '_joint_checkpoint' not in inspect.signature(s.service.evaluate).parameters


def test_wrong_host_operator_or_peer_cannot_mint_checkpoint(context):
 s=context
 with pytest.raises(ValueError):s.reader._state.bind_joint(object(),s.la._state)
 with pytest.raises(ValueError):s.reader._state.bind_joint(s.op,lambda:True)
 with pytest.raises(ValueError):s.reader._state.issue_joint(JointReadCheckpoint(),s.fg,s.fs.canonical_json,s.lg,s.ls.canonical_json)


def test_concurrent_distinct_clock_domains_share_original_forecast_authority(context):
 s=context;services=[]
 for epoch in (1000.,2000.):
  domain=HostReadClock(s.op,synthetic_start=(epoch,epoch/2))
  a,e=build_stored_forecast_accuracy(s.reader,s.batch.scope,(s.snap,),operator=s.op,clock_domain=domain)
  p=a.establish_principal(s.op,subject_reference='synthetic:parallel-domain',scope=s.batch.scope)
  services.append((e,a.authorize(s.op,p,s.ls)))
 def task(i):
  e,g=services[i%2];return e.evaluate(s.fg,s.fs,g,s.ls)
 with ThreadPoolExecutor(max_workers=6) as pool:
  assert all(r.status=='VERIFIED_EVALUATION' for r in pool.map(task,range(24)))
 assert not s.reader._state.joint_records


def test_system_clock_domain_mode_is_not_a_caller_sampler(context):
 s=context;op=object();domain=HostReadClock(op)
 a,r=build_canonical_forecast_reader(s.target,s.batch.scope,operator=op,clock_domain=domain)
 p=a.establish_principal(op,subject_reference='synthetic:system-clock-host',scope=s.batch.scope)
 g=a.authorize(op,p,s.fs)
 labels,e=build_stored_forecast_accuracy(r,s.batch.scope,(s.snap,),operator=op)
 principal=labels.establish_principal(op,subject_reference='synthetic:system-clock-labels',scope=s.batch.scope)
 h=labels.authorize(op,principal,s.ls)
 assert e.evaluate(g,s.fs,h,s.ls).status=='VERIFIED_EVALUATION'
 with pytest.raises(ValueError):domain.set_synthetic(op,wall=0.,elapsed=0.)
 with pytest.raises(ValueError):
  build_canonical_forecast_reader(s.target,s.batch.scope,operator=op,clock_domain=lambda:True)
