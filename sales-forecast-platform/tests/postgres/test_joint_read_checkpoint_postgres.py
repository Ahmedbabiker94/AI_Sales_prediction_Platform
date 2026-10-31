"""Actual disposable PG joint-boundary tests; synthetic fixtures, no model use."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import copy,pickle,runpy
from types import SimpleNamespace
import pytest
from conftest import driver
from src.database import forecast_read_connection as connections
from src.providers.forecast_read_provider import ForecastReadProvider
from src.services.canonical_forecast_read_service import JointReadCheckpoint
from src.services.stored_forecast_accuracy_service import build_stored_forecast_accuracy
PG=runpy.run_path(str(Path(__file__).with_name('test_stored_forecast_accuracy_postgres.py')))

@pytest.fixture
def watched(monkeypatch):
 real=driver();data=SimpleNamespace(opened=[],sql=[],dispatch=0)
 class Cursor(real.extensions.cursor):
  def execute(self,q,*a,**kw):data.sql.append(q);return super().execute(q,*a,**kw)
 class Connection(real.extensions.connection):
  def cursor(self,*a,**kw):kw['cursor_factory']=Cursor;return super().cursor(*a,**kw)
 def connect(**kw):
  assert kw['user']=='r7_forecast_reader'
  c=real.connect(**kw,connection_factory=Connection);data.opened.append(c);return c
 lookup=ForecastReadProvider.lookup
 def dispatch(self,*a,**kw):data.dispatch+=1;return lookup(self,*a,**kw)
 monkeypatch.setattr(connections,'driver',lambda:SimpleNamespace(connect=connect))
 monkeypatch.setattr(ForecastReadProvider,'lookup',dispatch)
 return data


def h(s, ownership):return s.reader._state.issue_joint(s.service._joint_issuer,s.fg,s.fs.canonical_json,s.lg,s.ls.canonical_json,_lifetime=ownership)

@pytest.mark.parametrize('case',['A','B','C','D'])
def test_four_original_and_diagnostic_failures_closed(watched,case):
 s=PG['setup']();calls=0
 if case=='C':s.fg=s.fa.authorize(s.op,s.fp,s.fs,lifetime_seconds=1)
 else:s.lg=s.la.authorize(s.op,s.lp,s.ls,lifetime_seconds=1)
 if case=='D':
  original=s.reader._state.wall
  def clock():
   nonlocal calls
   calls+=1
   if calls==2:s.clock.update(wall=101.,elapsed=1.)
   return original()
  s.reader._state.wall=clock
  out=PG['evaluate'](s)
 else:
  def cancelled():
   nonlocal calls
   calls+=1
   if calls==(2 if case=='A' else 6):s.clock.update(wall=101.,elapsed=1.)
   return False
  out=PG['evaluate'](s,cancelled=cancelled)
 assert out.status=='DENIED' and out.evidence is None
 expected=0 if case in ('A','D') else 1
 assert len(watched.opened)==watched.dispatch==expected
 assert all(c.closed for c in watched.opened)
 assert all(q.split()[0] in ('SELECT','SHOW') for q in watched.sql)
 assert not s.reader._state.joint_records
 print('REPAIRED_BOUNDARY',case,'DENIED','connections',len(watched.opened),'lookup',watched.dispatch)

@pytest.mark.parametrize('authority',['label','forecast','both'])
@pytest.mark.parametrize('tick',[-.001,0.,.001])
def test_exact_initial_cutoff_with_real_connection_instrumentation(watched,authority,tick):
 s=PG['setup']()
 if authority in ('label','both'):s.lg=s.la.authorize(s.op,s.lp,s.ls,lifetime_seconds=1)
 if authority in ('forecast','both'):s.fg=s.fa.authorize(s.op,s.fp,s.fs,lifetime_seconds=1)
 s.clock.update(wall=101.+tick,elapsed=1.+tick)
 out=PG['evaluate'](s)
 assert out.status==('VERIFIED_EVALUATION' if tick<0 else 'DENIED')
 assert len(watched.opened)==watched.dispatch==(1 if tick<0 else 0)
 assert all(c.closed for c in watched.opened)

@pytest.mark.parametrize('which',['label','forecast'])
@pytest.mark.parametrize('phase',['initial','final'])
def test_revoked_by_supported_callback_no_data(watched,which,phase):
 s=PG['setup']();calls=0
 def cancelled():
  nonlocal calls
  calls+=1
  if calls==(2 if phase=='initial' else 6):
   (s.la if which=='label' else s.fa).revoke(s.op,s.lg if which=='label' else s.fg)
  return False
 assert PG['evaluate'](s,cancelled=cancelled).status=='DENIED'
 assert len(watched.opened)==watched.dispatch==(0 if phase=='initial' else 1)
 assert all(c.closed for c in watched.opened)

@pytest.mark.parametrize('error',[RuntimeError,KeyboardInterrupt,SystemExit])
def test_internal_reader_callback_failure_propagation_and_no_connection(watched,error):
 s=PG['setup']();calls=0;clock=s.reader._state.wall
 def fail():
  nonlocal calls
  calls+=1
  if calls==2:raise error('injected')
  return clock()
 s.reader._state.wall=fail
 if error is RuntimeError:assert PG['evaluate'](s).status=='DENIED'
 else:
  with pytest.raises(error):PG['evaluate'](s)
 assert watched.opened==[] and watched.dispatch==0 and not s.reader._state.joint_records

@pytest.mark.parametrize('value',[JointReadCheckpoint(),{},lambda:True])
def test_foreign_checkpoint_before_real_sql(watched,value):
 s=PG['setup']()
 assert s.reader.read(s.fg,s.fs,_joint_checkpoint=value).status=='DENIED'
 assert watched.opened==[] and watched.dispatch==0

@pytest.mark.parametrize('operation',[copy.copy,copy.deepcopy,pickle.dumps])
def test_copy_reconstruction_not_original_checkpoint(watched,operation):
 s=PG['setup']()
 with s.reader._state.joint_scope(s.service._joint_issuer) as ownership:
  original=h(s, ownership)
  with pytest.raises(TypeError):operation(original)
  s.reader._state.close_joint(original)
  assert s.reader.read(s.fg,s.fs,_joint_checkpoint=JointReadCheckpoint()).status=='DENIED'
  assert watched.opened==[] and watched.dispatch==0


def test_concurrent_real_reads_and_final_checks_no_additional_connection(watched):
 s=PG['setup']()
 with ThreadPoolExecutor(max_workers=4) as pool:
  results=list(pool.map(lambda _:PG['evaluate'](s),range(8)))
 assert all(r.status=='VERIFIED_EVALUATION' for r in results)
 assert len(watched.opened)==watched.dispatch==8
 assert all(c.closed for c in watched.opened)
 assert len({r.evidence.content_sha256 for r in results})==1
 assert not s.reader._state.joint_records


def test_different_label_epoch_and_forecast_epoch_both_pinned(watched):
 s=PG['setup']();clock=PG['F']['ClockControl'](s.op,{'wall':1000.,'elapsed':500.})
 a,e=build_stored_forecast_accuracy(s.reader,s.batch.scope,(s.snapshot,),operator=s.op,
  wall_clock=lambda:clock['wall'],elapsed_clock=lambda:clock['elapsed'],clock_domain=clock.domain)
 p=a.establish_principal(s.op,subject_reference='synthetic:independent-clock',scope=s.batch.scope)
 g=a.authorize(s.op,p,s.ls,lifetime_seconds=1);calls=0
 def cancelled():
  nonlocal calls
  calls+=1
  if calls==2:clock.update(wall=1001.,elapsed=501.)
  return False
 assert e.evaluate(s.fg,s.fs,g,s.ls,cancelled=cancelled).status=='DENIED'
 assert watched.opened==[] and watched.dispatch==0


def test_exact_two_metric_revisions_preserve_original_forecast_evidence(watched):
 s=PG['setup']();first=PG['evaluate'](s)
 revised=PG['F']['snapshot'](s.batch,values=('501','651'),revision='joint-repair-revision-B')
 a,e=build_stored_forecast_accuracy(s.reader,s.batch.scope,(s.snapshot,revised),operator=s.op,clock_domain=s.clock.domain)
 p=a.establish_principal(s.op,subject_reference='synthetic:metric-review',scope=s.batch.scope)
 selection=s.ls.model_copy(update={'revision_id':revised.revision_id,'expected_snapshot_sha256':revised.content_sha256})
 grant=a.authorize(s.op,p,selection);second=e.evaluate(s.fg,s.fs,grant,selection)
 assert first.status==second.status=='VERIFIED_EVALUATION'
 assert (first.evidence.mae,first.evidence.rmse,first.evidence.bias)==(1.0000152587890625,1.0520395551960913,-1.0000152587890625)
 assert (second.evidence.mae,second.evidence.rmse,second.evidence.bias)==(2.0000152587890625,2.026528495549795,-2.0000152587890625)
 assert first.evidence.forecast_batch_json==second.evidence.forecast_batch_json==s.batch.canonical_json
 assert first.evidence.forecast_receipt_json==second.evidence.forecast_receipt_json
 assert [p.prediction_float32_le for p in first.evidence.pairs]==[p.prediction_float32_le for p in second.evidence.pairs]==['2d56f943','e9542244']*2
 assert len(watched.opened)==watched.dispatch==2 and all(c.closed for c in watched.opened)
 assert first.evidence.label_selection.revision_id!=second.evidence.label_selection.revision_id
 assert first.evidence.content_sha256!=second.evidence.content_sha256
 print('EXACT_REPAIRED_METRICS',first.evidence.mae,first.evidence.rmse,first.evidence.bias,second.evidence.mae,second.evidence.rmse,second.evidence.bias)
