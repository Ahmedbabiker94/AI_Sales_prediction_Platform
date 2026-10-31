"""Owned-process interruption probes. No model/native execution or database."""
import dis
import inspect
import os
import signal
import sys
import runpy
from concurrent.futures import ThreadPoolExecutor
import pytest
from src.services.canonical_forecast_read_service import ForecastReadDenied
F=runpy.run_path('tests/runtime/test_stored_forecast_accuracy.py')

EDGES=('before_registration','record_owned','registered','issuer_return','caller_store','caller_assignment','scope_enter_return',
       'read_dispatch','callback','result','final_validation','close_enter','close_invalidate','abort','abort_return')


def interrupt_at(s, edge, kind, evaluate):
    state=s.reader._state
    issue=state.issue_joint.__func__.__code__
    service=s.service.evaluate.__func__.__code__
    execute=s.service._evaluate.__func__.__code__
    require=s.service._require.__func__.__code__
    read=s.reader.read.__func__.__code__
    score=s.service._score.__func__.__code__
    # Line lookup keeps the fault attached to the intended operation.
    source,start=inspect.getsourcelines(state.issue_joint)
    markers={
      'before_registration':'h = JointReadCheckpoint()',
      'record_owned':'self.joint_records[id(h)] = _lifetime.record',
      'registered':'return h',
    }
    lines={k:start+next(i for i,x in enumerate(source) if v in x) for k,v in markers.items()}
    store_offset=next(i.offset for i in dis.get_instructions(service) if i.opname=="STORE_FAST" and i.argval=="checkpoint")
    hit=False;seen=0;observed=[]
    def trace(frame,event,arg):
        nonlocal hit,seen
        if hit:return trace
        name=frame.f_code.co_name
        if frame.f_code is service:frame.f_trace_opcodes=True
        match=(edge in lines and frame.f_code.co_filename == issue.co_filename and event=='line' and frame.f_lineno==lines[edge])
        match |= edge=='issuer_return' and frame.f_code is issue and event=='return'
        match |= edge=='caller_store' and frame.f_code is service and event=='opcode' and frame.f_lasti==store_offset
        match |= edge=='scope_enter_return' and name=='__enter__' and frame.f_globals.get('__name__')=='src.services.canonical_forecast_read_service' and event=='return'
        match |= edge=='caller_assignment' and frame.f_code is execute and event=='call'
        match |= edge=='read_dispatch' and frame.f_code is read and event=='call'
        match |= edge=='callback' and frame.f_code is require and event=='call'
        match |= edge=='result' and frame.f_code is score and event=='return'
        if edge=='final_validation' and frame.f_code is require and event=='call':
            seen+=1;match=seen==2
        match |= edge=='close_enter' and name=='__exit__' and frame.f_globals.get('__name__')=='src.services.canonical_forecast_read_service' and event=='call'
        match |= edge=='close_invalidate' and name=='abort_joint' and event=='call'
        match |= edge=='abort' and name=='abort_joint' and event=='line'
        match |= edge=='abort_return' and name=='abort_joint' and event=='return'
        if match:
            hit=True;observed.extend(state.joint_records.values());sys.settrace(None)
            if kind=='SIGINT':os.kill(os.getpid(),signal.SIGINT)
            elif kind=='SystemExit':raise SystemExit(73)
            else:raise RuntimeError('injected lifecycle failure')
        return trace
    previous=sys.gettrace();out=None;propagated=False
    try:
        sys.settrace(trace)
        out=evaluate()
    except (KeyboardInterrupt,SystemExit):propagated=True
    finally:sys.settrace(previous)
    assert hit
    if kind!='RuntimeError':assert propagated and out is None
    else:assert out is None or out.status!='VERIFIED_EVALUATION'
    # A traceback retained by an interrupt may delay physical weak-entry pruning.
    # It cannot keep the owning activation on the live stack or authorize use.
    for r in observed+list(state.joint_records.values()):
        with pytest.raises(ForecastReadDenied):state.check_joint(r.handle,s.fg,s.fs)
    assert not state.joint_records
    if edge in ('before_registration','record_owned','registered','issuer_return','caller_store','caller_assignment','scope_enter_return','read_dispatch','callback'):
        assert s.lookup.call_count==0
    return {'edge':edge,'kind':kind,'propagated':propagated,'lookups':s.lookup.call_count,'active_records':0}

@pytest.mark.parametrize('edge',EDGES)
@pytest.mark.parametrize('kind',['SIGINT','SystemExit','RuntimeError'])
def test_issuance_and_complete_lifetime_interruption(monkeypatch,edge,kind):
    s=F['context'].__wrapped__(monkeypatch)
    interrupt_at(s,edge,kind,lambda:F['evaluate'](s))


