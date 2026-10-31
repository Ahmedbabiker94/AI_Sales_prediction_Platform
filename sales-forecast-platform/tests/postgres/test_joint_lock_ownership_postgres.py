"""Original grants, real PG read credentials; setup writes explicitly separate."""
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import runpy,json
import pytest
from conftest import driver
from src.database import forecast_read_connection as connections
from src.providers.forecast_read_provider import ForecastReadProvider
R=runpy.run_path(str(Path(__file__).parents[1]/'runtime/test_joint_lock_ownership.py'))
PG=runpy.run_path(str(Path(__file__).with_name('test_stored_forecast_accuracy_postgres.py')))

@pytest.fixture
def watched(monkeypatch):
    real=driver();d=SimpleNamespace(opened=[],sql=[],dispatch=0)
    class Cursor(real.extensions.cursor):
        def execute(self,q,*a,**kw):d.sql.append(q);return super().execute(q,*a,**kw)
    class Connection(real.extensions.connection):
        def cursor(self,*a,**kw):kw['cursor_factory']=Cursor;return super().cursor(*a,**kw)
    def connect(**kw):
        assert kw['user']=='r7_forecast_reader'
        c=real.connect(**kw,connection_factory=Connection);d.opened.append(c);return c
    original=ForecastReadProvider.lookup
    def lookup(self,*a,**kw):d.dispatch+=1;return original(self,*a,**kw)
    monkeypatch.setattr(connections,'driver',lambda:SimpleNamespace(connect=connect))
    monkeypatch.setattr(ForecastReadProvider,'lookup',lookup)
    return d

@pytest.mark.parametrize('phase,target',R['PHASE_TARGETS'])
@pytest.mark.parametrize('edge',['acquired_return','cleanup_enter'])
@pytest.mark.parametrize('kind',['SIGINT','SystemExit'])
def test_real_pg_original_grants_lock_gaps(watched,phase,target,edge,kind):
    s=PG['setup']()
    result=R['fault'](s,phase,target,edge,kind,lambda:PG['evaluate'](s))
    assert len(watched.opened)==watched.dispatch==(1 if phase=='abort_joint' else 0)
    assert all(c.closed for c in watched.opened)
    assert all(q.split()[0] in ('SELECT','SHOW') for q in watched.sql)
    print('LOCK_PG',json.dumps(dict(result,connections=len(watched.opened),lookups=watched.dispatch,all_closed=True)))

@pytest.mark.parametrize('target',['reader','label','forecast_clock','label_clock'])
@pytest.mark.parametrize('kind',['SIGINT','SystemExit'])
def test_final_lock_cutoff_after_genuine_read(watched,target,kind):
    s=PG['setup']()
    result=R['fault'](s,'check_joint',target,'acquired_return',kind,lambda:PG['evaluate'](s),occurrence=4)
    assert len(watched.opened)==watched.dispatch==1 and all(c.closed for c in watched.opened)
    assert all(q.split()[0] in ('SELECT','SHOW') for q in watched.sql)
    print('FINAL_LOCK_PG',json.dumps(dict(result,connections=1,all_closed=True)))


def test_cancel_while_unrelated_real_reader_connection_active(monkeypatch):
    s=PG['setup']();real=driver();opened=[];active=Event();release=Event();seen=[]
    class Cursor(real.extensions.cursor):
        def execute(self,q,*a,**kw):
            if q.startswith('SELECT') and not seen:
                seen.append(q);active.set();assert release.wait(5)
            return super().execute(q,*a,**kw)
    class Connection(real.extensions.connection):
        def cursor(self,*a,**kw):kw['cursor_factory']=Cursor;return super().cursor(*a,**kw)
    def connect(**kw):
        assert kw['user']=='r7_forecast_reader'
        c=real.connect(**kw,connection_factory=Connection);opened.append(c);return c
    monkeypatch.setattr(connections,'driver',lambda:SimpleNamespace(connect=connect))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(s.reader.read,s.fg,s.fs)
        assert active.wait(3)
        try:
            assert PG['evaluate'](s,cancelled=lambda:True).status=='DENIED'
            assert len(opened)==1 and not opened[0].closed and not s.reader._state.joint_records
        finally:release.set()
        assert future.result(timeout=3).status=='VERIFIED_RECORD_SET'
    assert all(c.closed for c in opened)
