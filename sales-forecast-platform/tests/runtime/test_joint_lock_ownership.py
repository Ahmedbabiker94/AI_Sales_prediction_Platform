"""Bounded lock-ledger faults; no native models or database in this module."""
import contextlib,inspect,os,signal,sys,runpy
from concurrent.futures import ThreadPoolExecutor
from threading import RLock,Event,get_ident,Thread
from types import SimpleNamespace
import pytest
from src.services import canonical_forecast_read_service as C
from src.services.stored_forecast_accuracy_service import build_stored_forecast_accuracy
F=runpy.run_path('tests/runtime/test_stored_forecast_accuracy.py')


def separate_label_domain(s):
    clock=F['ClockControl'](s.op)
    snapshot=getattr(s,'snap',None) or s.snapshot
    a,e=build_stored_forecast_accuracy(s.reader,s.batch.scope,(snapshot,),operator=s.op,clock_domain=clock.domain)
    p=a.establish_principal(s.op,subject_reference='synthetic:lock-probe',scope=s.batch.scope)
    s.la,s.service,s.lp,s.lg=a,e,p,a.authorize(s.op,p,s.ls)
    return (s.reader._state.lock,a._state.lock,s.reader._state.clock_domain._lock,clock.domain._lock)


def fault(s,phase,target,edge,kind,evaluate,occurrence=1):
    locks=separate_label_domain(s);chosen=locks[('reader','label','forecast_clock','label_clock').index(target)]
    source,start=inspect.getsourcelines(C._locked_call)
    before=start+next(i for i,x in enumerate(source) if 'if not lock.acquire' in x)
    hit=False;cleanup_calls=0;phase_calls=0
    def relevant(frame):
        return frame.f_code is C._locked_call.__code__ and frame.f_back.f_code.co_name==phase and (phase=='abort_joint' or len(frame.f_locals.get('attempts',()))>1) and phase_calls==occurrence
    def fail():
        nonlocal hit
        hit=True;sys.settrace(None);sys.setprofile(None)
        if kind=='SIGINT':os.kill(os.getpid(),signal.SIGINT)
        elif kind=='SystemExit':raise SystemExit(73)
        else:raise RuntimeError('injected lock edge')
    def profile(frame,event,arg):
        if hit:return
        if edge=='acquired_return' and relevant(frame) and event=='c_return' and getattr(arg,'__self__',None) is chosen and getattr(arg,'__name__',None)=='acquire':fail()
        if edge=='release_return' and event=='c_return' and frame.f_code is C._release_owned.__code__ and relevant(frame.f_back) and getattr(arg,'__self__',None) is chosen and getattr(arg,'__name__',None)=='release':fail()
    def trace(frame,event,arg):
        nonlocal cleanup_calls,phase_calls
        if hit:return trace
        if (event=='call' and frame.f_code is C._locked_call.__code__
                and frame.f_back.f_code.co_name==phase
                and (phase=='abort_joint' or len(frame.f_locals.get('locks',()))>1)):
            phase_calls+=1
        if edge=='before_acquire' and relevant(frame) and event=='line' and frame.f_lineno==before and frame.f_locals.get('lock') is chosen:fail()
        if edge in ('cleanup_enter','backup_enter') and frame.f_code is C._release_owned.__code__ and event=='call' and relevant(frame.f_back):
            attempts=frame.f_locals.get('attempts',())
            if any(a.lock is chosen for a in attempts):
                cleanup_calls+=1
                if cleanup_calls==(1 if edge=='cleanup_enter' else 2):fail()
        return trace
    previous_trace,previous_profile=sys.gettrace(),sys.getprofile();out=None;propagated=False
    try:
        sys.settrace(trace);sys.setprofile(profile);out=evaluate()
    except (KeyboardInterrupt,SystemExit):propagated=True
    finally:sys.settrace(previous_trace);sys.setprofile(previous_profile)
    assert hit
    if kind=='RuntimeError':assert out is not None and out.status!='VERIFIED_EVALUATION'
    else:assert propagated and out is None
    assert all(l._recursion_count()==0 for l in locks)
    # Independent thread proves no invisible stranded depth is blocking others.
    def available(lock):
        got=lock.acquire(timeout=.25)
        if got:lock.release()
        return got
    with ThreadPoolExecutor(max_workers=1) as pool:assert all(pool.map(available,locks))
    state=s.reader._state
    for r in list(state.joint_records.values()):
        with pytest.raises(C.ForecastReadDenied):state.check_joint(r.handle,s.fg,s.fs)
    assert not state.joint_records
    return dict(phase=phase,target=target,edge=edge,kind=kind,propagated=propagated,all_locks_released=True)

