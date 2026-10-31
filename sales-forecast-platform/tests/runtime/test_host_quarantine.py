"""Fail-stop escalation: original grants, real signals, bounded owned children.

No reset/manual unlock is used. Within-process denial is proved before each
quarantined child exits. Database mode uses the original disposable PG reader.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import copy
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch
import inspect
import json
import os
import runpy
import signal
import subprocess
import sys
import pytest
from src.services import canonical_forecast_read_service as C
from src.providers.forecast_read_provider import ForecastReadProvider


def child(order, publication='none', inflight=False, postgres=False):
    if postgres:
        sys.path.insert(0, str(Path('tests/postgres').resolve()))
        F = runpy.run_path('tests/postgres/test_stored_forecast_accuracy_postgres.py')
        s = F['setup']()
        from src.database import forecast_read_connection as connections
        real = connections.driver()
        opened, sql = [], []
        class Cursor(real.extensions.cursor):
            def execute(self, q, *a, **kw):
                sql.append(q)
                return super().execute(q, *a, **kw)
        class Connection(real.extensions.connection):
            def cursor(self, *a, **kw):
                kw['cursor_factory'] = Cursor
                return super().cursor(*a, **kw)
        def connect(**kw):
            assert kw['user'] == 'r7_forecast_reader'
            c = real.connect(**kw, connection_factory=Connection)
            opened.append(c)
            return c
        connections.driver = lambda: SimpleNamespace(connect=connect)
        real_lookup = ForecastReadProvider.lookup
        def lookup(provider, *a):
            return real_lookup(provider, *a)
    else:
        F = runpy.run_path('tests/runtime/test_stored_forecast_accuracy.py')
        b = F['make_batch']()
        s = F['setup_context'](b, (F['snapshot'](b),))
        opened, sql = [], []
        def lookup(provider, *a):
            return F['stored'](b)
    assert s.fa.host_health == 'HEALTHY'
    state = s.reader._state
    dispatch = []
    active, release = Event(), Event()
    def watched(provider, *a):
        dispatch.append(True)
        found = lookup(provider, *a)
        if inflight:
            active.set()
            assert release.wait(5)
        return found
    captured, retained = [], []
    first = second = False
    hit = False
    source, start = inspect.getsourcelines(C._HostHealth.status)
    marker = 'self._shutdown = True' if publication.startswith('before') else "return 'SHUTDOWN_REQUIRED'"
    line = start + (max if publication.startswith('after') else next)(
        (i for i,x in enumerate(source) if marker in x))
    def publishing(frame,event,arg):
        nonlocal hit
        if not hit and frame.f_code is C._HostHealth.status.__code__ and event == 'line' and frame.f_lineno == line:
            hit = True
            sys.settrace(None)
            interrupt(publication.split('_',1)[1])
        return publishing
    def relevant(frame):
        p = frame.f_back
        return (frame.f_code is C._release_owned.__code__ and p is not None
                and p.f_code is C._locked_call.__code__
                and p.f_back.f_code.co_name == 'issue_joint'
                and len(frame.f_locals.get('attempts', ())) == 2)
    def interrupt(kind):
        if kind == 'SIGINT':
            os.kill(os.getpid(), signal.SIGINT)
        else:
            raise SystemExit(73)
    def trace(frame, event, arg):
        nonlocal first
        if not first and event == 'call' and relevant(frame):
            first = True
            captured.extend(frame.f_locals['attempts'])
            retained.extend(state.joint_records.values())
            sys.settrace(None)
            interrupt('SIGINT' if order == 'sigint_first' else 'SystemExit')
        return trace
    def profile(frame, event, arg):
        nonlocal second
        if first and not second and event == 'call' and relevant(frame):
            second = True
            sys.setprofile(None)
            if publication != 'none':
                sys.settrace(publishing)
            interrupt('SystemExit' if order == 'sigint_first' else 'SIGINT')
    pool = ThreadPoolExecutor(max_workers=1)
    future = None
    try:
        with patch.object(ForecastReadProvider, 'lookup', watched):
            if inflight:
                future = pool.submit(s.reader.read, s.fg, s.fs)
                assert active.wait(3)
            try:
                sys.setprofile(profile)
                sys.settrace(trace)
                F['evaluate'](s)
                raise AssertionError('interruption did not propagate')
            except (KeyboardInterrupt, SystemExit) as exc:
                propagated = type(exc).__name__
            finally:
                sys.settrace(None)
                sys.setprofile(None)
            assert first and second
            assert len(captured) == 2 and all(a.baseline == 0 and a.lock._recursion_count() == 1 for a in captured)
            assert all(r.lifetime() is None or r.lifetime().closed for r in retained)
            assert len(dispatch) == int(inflight) and len(opened) == int(inflight and postgres)
            # Publication is reached during caller cleanup, immediately after
            # the double-interrupted executor unwinds. A third interruption is
            # permitted to propagate; its orphan evidence must remain decisive.
            if publication != 'none':
                assert hit
            assert s.fa.host_health == 'SHUTDOWN_REQUIRED'
            if future is not None:
                release.set()
                assert future.result(timeout=3).status == 'DENIED'
            assert all(c.closed for c in opened)
            before = len(dispatch), len(opened), len(sql)
            rejected = []
            def deny(name, fn):
                try:
                    fn()
                except Exception:
                    rejected.append(name)
                else:
                    raise AssertionError('unsafe continuation: '+name)
            deny('forecast_principal', lambda:s.fa.establish_principal(s.op,subject_reference='new',scope=s.batch.scope))
            deny('forecast_grant', lambda:s.fa.authorize(s.op,s.fp,s.fs))
            deny('label_principal', lambda:s.la.establish_principal(s.op,subject_reference='new',scope=s.batch.scope))
            deny('label_grant', lambda:s.la.authorize(s.op,s.lp,s.ls))
            deny('checkpoint_scope', lambda:state.joint_scope(s.service._joint_issuer))
            deny('replacement_clock', lambda:C.HostReadClock(object(),synthetic_start=(100.,100.)))
            deny('replacement_reader', lambda:C.build_canonical_forecast_reader(state.reader,s.batch.scope,operator=s.op))
            with pytest.raises(AttributeError):
                s.fa.host_health = 'HEALTHY'
            assert not any(name in vars(type(s.fa)) for name in ('reset','unlock','recover'))
            for r in retained:
                deny('original_checkpoint',lambda r=r:state.check_joint(r.handle,s.fg,s.fs))
                deny('copied_checkpoint',lambda r=r:state.check_joint(copy(r.handle),s.fg,s.fs))
            assert F['evaluate'](s).status == 'DENIED'
            assert s.reader.read(s.fg,s.fs).status == 'DENIED'
            assert pool.submit(s.reader.read,s.fg,s.fs).result(timeout=2).status == 'DENIED'
            assert before == (len(dispatch),len(opened),len(sql))
            assert s.fa.host_health == 'SHUTDOWN_REQUIRED'
            assert all(a.lock._recursion_count() == 1 for a in captured)
            assert all(q.split()[0] in ('SELECT','SHOW') for q in sql)
            print('QUARANTINE_PROOF', json.dumps(dict(runtime=sys.version.split()[0],postgres=postgres,
                order=order,publication=publication,inflight=inflight,propagated=propagated,
                baseline=[a.baseline for a in captured],stranded_depth=[a.lock._recursion_count() for a in captured],
                health=s.fa.host_health,denied=rejected,dispatch=len(dispatch),connections=len(opened),
                all_closed=True,manual_unlock=False,within_process_denials=True)),flush=True)
    finally:
        release.set()
        pool.shutdown(wait=True)


@pytest.mark.parametrize('order',['sigint_first','systemexit_first'])
@pytest.mark.parametrize('publication',['none','before_SIGINT','before_SystemExit','after_SIGINT','after_SystemExit'])
@pytest.mark.parametrize('inflight',[False,True])
def test_double_cleanup_terminal_quarantine(order,publication,inflight):
    p = subprocess.run([sys.executable,'-B',__file__,order,publication,str(int(inflight))],
        capture_output=True,text=True,timeout=15,start_new_session=True)
    assert p.returncode == 0,(p.stdout,p.stderr)
    assert 'QUARANTINE_PROOF' in p.stdout
    print(p.stdout)


if __name__ == '__main__':
    child(sys.argv[1], sys.argv[2], bool(int(sys.argv[3])), len(sys.argv)>4 and sys.argv[4]=='postgres')


def callback_child(order, at, postgres=False):
    """Quarantine from a nested original request at each supported callback."""
    F=runpy.run_path('tests/postgres/test_stored_forecast_accuracy_postgres.py' if postgres
                     else 'tests/runtime/test_stored_forecast_accuracy.py')
    if postgres:
        outer,inner=F['setup'](),F['setup']()
        lookup=ForecastReadProvider.lookup
    else:
        b=F['make_batch']()
        outer,inner=(F['setup_context'](b,(F['snapshot'](b),)) for _ in range(2))
        def lookup(provider,*a):return F['stored'](b)
    dispatch=[];calls=0;first=second=False;captured=[]
    def watched(provider,*a):
        dispatch.append(True)
        return lookup(provider,*a)
    def relevant(frame):
        p=frame.f_back
        return (frame.f_code is C._release_owned.__code__ and p is not None
                and p.f_code is C._locked_call.__code__ and p.f_back.f_code.co_name=='issue_joint'
                and len(frame.f_locals.get('attempts',()))==2)
    def interrupt(kind):
        if kind=='SIGINT':os.kill(os.getpid(),signal.SIGINT)
        else:raise SystemExit(73)
    def trace(frame,event,arg):
        nonlocal first
        if not first and event=='call' and relevant(frame):
            first=True;captured.extend(frame.f_locals['attempts']);sys.settrace(None)
            interrupt('SIGINT' if order=='sigint_first' else 'SystemExit')
        return trace
    def profile(frame,event,arg):
        nonlocal second
        if first and not second and event=='call' and relevant(frame):
            second=True;sys.setprofile(None)
            interrupt('SystemExit' if order=='sigint_first' else 'SIGINT')
    def callback():
        nonlocal calls
        calls+=1
        if calls==at:
            try:
                sys.setprofile(profile);sys.settrace(trace);F['evaluate'](inner)
                raise AssertionError('interruption did not propagate')
            except (KeyboardInterrupt,SystemExit):pass
            finally:sys.settrace(None);sys.setprofile(None)
            assert first and second and inner.fa.host_health=='SHUTDOWN_REQUIRED'
        return False
    with patch.object(ForecastReadProvider,'lookup',watched):
        out=F['evaluate'](outer,cancelled=callback)
        assert out.status=='DENIED' and out.evidence is None and first and second
        assert len(dispatch)==int(at>=4)
        assert outer.fa.host_health=='SHUTDOWN_REQUIRED'
        assert outer.reader.read(outer.fg,outer.fs).status=='DENIED'
        assert all(a.baseline==0 and a.lock._recursion_count()==1 for a in captured)
        assert all(r.lifetime() is None or r.lifetime().closed for r in outer.reader._state.joint_records.values())
        print('CALLBACK_QUARANTINE',json.dumps(dict(order=order,callback=at,postgres=postgres,
            result=out.status,lookups=len(dispatch),health=outer.fa.host_health,manual_unlock=False)),flush=True)


@pytest.mark.parametrize('order',['sigint_first','systemexit_first'])
@pytest.mark.parametrize('at',[1,2,3,4,5,6])
def test_quarantine_inside_original_authorization_callback(order,at):
    code=('import runpy; f=runpy.run_path("tests/runtime/test_host_quarantine.py"); '
          'f["callback_child"]('+repr(order)+','+str(at)+')')
    p=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True,timeout=15,start_new_session=True)
    assert p.returncode==0,(p.stdout,p.stderr)
    print(p.stdout)
