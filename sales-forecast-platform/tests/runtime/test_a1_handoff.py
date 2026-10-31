"""A1 original-operation controls. Mocked storage unless postgres=True child."""
from pathlib import Path
from types import SimpleNamespace
from threading import Thread,Event,Barrier
from unittest.mock import patch
import sys,os,signal,runpy,json,subprocess
import pytest
from src.providers import _forecast_read_order as O
from src.providers.forecast_read_provider import ForecastReadProvider

MODES=['q_before_s','s_q_c','q_read_p','q_eval_p','read_p_q_return','eval_p_q_return','duplicate_foreign','claim_SIGINT','claim_SystemExit','consume_SIGINT','consume_SystemExit','pub_SIGINT','pub_SystemExit','six_claims_then_q','metrics_A','metrics_B','expired_claim','origin_tamper','claim_before_SIGINT','claim_before_SystemExit','consume_before_SIGINT','consume_before_SystemExit','pub_before_SIGINT','pub_before_SystemExit','close_SIGINT','close_SystemExit','q_checkpoint_activation','checkpoint_SIGINT','checkpoint_SystemExit','race_s_q']

def scenario(mode,postgres=False):
    if postgres:
        sys.path.insert(0,str(Path('tests/postgres').resolve()));F=runpy.run_path('tests/postgres/test_stored_forecast_accuracy_postgres.py');s=F['setup'](values=('501','651','501','651') if mode=='metrics_B' else ('500','650','500','650'))
    else:
        F=runpy.run_path('tests/runtime/test_stored_forecast_accuracy.py');b=F['make_batch']();s=F['setup_context'](b,(F['snapshot'](b,values=('501','651','501','651') if mode=='metrics_B' else ('500','650','500','650')),))
    opened=[];sql=[];calls=[];claim_handles=[];events=[]
    if postgres:
        from src.database import forecast_read_connection as connections
        real=connections.driver()
        class Cursor(real.extensions.cursor):
            def execute(self,q,*a,**kw):sql.append(q);return super().execute(q,*a,**kw)
        class Connection(real.extensions.connection):
            def cursor(self,*a,**kw):kw['cursor_factory']=Cursor;return super().cursor(*a,**kw)
        def connect(**kw):
            assert kw['user']=='r7_forecast_reader';events.append(('C',O._ORDER.terminal()))
            c=real.connect(**kw,connection_factory=Connection);opened.append(c);return c
        connections.driver=lambda:SimpleNamespace(connect=connect)
        real_lookup=ForecastReadProvider.lookup
    else:
        def real_lookup(provider,*a):events.append(('C',O._ORDER.terminal()));return F['stored'](b)
    claimed=ForecastReadProvider.lookup_claimed
    def claim_capture(provider,identity,handle):
        claim_handles.append((provider,identity,handle))
        if mode=='expired_claim':
            deadline=O._CLAIMS[id(handle)].deadline
            with patch.object(O,'monotonic',lambda:deadline):return claimed(provider,identity,handle)
        return claimed(provider,identity,handle)
    def lookup(provider,*a):
        calls.append(True)
        if mode=='duplicate_foreign':
            from dataclasses import replace
            from uuid import uuid4
            from copy import copy
            foreign=ForecastReadProvider(replace(provider.repository,scope=provider.repository.scope.model_copy(update={'dataset_id':uuid4()})))
            with pytest.raises(O.OrderDenied):claimed(foreign,a[0],claim_handles[-1][2])
            with pytest.raises(TypeError):copy(claim_handles[-1][2])
            for args in [(provider,a[0],O._Claim()),(provider,'0'*64,claim_handles[-1][2])]:
                with pytest.raises(O.OrderDenied):claimed(*args)
            with pytest.raises(O.OrderDenied):claimed(*claim_handles[-1])
        return real_lookup(provider,*a)
    def q():O._ORDER.quarantine();events.append(('Q',True))
    hit=False;captured=None;propagated=False
    def trigger():
        nonlocal hit
        hit=True;sys.settrace(None);sys.setprofile(None)
        if mode.endswith('SIGINT'):os.kill(os.getpid(),signal.SIGINT)
        elif mode.endswith('SystemExit'):raise SystemExit(73)
        else:q()
    def trace(frame,trace_event,arg):
        if hit:return trace
        if mode.startswith('close_') and trace_event=='call' and frame.f_code is O._Operation.__exit__.__code__:trigger()
        if mode=='q_before_s' and trace_event=='call' and frame.f_code is O._Operation.admit.__code__:trigger()
        if mode=='s_q_c' and trace_event=='call' and frame.f_code is O._consume_claim.__code__:trigger()
        if mode in ('q_read_p','q_eval_p') and trace_event=='call' and frame.f_code is O._Operation.publish.__code__ and frame.f_locals['self'].kind==('READ' if mode=='q_read_p' else 'EVAL'):trigger()
        return trace
    def profile(frame,profile_event,arg):
        if hit:return
        if profile_event==('c_call' if '_before_' in mode or mode=='q_checkpoint_activation' else 'c_return') and getattr(arg,'__name__',None)=='setdefault':
            if frame.f_code is O._Order._append.__code__:
                kind=frame.f_locals['proposal'].event.kind
                target=('I' if mode.startswith('checkpoint_') or mode=='q_checkpoint_activation' else 'S' if mode.startswith('claim_') else 'P_READ' if mode.startswith('pub_') or mode=='read_p_q_return' else 'P_EVAL' if mode=='eval_p_q_return' else None)
                if kind==target and getattr(arg,'__self__',None) is not O._ORDER._intent:trigger()
            if mode.startswith('consume_') and frame.f_code is O._consume_claim.__code__ and getattr(arg,'__self__',None) is frame.f_locals['record'].use:trigger()
    with patch.object(ForecastReadProvider,'lookup',lookup),patch.object(ForecastReadProvider,'lookup_claimed',claim_capture):
        if mode=='origin_tamper':
            from copy import copy
            real_origin=s.reader._state._operation_origin
            for original in [real_origin,s.service._publication_origin]:
                with pytest.raises(TypeError):copy(original)
            with pytest.raises(O.OrderDenied):O._operation(O._Origin(),s.fs,s.fg)
            with pytest.raises(O.OrderDenied):O._register_origin(object(),'READ',s.reader._state.provider,s.reader._state,None,None)
            with pytest.raises(O.OrderDenied):
                with O._operation(real_origin,s.fs,s.fg):pass
        if mode=='race_s_q':
            barrier=Barrier(7);outcomes=[];errors=[]
            def request():
                try:
                    barrier.wait(timeout=3);outcomes.append(s.reader.read(s.fg,s.fs))
                except BaseException as exc:errors.append(repr(exc))
            ts=[Thread(target=request,daemon=True) for _ in range(6)]
            for t in ts:t.start()
            barrier.wait(timeout=3);q()
            for t in ts:t.join(5)
            assert not errors and not any(t.is_alive() for t in ts) and len(outcomes)==6
            assert all(o.status in ('DENIED','VERIFIED_RECORD_SET') for o in outcomes)
            outcome=outcomes[0];hit=True
        elif mode=='six_claims_then_q':
            barrier=Barrier(7);release=Event();outcomes=[];errors=[]
            def held(provider,identity,handle):
                claim_handles.append((provider,identity,handle));barrier.wait(timeout=3);assert release.wait(3);return claimed(provider,identity,handle)
            def request():
                try:outcomes.append(s.reader.read(s.fg,s.fs))
                except BaseException as exc:errors.append(repr(exc))
            with patch.object(ForecastReadProvider,'lookup_claimed',held):
                ts=[Thread(target=request,daemon=True) for _ in range(6)]
                for t in ts:t.start()
                try:barrier.wait(timeout=3);q()
                finally:release.set()
                for t in ts:t.join(4)
                assert not any(t.is_alive() for t in ts) and not errors
                assert len(calls)==6 and all(o.status=='DENIED' for o in outcomes)
            outcome=outcomes[0];hit=True
        else:
            try:
                sys.settrace(trace);sys.setprofile(profile)
                outcome=F['evaluate'](s) if mode in ('q_eval_p','eval_p_q_return','metrics_A','metrics_B','q_checkpoint_activation','checkpoint_SIGINT','checkpoint_SystemExit') else s.reader.read(s.fg,s.fs)
            except (KeyboardInterrupt,SystemExit):propagated=True;outcome=None
            finally:sys.settrace(None);sys.setprofile(None)
        rows=O._ORDER.snapshot();kinds=[r[1] for r in rows];S=[r[0] for r in rows if r[1]=='S'];Q=[r[0] for r in rows if r[1]=='Q'];P=[r[0] for r in rows if r[1].startswith('P_')]
        if mode in ('duplicate_foreign','origin_tamper'):assert outcome.status=='VERIFIED_RECORD_SET' and len(calls)==1
        elif mode in ('metrics_A','metrics_B'):
            assert outcome.status=='VERIFIED_EVALUATION' and len(calls)==1
            e=outcome.evidence
            assert [p.prediction_float32_le for p in e.pairs]==['2d56f943','e9542244']*2
            expected=(1.0000152587890625,1.0520395551960913,-1.0000152587890625) if mode=='metrics_A' else (2.0000152587890625,2.026528495549795,-2.0000152587890625)
            assert tuple(getattr(e,n) for n in ('mae','rmse','bias'))==expected
        elif mode=='race_s_q':
            assert len(Q)==1 and len(calls)<=len(S)<=6
            assert all(start<Q[0] for start in S) and all(p<Q[0] for p in P)
        elif mode=='expired_claim':assert outcome.status=='DENIED' and len(S)==1 and not calls
        elif mode.endswith(('SIGINT','SystemExit')):assert hit and propagated
        elif mode in ('read_p_q_return','eval_p_q_return'):
            assert hit and outcome.status==('VERIFIED_RECORD_SET' if mode=='read_p_q_return' else 'VERIFIED_EVALUATION');assert max(P)<Q[0]
        else:
            assert hit and outcome.status=='DENIED'
            assert all(p<Q[0] for p in P)
        if mode.startswith('checkpoint_') or mode=='q_checkpoint_activation':
            assert not S and not calls and not opened
            for retained in s.reader._state.joint_records.values():
                assert retained.lifetime() is None or retained.lifetime().closed
                with pytest.raises(Exception):s.reader._state.check_joint(retained.handle,s.fg,s.fs)
                if mode=='q_checkpoint_activation':assert not O._issued_original(retained.handle)
        if mode=='q_before_s':assert not S and not calls and not opened
        if mode=='s_q_c':assert len(calls)==1 and S[0]<Q[0] and ('C',True) in events
        if mode.startswith(('claim_','consume_')):assert not calls and not opened
        if mode=='q_read_p':assert 'P_READ' not in kinds
        if mode=='q_eval_p':assert 'P_EVAL' not in kinds
        if mode in ('pub_SIGINT','pub_SystemExit'):assert 'P_READ' in kinds
        if mode.startswith('pub_before_'):assert 'P_READ' not in kinds
        if mode.startswith('claim_before_'):assert not S
        for index,kind,record in rows:
            if kind.startswith('P_'):
                assert record.kind==kind
                assert record.origin is (s.reader._state._operation_origin if kind=='P_READ' else s.service._publication_origin)
                assert record.selection_json==s.fs.canonical_json
                assert record.grants[0] is s.fg
                if kind=='P_EVAL':assert record.grants[1] is s.lg
                if outcome is not None and outcome.status.startswith('VERIFIED') and kind==('P_EVAL' if outcome.status=='VERIFIED_EVALUATION' else 'P_READ'):
                    import hashlib
                    assert record.result_sha256==hashlib.sha256(outcome.canonical_json.encode()).hexdigest()
        assert all(c.closed for c in opened)
        assert all(q.split()[0] in ('SELECT','SHOW') for q in sql)
        # All private claims die with their original owning scope, regardless of
        # whether the caller's local handle assignment completed.
        for args in claim_handles:
            with pytest.raises(O.OrderDenied):claimed(*args)
        if Q:
            before=len(calls)
            assert s.reader.read(s.fg,s.fs).status=='DENIED'
            with pytest.raises(Exception):s.fa.authorize(s.op,s.fp,s.fs)
            assert len(calls)==before
        print('A1_PROOF',json.dumps(dict(mode=mode,postgres=postgres,runtime=sys.version.split()[0],S=S,Q=Q,P=P,events=events,result=outcome.status if outcome else None,propagated=propagated,lookups=len(calls),connections=len(opened),all_closed=True)))

@pytest.mark.parametrize('mode',MODES)
def test_original_owned_A1_order(mode):
    p=subprocess.run([sys.executable,'-B',__file__,mode],capture_output=True,text=True,timeout=20,start_new_session=True)
    assert p.returncode==0,(p.stdout,p.stderr)
    print(p.stdout)
if __name__=='__main__':scenario(sys.argv[1],len(sys.argv)>2 and sys.argv[2]=='postgres')