PHASE_TARGETS=[('issue_joint','reader'),('issue_joint','label'),('check_joint','reader'),('check_joint','label'),('check_joint','forecast_clock'),('check_joint','label_clock'),('abort_joint','reader')]

@pytest.mark.parametrize('phase,target',PHASE_TARGETS)
@pytest.mark.parametrize('edge',['before_acquire','acquired_return','cleanup_enter','release_return','backup_enter'])
@pytest.mark.parametrize('kind',['SIGINT','SystemExit','RuntimeError'])
def test_acquire_and_cleanup_ownership(monkeypatch,phase,target,edge,kind):
    s=F['context'].__wrapped__(monkeypatch)
    fault(s,phase,target,edge,kind,lambda:F['evaluate'](s))
    if phase in ('issue_joint','check_joint'):assert s.lookup.call_count==0


@pytest.mark.parametrize('kind',['SIGINT','SystemExit'])
def test_contention_interruption_never_releases_foreign_owner(kind):
    lock=RLock();ready=Event();release=Event();seen=[]
    def owner():
        lock.acquire();ready.set()
        try:assert release.wait(3)
        finally:lock.release()
    t=Thread(target=owner);t.start();assert ready.wait(1)
    source,start=inspect.getsourcelines(C._locked_call);line=start+next(i for i,x in enumerate(source) if 'if not lock.acquire' in x)
    def trace(frame,event,arg):
        if frame.f_code is C._locked_call.__code__ and event=='line' and frame.f_lineno==line:
            sys.settrace(None)
            if kind=='SIGINT':os.kill(os.getpid(),signal.SIGINT)
            else:raise SystemExit(73)
        return trace
    try:
        sys.settrace(trace)
        with pytest.raises((KeyboardInterrupt,SystemExit)):C._locked_call((lock,),lambda:seen.append(True))
        assert lock.acquire(blocking=False) is False and seen==[]
    finally:sys.settrace(None);release.set();t.join(2)
    assert not t.is_alive() and lock.acquire(blocking=False);lock.release()


def test_timeout_pending_and_wrong_thread_ledger():
    lock=RLock();ready=Event();release=Event()
    def owner():
        lock.acquire();ready.set()
        try:assert release.wait(3)
        finally:lock.release()
    t=Thread(target=owner);t.start();assert ready.wait(1)
    try:
        with pytest.raises(C.ForecastReadDenied):C._locked_call((lock,),lambda:None)
        assert lock.acquire(blocking=False) is False
    finally:release.set();t.join(2)
    lock.acquire()
    attempts=(C._LockAttempt(lock,0,get_ident()),)
    with ThreadPoolExecutor(max_workers=1) as pool:
        error=pool.submit(C._release_owned,attempts).exception(timeout=1)
    assert isinstance(error,C.ForecastReadDenied) and lock._recursion_count()==1
    lock.release()


def test_recursive_baseline_and_duplicate_cleanup():
    lock=RLock();lock.acquire()
    try:
        assert C._locked_call((lock,),lambda:C._locked_call((lock,),lambda:lock._recursion_count()))==3
        assert lock._recursion_count()==1
        attempts=(C._LockAttempt(lock,1,get_ident()),)
        C._release_owned(attempts);C._release_owned(attempts)
        assert lock._recursion_count()==1
    finally:lock.release()

