"""Metadata prototype tests; no driver, provider, host grant or DB proof."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import os,signal,sys
import pytest
from src.providers._forecast_read_order import _Order,OrderDenied
@pytest.mark.parametrize('kind',['I','S','P_READ','P_EVAL'])
def test_before_quarantine_and_terminal_order(kind):
    order=_Order();h=object();node=order.commit(kind,h);order.quarantine()
    assert node.event.identity is h
    assert [x[1] for x in order.snapshot()]==[kind,'Q']
    with pytest.raises(OrderDenied):order.commit(kind,object())
    assert [x[1] for x in order.snapshot()]==[kind,'Q']
@pytest.mark.parametrize('kind',['I','S','P_READ','P_EVAL'])
def test_stale_sample_is_not_commit_authority(kind):
    order=_Order();assert not order.terminal();order.quarantine()
    with pytest.raises(OrderDenied):order.commit(kind,object())
@pytest.mark.parametrize('kind',['S','P_READ','P_EVAL'])
def test_six_concurrent_commits_and_q(kind):
    order=_Order();barrier=Barrier(7)
    def request(i):
        barrier.wait(timeout=2)
        if i==6:order.quarantine();return
        try:order.commit(kind,object())
        except OrderDenied:pass
    with ThreadPoolExecutor(max_workers=7) as pool:list(pool.map(request,range(7)))
    rows=order.snapshot();assert rows[-1][1]=='Q'
    assert len(rows)<=7 and sum(x[1]=='Q' for x in rows)==1
@pytest.mark.parametrize('kind',['S','P_READ','P_EVAL','Q'])
@pytest.mark.parametrize('edge',['c_call','c_return'])
@pytest.mark.parametrize('exception',['SIGINT','SystemExit'])
def test_native_insertion_interrupt(kind,edge,exception):
    order=_Order();hit=False
    def profile(frame,profile_event,arg):
        nonlocal hit
        if frame.f_code is order._append.__func__.__code__ and profile_event==edge and getattr(arg,'__name__',None)=='setdefault' and getattr(arg,'__self__',None) is not order._intent:
            hit=True;sys.setprofile(None)
            if exception=='SIGINT':os.kill(os.getpid(),signal.SIGINT)
            else:raise SystemExit(73)
    try:
        sys.setprofile(profile)
        with pytest.raises((KeyboardInterrupt,SystemExit)):
            if kind=='Q':order.quarantine()
            else:order.commit(kind,object())
    finally:sys.setprofile(None)
    assert hit
    order.quarantine()
    rows=order.snapshot();assert rows[-1][1]=='Q'
    assert [x[1] for x in rows]==(['Q'] if kind=='Q' or edge=='c_call' else [kind,'Q'])
    with pytest.raises(OrderDenied):order.commit('P_READ',object())

@pytest.mark.parametrize('edge',['c_call','c_return'])
@pytest.mark.parametrize('exception',['SIGINT','SystemExit'])
def test_quarantine_intent_cannot_be_lost(edge,exception):
    order=_Order();hit=False
    def profile(frame,profile_event,arg):
        nonlocal hit
        if profile_event==edge and getattr(arg,'__name__',None)=='setdefault' and getattr(arg,'__self__',None) is order._intent:
            hit=True;sys.setprofile(None)
            if exception=='SIGINT':os.kill(os.getpid(),signal.SIGINT)
            else:raise SystemExit(73)
    try:
        sys.setprofile(profile)
        with pytest.raises((KeyboardInterrupt,SystemExit)):order.quarantine()
    finally:sys.setprofile(None)
    assert hit
    if edge=='c_return':
        with pytest.raises(OrderDenied):order.commit('S',object())
        assert order.snapshot()[0][1]=='Q'
    else:
        # No Q or intent published; the interrupt is not a fictitious Q event.
        assert order.snapshot()==()
    order.quarantine()
    with pytest.raises(OrderDenied):order.commit('P_EVAL',object())

def test_bounded_journal_fails_stop_not_reset(monkeypatch):
    from src.providers import _forecast_read_order as O
    monkeypatch.setattr(O,'_MAX_RECORDS',3)
    order=_Order();order.commit('S',object());order.commit('P_READ',object())
    with pytest.raises(OrderDenied):order.commit('S',object())
    assert [r[1] for r in order.snapshot()]==['S','P_READ','Q']
    assert order.terminal()

def test_concurrent_quarantine_publishers_have_one_terminal_point():
    order=_Order();barrier=Barrier(8)
    def request(_):barrier.wait(timeout=2);order.quarantine()
    with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(request,range(8)))
    assert [r[1] for r in order.snapshot()]==['Q']