def test_pending_duplicate_and_terminal_ownership(monkeypatch):
    s=F['context'].__wrapped__(monkeypatch);state=s.reader._state
    def issue(owner):return state.issue_joint(s.service._joint_issuer,s.fg,s.fs.canonical_json,s.lg,s.ls.canonical_json,_lifetime=owner)
    owner=state.joint_scope(s.service._joint_issuer)
    with pytest.raises(ForecastReadDenied):issue(owner)
    with owner:
        h=issue(owner)
        with pytest.raises(ForecastReadDenied):issue(owner)
        assert state.check_joint(h,s.fg,s.fs).handle is h
        state.close_joint(h);state.close_joint(h);state.abort_joint(owner);state.abort_joint(owner)
        with pytest.raises(ForecastReadDenied):issue(owner)
    with pytest.raises(ForecastReadDenied):owner.__enter__()
    with pytest.raises(ForecastReadDenied):state.check_joint(h,s.fg,s.fs)
    assert not state.joint_records


def test_nested_and_parallel_scopes_leave_no_records(monkeypatch):
    s=F['context'].__wrapped__(monkeypatch);inside=False
    def callback():
        nonlocal inside
        if not inside:
            inside=True
            assert F['evaluate'](s).status=='VERIFIED_EVALUATION'
        return False
    assert F['evaluate'](s,cancelled=callback).status=='VERIFIED_EVALUATION'
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(x.status=='VERIFIED_EVALUATION' for x in pool.map(lambda _:F['evaluate'](s),range(12)))
    assert not s.reader._state.joint_records

@pytest.mark.parametrize('kind',['SIGINT','SystemExit'])
def test_interrupted_exit_caught_in_same_frame_cannot_reuse(monkeypatch,kind):
    s=F['context'].__wrapped__(monkeypatch);state=s.reader._state
    owner=state.joint_scope(s.service._joint_issuer);injected=False;h=None
    def trace(frame,event,arg):
        nonlocal injected
        if frame.f_code is owner.__exit__.__func__.__code__ and event=='call' and not injected:
            injected=True;sys.settrace(None)
            if kind=='SIGINT':os.kill(os.getpid(),signal.SIGINT)
            else:raise SystemExit(73)
        return trace
    previous=sys.gettrace()
    try:
        sys.settrace(trace)
        with owner:
            h=state.issue_joint(s.service._joint_issuer,s.fg,s.fs.canonical_json,s.lg,s.ls.canonical_json,_lifetime=owner)
            assert state.check_joint(h,s.fg,s.fs).handle is h
    except (KeyboardInterrupt,SystemExit):pass
    finally:sys.settrace(previous)
    assert injected and h is not None
    # The owner variable AND caller frame still exist, but the protected with
    # body has ended. Merely retaining a frame/traceback does not keep it live.
    with pytest.raises(ForecastReadDenied):state.check_joint(h,s.fg,s.fs)
    assert not state.joint_records and s.lookup.call_count==0

@pytest.mark.parametrize('kind',['SIGINT','SystemExit'])
def test_interrupted_close_after_owner_drop_prunes_retained_record(monkeypatch,kind):
    s=F['context'].__wrapped__(monkeypatch);state=s.reader._state;hit=False
    source,start=inspect.getsourcelines(state.close_joint)
    line=start+next(i for i,x in enumerate(source) if 'del self.joint_records' in x)
    def trace(frame,event,arg):
        nonlocal hit
        if frame.f_code.co_filename == state.close_joint.__func__.__code__.co_filename and event=='line' and frame.f_lineno==line:
            hit=True;sys.settrace(None)
            if kind=='SIGINT':os.kill(os.getpid(),signal.SIGINT)
            else:raise SystemExit(73)
        return trace
    with state.joint_scope(s.service._joint_issuer) as owner:
        h=state.issue_joint(s.service._joint_issuer,s.fg,s.fs.canonical_json,s.lg,s.ls.canonical_json,_lifetime=owner)
        # Retain the record deliberately, so weakref collection cannot mask it.
        original=state.check_joint(h,s.fg,s.fs)
        previous=sys.gettrace()
        try:
            sys.settrace(trace);state.close_joint(h)
        except (KeyboardInterrupt,SystemExit):pass
        finally:sys.settrace(previous)
        assert hit and original.handle is h
        with pytest.raises(ForecastReadDenied):state.check_joint(h,s.fg,s.fs)
        assert not state.joint_records
    assert s.lookup.call_count==0