@pytest.mark.parametrize('target',['reader','label','forecast_clock','label_clock'])
@pytest.mark.parametrize('edge',['before_acquire','acquired_return','cleanup_enter'])
@pytest.mark.parametrize('kind',['SIGINT','SystemExit'])
def test_final_paired_lock_cutoff_interruption(monkeypatch,target,edge,kind):
    s=F['context'].__wrapped__(monkeypatch)
    fault(s,'check_joint',target,edge,kind,lambda:F['evaluate'](s),occurrence=4)
    assert s.lookup.call_count==1  # legitimate earlier read, no second dispatch


@pytest.mark.parametrize('kind',['SIGINT','SystemExit'])
def test_signal_during_native_blocking_acquisition(kind):
    lock=RLock();ready=Event();release=Event();waiting=Event();sent=[]
    def owner():
        lock.acquire();ready.set()
        try:assert release.wait(3)
        finally:lock.release()
    t=Thread(target=owner);t.start();assert ready.wait(1)
    signo=signal.SIGINT if kind=='SIGINT' else signal.SIGUSR1
    previous_handler=signal.getsignal(signo)
    if kind=='SystemExit':
        def terminate(*_):raise SystemExit(73)
        signal.signal(signo,terminate)
    def sender():
        assert waiting.wait(1)
        # The foreign owner keeps the lock held; main is inside timed acquire.
        import time
        time.sleep(.05);sent.append(True);os.kill(os.getpid(),signo)
    timer=Thread(target=sender);timer.start()
    def profile(frame,event,arg):
        if frame.f_code is C._locked_call.__code__ and event=='c_call' and getattr(arg,'__self__',None) is lock and getattr(arg,'__name__',None)=='acquire':
            sys.setprofile(None);waiting.set()
    try:
        sys.setprofile(profile)
        with pytest.raises((KeyboardInterrupt,SystemExit)):C._locked_call((lock,),lambda:pytest.fail('pending acquisition dispatched'))
        assert sent and lock.acquire(blocking=False) is False
    finally:
        sys.setprofile(None);signal.signal(signo,previous_handler);release.set();timer.join(2);t.join(2)
    assert not t.is_alive() and not timer.is_alive()
    assert lock.acquire(blocking=False);lock.release()


@pytest.mark.parametrize('kind',['SIGINT','SystemExit'])
def test_body_failure_then_primary_cleanup_interruption(kind):
    lock=RLock();hit=False
    def trace(frame,event,arg):
        nonlocal hit
        if not hit and frame.f_code is C._release_owned.__code__ and event=='call':
            hit=True;sys.settrace(None)
            if kind=='SIGINT':os.kill(os.getpid(),signal.SIGINT)
            else:raise SystemExit(73)
        return trace
    def body():raise ValueError('first failure')
    try:
        sys.settrace(trace)
        with pytest.raises((KeyboardInterrupt,SystemExit)):C._locked_call((lock,),body)
    finally:sys.settrace(None)
    assert hit and lock._recursion_count()==0
    with ThreadPoolExecutor(max_workers=1) as pool:
        def available():
            got=lock.acquire(timeout=.25)
            if got:lock.release()
            return got
        assert pool.submit(available).result(timeout=1)

@pytest.mark.parametrize('kind',['SIGINT','SystemExit'])
def test_same_frame_retry_is_fresh_scope_after_spent_issuance(monkeypatch,kind):
    s=F['context'].__wrapped__(monkeypatch)
    fault(s,'issue_joint','reader','acquired_return',kind,lambda:F['evaluate'](s))
    assert s.lookup.call_count==0
    assert F['evaluate'](s).status=='VERIFIED_EVALUATION'
    assert s.lookup.call_count==1 and not s.reader._state.joint_records


def test_mixed_six_thread_domains_and_nested_callbacks(monkeypatch):
    s=F['context'].__wrapped__(monkeypatch);services=[]
    for i in range(3):
        clock=F['ClockControl'](s.op)
        a,e=build_stored_forecast_accuracy(s.reader,s.batch.scope,(s.snap,),operator=s.op,clock_domain=clock.domain)
        p=a.establish_principal(s.op,subject_reference='synthetic:mixed'+str(i),scope=s.batch.scope)
        g=a.authorize(s.op,p,s.ls,lifetime_seconds=1)
        if i==2:clock.update(wall=101.,elapsed=1.)
        services.append((e,g))
    def run(i):
        e,g=services[i%3];return e.evaluate(s.fg,s.fs,g,s.ls).status
    with ThreadPoolExecutor(max_workers=6) as pool:out=list(pool.map(run,range(18)))
    assert out.count('VERIFIED_EVALUATION')==12 and out.count('DENIED')==6
    assert s.lookup.call_count==12 and not s.reader._state.joint_records
    entered=False
    def nested():
        nonlocal entered
        if not entered:
            entered=True
            assert F['evaluate'](s).status=='VERIFIED_EVALUATION'
            assert s.reader.read(s.fg,s.fs).status=='VERIFIED_RECORD_SET'
        return False
    assert F['evaluate'](s,cancelled=nested).status=='VERIFIED_EVALUATION'
    assert s.lookup.call_count==15 and not s.reader._state.joint_records


def test_concurrent_terminal_close_abort(monkeypatch):
    s=F['context'].__wrapped__(monkeypatch);state=s.reader._state
    with state.joint_scope(s.service._joint_issuer) as owner:
        h=state.issue_joint(s.service._joint_issuer,s.fg,s.fs.canonical_json,s.lg,s.ls.canonical_json,_lifetime=owner)
        def close(i):
            if i%2:state.close_joint(h)
            else:state.abort_joint(owner)
        with ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(close,range(18)))
        with pytest.raises(C.ForecastReadDenied):state.check_joint(h,s.fg,s.fs)
        assert not state.joint_records
    assert s.lookup.call_count==0


def test_cancellation_does_not_close_unrelated_active_reader(monkeypatch):
    s=F['context'].__wrapped__(monkeypatch);active=Event();release=Event()
    def lookup(*_):
        active.set();assert release.wait(2);return F['stored'](s.batch)
    s.lookup.side_effect=lookup
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(s.reader.read,s.fg,s.fs)
        assert active.wait(1)
        try:
            assert F['evaluate'](s,cancelled=lambda:True).status=='DENIED'
            assert s.lookup.call_count==1 and not s.reader._state.joint_records
        finally:release.set()
        assert future.result(timeout=2).status=='VERIFIED_RECORD_SET'

@pytest.mark.parametrize('kind',['SIGINT','SystemExit'])
def test_interrupted_close_acquire_is_terminal_in_original_scope(monkeypatch,kind):
    s=F['context'].__wrapped__(monkeypatch);state=s.reader._state;hit=False
    with state.joint_scope(s.service._joint_issuer) as owner:
        h=state.issue_joint(s.service._joint_issuer,s.fg,s.fs.canonical_json,s.lg,s.ls.canonical_json,_lifetime=owner)
        def profile(frame,event,arg):
            nonlocal hit
            if (frame.f_code is C._locked_call.__code__ and frame.f_back.f_code.co_name=='close_joint'
                and event=='c_return' and getattr(arg,'__self__',None) is state.lock
                and getattr(arg,'__name__',None)=='acquire'):
                hit=True;sys.setprofile(None)
                if kind=='SIGINT':os.kill(os.getpid(),signal.SIGINT)
                else:raise SystemExit(73)
        try:
            sys.setprofile(profile)
            with pytest.raises((KeyboardInterrupt,SystemExit)):state.close_joint(h)
        finally:sys.setprofile(None)
        assert hit and owner.closed and state.lock._recursion_count()==0
        with pytest.raises(C.ForecastReadDenied):state.check_joint(h,s.fg,s.fs)
        assert not state.joint_records
    assert s.lookup.call_count==0


@pytest.mark.parametrize('supported',[True,False])
def test_joint_runtime_policy_does_not_gate_standalone_reader(monkeypatch,supported):
    s=F['context'].__wrapped__(monkeypatch)
    monkeypatch.setattr(C,'_SUPPORTED_JOINT_RUNTIME',supported)
    assert F['evaluate'](s).status==('VERIFIED_EVALUATION' if supported else 'DENIED')
    assert s.lookup.call_count==(1 if supported else 0)
    assert s.reader.read(s.fg,s.fs).status=='VERIFIED_RECORD_SET'
    assert not s.reader._state.joint_records
