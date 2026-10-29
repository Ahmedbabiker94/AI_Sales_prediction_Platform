"""Contract-only: original local registries with STUB B3 returns, never native proof."""
import copy
import json
import os
from pathlib import Path
import runpy
import socket
import struct
import threading
import time
from unittest.mock import Mock
import pytest
from src.handoff.protocol import Channel, HandoffError, MAX_FRAME, canonical, decode, digest, frame, validate
from src.handoff.content import inspect_proposal
from src.handoff.worker_export import _ProtectedExport

BASE=runpy.run_path('tests/runtime/test_forecast_write_intent.py')
HOST=runpy.run_path('tests/native/run_native_handoff.py')

@pytest.fixture(scope='module')
def prepared(tmp_path_factory):
    g=BASE['prepared'].__wrapped__(tmp_path_factory); value=next(g)
    yield value
    next(g,None)

@pytest.fixture
def context(prepared,monkeypatch):
    return BASE['make'].__wrapped__(prepared,monkeypatch)()

CTX=dict(session='a'*64,container='b'*64,source='c'*64,challenge='d'*64)


def exporter(s,**kw):
    return _ProtectedExport(s.write,s.write_operator,s.receipt,s.reference,s.native,s.inputs,s.request,CTX,
        idempotency='e'*64,deadline=150.,**kw)


def body(raw):return decode(raw[4:])['body']


def reserved(e):
    proposal=body(e.proposal()); a=dict(proposal=digest(proposal),approval='f'*64)
    r=body(e.reserve(a)); challenge={k:r[k] for k in ('proposal','approval','reservation')}
    return proposal,r,dict(challenge,final_challenge='1'*64)


def test_original_consumption_and_exact_projection(context):
    s=context;e=exporter(s);p,r,c=reserved(e)
    assert e._consumed is not None and e._record is s.write._grants[id(e._grant)]
    assert e._record.receipt is s.receipt and e._record.reference is s.reference
    assert e._record.producer_record is s.producer._issued[id(s.receipt)]
    records,batch=inspect_proposal(p)
    assert [x.float32_le for x in records.rows]==[x.float32_le for x in batch.rows]
    assert [x.logical_key_id for x in records.rows]==[x.key.logical_key_id for x in batch.rows]
    release=body(e.release(c));assert release['grant']==r['grant'] and e.state=='FINAL_RELEASE'
    with pytest.raises(HandoffError):e.release(c)
    with pytest.raises(ValueError):s.write.simulate_consume(e._grant,deadline=150.)

@pytest.mark.parametrize('field',['receipt','reference','native','inputs'])
def test_copied_original_identity_rejected(context,field):
    s=context
    original=getattr(s,field)
    if field in ('receipt','inputs'):
        with pytest.raises(TypeError):copy.copy(original)
        substitute=type(original)() if field=='receipt' else type(original)(original.binding,original.output)
    else:substitute=copy.copy(original)
    setattr(s,field,substitute)
    with pytest.raises(ValueError):exporter(s).proposal()
    assert not s.write._grants

@pytest.mark.parametrize('stage',['proposal','reserve','release'])
@pytest.mark.parametrize('component',['producer','owner','issuer'])
def test_revocation_each_worker_boundary(context,stage,component):
    s=context;e=exporter(s)
    if stage=='release':p,r,c=reserved(e)
    elif stage=='reserve':p=body(e.proposal())
    if component=='producer':s.producer.revoke(s.operator,s.receipt)
    elif component=='owner':s.control.update(owner=False)
    else:s.write.invalidate(s.write_operator)
    with pytest.raises(ValueError):
        if stage=='proposal':e.proposal()
        elif stage=='reserve':e.reserve(dict(proposal=digest(p),approval='f'*64))
        else:e.release(c)
    assert e.state=='REJECTED'

@pytest.mark.parametrize('edge',['cancel','wall','elapsed','producer_wall','producer_elapsed'])
def test_final_callbacks_revoke_no_release(context,edge):
    s=context;e=exporter(s);p,r,c=reserved(e)
    funcs={'cancel':lambda:False,'wall':s.write._wall,'elapsed':s.write._elapsed,
           'producer_wall':s.producer._wall,'producer_elapsed':s.producer._elapsed}
    # Any synchronous callback after reservation must be followed by final live checks.
    def revoke():
        s.write.revoke(s.write_operator,e._grant)
        return funcs[edge]()
    if edge=='cancel':e._cancelled=revoke
    elif edge.startswith('producer_'):setattr(s.producer,'_'+edge.removeprefix('producer_'),revoke)
    else:setattr(s.write,'_'+edge,revoke)
    with pytest.raises(ValueError):e.release(c)
    assert e.state=='REJECTED'

@pytest.mark.parametrize('fault',['approval','proposal','foreign_challenge','expired','cancelled','foreign_issuer'])
def test_fail_closed(context,fault):
    s=context;e=exporter(s)
    if fault in ('approval','proposal'):
        p=body(e.proposal());a=dict(proposal=digest(p),approval='f'*64);a[fault]='bad'
        with pytest.raises(ValueError):e.reserve(a)
        assert not s.write._grants
    elif fault=='foreign_issuer':
        with pytest.raises(ValueError):
            _ProtectedExport(object(),s.write_operator,s.receipt,s.reference,s.native,s.inputs,s.request,CTX,idempotency='e'*64,deadline=150.)
    else:
        p,r,c=reserved(e)
        if fault=='foreign_challenge':c['proposal']='0'*64
        elif fault=='expired':s.control['wall']=150.
        else:e._cancelled=lambda:True
        with pytest.raises(ValueError):e.release(c)
        assert e._record.state=='CONSUMED'

@pytest.mark.parametrize('fault',['reorder','bits','projection','idempotency','digest'])
def test_projection_tampering(context,fault):
    e=exporter(context);p=body(e.proposal())
    if fault=='projection':p['projection']=p['projection'].replace('INITIAL_WRITE_ONLY','CORRECTION')
    else:
        r=json.loads(p['records'])
        if fault=='reorder':r['rows'].reverse()
        if fault=='bits':r['rows'][0]['float32_le']='00000000'
        if fault=='idempotency':r['idempotency_id']='f'*64
        if fault=='digest':r['row_set_sha256']='f'*64
        p['records']=canonical(r)
    with pytest.raises(ValueError):inspect_proposal(p)

@pytest.mark.parametrize('field',['version','purpose','kind','direction','sequence','session','container','source','challenge','extra'])
def test_strict_frame_substitution(field):
    value=decode(frame('APPROVAL',CTX,dict(proposal='e'*64,approval='f'*64))[4:])
    value[field]=0 if field=='sequence' else 'foreign'
    with pytest.raises(ValueError):validate(value,'APPROVAL',CTX)

@pytest.mark.parametrize('raw',[b'{',b'{"x":1,"x":2}',b'{"x":NaN}',b'\xff'])
def test_invalid_json(raw):
    with pytest.raises(ValueError):decode(raw)

@pytest.mark.parametrize('raw',[struct.pack('>I',MAX_FRAME+1),struct.pack('>I',10)+b'x',b'pytest native success\n'])
def test_truncated_oversized_or_pytest_stdout(raw):
    a,b=socket.socketpair()
    try:
        a.sendall(raw);a.shutdown(socket.SHUT_WR)
        c=Channel(b.fileno(),b.fileno(),deadline=time.monotonic()+.3)
        with pytest.raises(ValueError):c.receive('APPROVAL',CTX)
    finally:a.close();b.close()


def test_partial_frame_and_concurrent_diagnostic_drain():
    a,b=socket.socketpair();r,w=os.pipe()
    raw=frame('APPROVAL',CTX,dict(proposal='e'*64,approval='f'*64))
    def writer():
        os.write(w,b'x'*32000);os.close(w)
        for i in range(0,len(raw),7):a.sendall(raw[i:i+7])
    t=threading.Thread(target=writer);t.start()
    try:
        c=Channel(b.fileno(),b.fileno(),stderr=r,deadline=time.monotonic()+2)
        assert c.receive('APPROVAL',CTX)['approval']=='f'*64
    finally:t.join(timeout=2);a.close();b.close();os.close(r)
    assert not t.is_alive()


def test_backpressure_and_cancellation_bounded():
    a,b=socket.socketpair()
    try:
        c=Channel(a.fileno(),a.fileno(),deadline=time.monotonic()+.1)
        start=time.monotonic()
        with pytest.raises(ValueError):c.send(b'x'*MAX_FRAME)
        assert time.monotonic()-start<1
    finally:a.close();b.close()


def test_disabled_without_docker(monkeypatch):
    spy=Mock(side_effect=AssertionError('no Docker'))
    monkeypatch.setattr(HOST['P0'],'validate_recovery',spy)
    with pytest.raises(ValueError):HOST['run']()
    with pytest.raises(ValueError):HOST['run'](allow_native_handoff=True,approve=lambda *_:True)
    spy.assert_not_called()

@pytest.mark.parametrize('fault',['foreign_channel','duplicate','late','cancel','bits','session'])
def test_host_admission_original_channel_one_shot(context,fault):
    p,r,c=reserved(exporter(context)); channel=object()
    clock={'now':100.,'elapsed':0.,'cancel':False}
    host=HOST['_HostAdmission'](CTX,channel,deadline=150.,idempotency='e'*64,approve=lambda *_:True,
        cancelled=lambda:clock['cancel'],wall=lambda:clock['now'],elapsed=lambda:clock['elapsed'])
    host.state='WORKER_VERIFIED';approval=host.proposal(p,channel)
    r.update(approval=approval['approval']);challenge=host.reserved(r,channel)
    release=dict(challenge,grant=r['grant'],expires=150.,release=digest([CTX,challenge,r['grant']]))
    if fault=='foreign_channel':channel=object()
    elif fault=='late':clock['now']=150.
    elif fault=='cancel':clock['cancel']=True
    elif fault=='bits':release['proposal']='0'*64
    elif fault=='session':release['release']='0'*64
    if fault=='duplicate':
        assert host.admit(release,channel)['state']=='VALIDATED_NO_WRITE'
    with pytest.raises(ValueError):host.admit(release,channel)

@pytest.mark.parametrize('edge',['cancel','wall','elapsed'])
def test_host_final_callback_invalidation(context,edge):
    p,r,c=reserved(exporter(context)); channel=object()
    host=HOST['_HostAdmission'](CTX,channel,deadline=150.,idempotency='e'*64,
        approve=lambda *_:True,cancelled=lambda:False,wall=lambda:100.,elapsed=lambda:0.)
    host.state='WORKER_VERIFIED';a=host.proposal(p,channel);r['approval']=a['approval']
    ch=host.reserved(r,channel);release=dict(ch,grant=r['grant'],expires=150.,release=digest([CTX,ch,r['grant']]))
    def revoke(value):host.active=False;return value
    if edge=='cancel':host.cancelled=lambda:revoke(False)
    elif edge=='wall':host.wall=lambda:revoke(100.)
    else:host.elapsed=lambda:revoke(0.)
    with pytest.raises(ValueError):host.admit(release,channel)
    assert host.state=='UNKNOWN'

@pytest.mark.parametrize('mode',['DENIED','UNAVAILABLE','exception'])
def test_failed_b3_has_no_exportable_receipt(prepared,monkeypatch,mode):
    original=BASE['BASE']['make'].__wrapped__(prepared,monkeypatch)
    s=original();s.control['mode']=mode
    with pytest.raises(ValueError):BASE['BASE']['issue'](s)
    assert not s.producer._issued
    assert s.authority._grants[id(s.cap)].state=='CONSUMED'

@pytest.mark.parametrize('fault',['create','existing','directory','symlink'])
def test_evidence_output_exclusive_and_no_symlink(tmp_path,fault):
    # Accepted output primitive reused for NEW evidence only; no database calls.
    code=runpy.run_path('tests/postgres/run_postgres.py')
    p=tmp_path/'proof'
    if fault=='create':
        code['write_evidence'](p,'new')
        assert p.read_text()=='new'
        with pytest.raises(FileExistsError):code['write_evidence'](p,'overwrite')
    elif fault=='symlink':
        target=tmp_path/'target';target.write_text('old');p.symlink_to(target)
        with pytest.raises(ValueError):code['write_evidence'](p,'new')
        assert target.read_text()=='old'
    elif fault=='directory':
        p.mkdir()
        with pytest.raises(OSError):code['write_evidence'](p,'new')
    else:
        p.write_text('old')
        with pytest.raises(FileExistsError):code['write_evidence'](p,'new')
        assert p.read_text()=='old'

@pytest.mark.parametrize('pending',[False,True])
def test_cleanup_ambiguity_never_false_success(monkeypatch,pending):
    o=HOST['OwnedContainer']();o.pending=pending
    # Deterministic daemon simulation: absence alone never clears pending.
    monkeypatch.setattr(o,'command',lambda *a,**kw: __import__('subprocess').CompletedProcess(a,1,b'',b'Error: No such object'))
    assert o.cleanup()==('CLEANUP_UNCONFIRMED' if pending else 'CLEANUP_SUCCESS')


def test_unrelated_container_is_never_removed(monkeypatch):
    o=HOST['OwnedContainer']();calls=[]
    def command(args,**kw):
        calls.append(args)
        return __import__('subprocess').CompletedProcess(args,0,json.dumps([{'Id':'f'*64,'Config':{'Labels':{'r7_handoff':'foreign'}}}]).encode(),b'')
    monkeypatch.setattr(o,'command',command)
    assert o.cleanup()=='CLEANUP_UNCONFIRMED'
    assert not any(x[:2]==['rm','-f'] for x in calls)


def test_late_container_reconciled_but_daemon_uncertainty_retained(monkeypatch):
    o=HOST['OwnedContainer']();o.pending=True;calls=[]
    values=[False,True,False]
    def command(args,**kw):
        calls.append(args)
        if args[0]=='rm':return __import__('subprocess').CompletedProcess(args,0,b'',b'')
        present=values.pop(0) if values else False
        if present:
            return __import__('subprocess').CompletedProcess(args,0,json.dumps([{'Id':'f'*64,'Config':{'Labels':{'r7_handoff':o.identity}}}]).encode(),b'')
        return __import__('subprocess').CompletedProcess(args,1,b'',b'Error: No such object')
    monkeypatch.setattr(o,'command',command)
    assert o.cleanup()=='CLEANUP_UNCONFIRMED'
    assert ['rm','-f','f'*64] in calls


def test_unique_invocation_resource_names():
    items=[HOST['OwnedContainer']() for _ in range(20)]
    assert len({o.name for o in items})==20


def test_failed_postreservation_consumption_never_releases(context):
    s=context
    def cancelled():return any(g.state=='CONSUMED' for g in s.write._grants.values())
    e=exporter(s,cancelled=cancelled);p=body(e.proposal())
    with pytest.raises(ValueError):e.reserve({'proposal':digest(p),'approval':'f'*64})
    assert any(g.state=='CONSUMED' for g in s.write._grants.values())
    assert e._consumed is None and e.state=='REJECTED'
    with pytest.raises(ValueError):e.release({})
    with pytest.raises(ValueError):s.write.simulate_consume(e._grant,deadline=150.)


def test_foreign_original_producer_receipt_rejected(prepared,monkeypatch):
    make=BASE['make'].__wrapped__(prepared,monkeypatch)
    a,b=make(),make()
    a.receipt=b.receipt
    with pytest.raises(ValueError):exporter(a).proposal()
    assert not a.write._grants


def test_source_special_file_symlink_and_bytecode_rejection(tmp_path):
    from src.handoff.source import source_identity
    (tmp_path/'src').mkdir();(tmp_path/'tests').mkdir()
    f=tmp_path/'src'/'module.py';f.write_text('pass\n')
    valid=source_identity(tmp_path);assert len(valid)==64
    f.write_text('changed\n');assert source_identity(tmp_path)!=valid
    os.mkfifo(tmp_path/'src'/'fifo')
    with pytest.raises(ValueError):source_identity(tmp_path)
    (tmp_path/'src'/'fifo').unlink()
    (tmp_path/'src'/'link').symlink_to(f)
    with pytest.raises(ValueError):source_identity(tmp_path)
    (tmp_path/'src'/'link').unlink()
    (tmp_path/'src'/'module.pyc').write_bytes(b'arbitrary bytecode')
    with pytest.raises(ValueError):source_identity(tmp_path)


def test_untrusted_image_command_or_manifest_not_public_arguments():
    for key in ('image','command','manifest','worker','channel','source','arguments'):
        with pytest.raises(TypeError):HOST['run'](**{key:'untrusted'})

@pytest.mark.parametrize('fault',['no_approval','expired','foreign_idempotency'])
def test_host_rejection_before_any_grant(context,fault):
    e=exporter(context);p=body(e.proposal());channel=object()
    host=HOST['_HostAdmission'](CTX,channel,deadline=150.,
        idempotency='f'*64 if fault=='foreign_idempotency' else 'e'*64,
        approve=lambda *_:fault!='no_approval',cancelled=lambda:False,
        wall=lambda:150. if fault=='expired' else 100.,elapsed=lambda:0.)
    host.state='WORKER_VERIFIED'
    with pytest.raises(ValueError):host.proposal(p,channel)
    assert not context.write._grants


def test_lost_ack_does_not_restore_admission(context):
    p,r,c=reserved(exporter(context));channel=object()
    host=HOST['_HostAdmission'](CTX,channel,deadline=150.,idempotency='e'*64,
        approve=lambda *_:True,cancelled=lambda:False,wall=lambda:100.,elapsed=lambda:0.)
    host.state='WORKER_VERIFIED';a=host.proposal(p,channel);r['approval']=a['approval']
    ch=host.reserved(r,channel);release=dict(ch,grant=r['grant'],expires=150.,release=digest([CTX,ch,r['grant']]))
    first=host.admit(release,channel)
    assert first['durable_receipt'] is None and first['writes_performed'] is False
    # Lost ACK / saved stdout cannot cause a second sink admission.
    with pytest.raises(ValueError):host.admit(release,channel)


def test_creation_attempt_registered_before_cli_timeout(monkeypatch):
    import subprocess
    o=HOST['OwnedContainer']()
    def timeout(*args,**kwargs):
        assert o.pending and o.events==['CREATE_PENDING']
        raise subprocess.TimeoutExpired('docker create',.75)
    monkeypatch.setattr(o,'command',timeout)
    with pytest.raises(subprocess.TimeoutExpired):o.create(['create','--name',o.name])
    assert o.pending


def test_owned_cli_process_group_terminated_and_reaped(monkeypatch):
    import subprocess
    o=HOST['OwnedContainer'](); process=Mock(pid=12345)
    process.communicate.side_effect=[subprocess.TimeoutExpired('docker',.75),(b'',b'')]
    process.poll.return_value=None
    kill=Mock()
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**kw:process)
    monkeypatch.setattr(os,'killpg',kill)
    with pytest.raises(subprocess.TimeoutExpired):o.command(['create','--name',o.name],timeout=.75)
    kill.assert_called_once_with(12345,__import__('signal').SIGKILL)
    assert process.communicate.call_count==2


def test_repeated_invalid_frames_do_not_leak_selector_descriptors():
    before=len(list(Path('/proc/self/fd').iterdir()))
    for _ in range(40):
        a,b=socket.socketpair()
        try:
            a.sendall(struct.pack('>I',MAX_FRAME+1))
            channel=Channel(b.fileno(),b.fileno(),deadline=time.monotonic()+.3)
            with pytest.raises(ValueError):channel.receive('APPROVAL',CTX)
        finally:a.close();b.close()
    assert len(list(Path('/proc/self/fd').iterdir()))==before


# Focused strict UTF-8 regressions. Bootstrap/broker harnesses below are
# NON-NATIVE protocol tests, not successful-origin or Docker-isolation proof.
_INVALID_WIRE_ENCODINGS = ('utf-16','utf-16-le','utf-16-be','utf-32','utf-32-le','utf-32-be','utf8-bom','invalid-utf8')


def encoded_wire(kind, encoding, context=CTX):
    if kind=='HELLO':payload={'idempotency':'e'*64,'deadline':time.time()+30}
    elif kind=='PROPOSAL':payload={'records':'{}','projection':'{}'}
    else:payload={'proposal':'e'*64,'approval':'f'*64}
    text=canonical(decode(frame(kind,context,payload)[4:]))
    if encoding=='utf8-bom':raw=b'\xef\xbb\xbf'+text.encode('utf-8')
    elif encoding=='invalid-utf8':raw=b'\xff'+text.encode('utf-8')
    else:raw=text.encode(encoding)
    return struct.pack('>I',len(raw))+raw


@pytest.mark.parametrize('encoding',_INVALID_WIRE_ENCODINGS)
def test_receiver_rejects_non_utf8_or_bom_before_message_validation(encoding):
    a,b=socket.socketpair()
    try:
        a.sendall(encoded_wire('APPROVAL',encoding));a.shutdown(socket.SHUT_WR)
        channel=Channel(b.fileno(),b.fileno(),deadline=time.monotonic()+1)
        with pytest.raises(HandoffError,match='^invalid encoding$'):channel.receive('APPROVAL',CTX)
    finally:a.close();b.close()


@pytest.mark.parametrize('text',['ASCII','مبيعات — 销售','inside-string-\ufeff-is-data'])
def test_canonical_utf8_and_multibyte_protocol_content(text):
    a,b=socket.socketpair()
    try:
        # These are valid framing-schema strings, not canonical forecast records
        # or successful provenance. Domain content still needs inspect_proposal.
        payload={'records':text,'projection':text}
        a.sendall(frame('PROPOSAL',CTX,payload))
        channel=Channel(b.fileno(),b.fileno(),deadline=time.monotonic()+1)
        assert channel.receive('PROPOSAL',CTX)==payload
    finally:a.close();b.close()


@pytest.mark.parametrize('raw',[b'\x80',b'\xc0\xaf',b'\xed\xa0\x80',b'\xf4\x90\x80\x80',b'\xe2\x82'])
def test_strict_utf8_never_replaces_or_guesses_invalid_sequences(raw):
    with pytest.raises(HandoffError,match='^invalid encoding$'):decode(raw)


@pytest.mark.parametrize('encoding',(*_INVALID_WIRE_ENCODINGS,'utf-8'))
def test_actual_worker_bootstrap_encoding_before_source_or_native_work(encoding):
    import subprocess
    import sys
    # Invoke the unchanged worker run() in a bounded child, with a tripwire at
    # its NEXT source-validation boundary. No source/model/database operation
    # beyond parsing may execute. This also distinguishes decode rejection from
    # an unrelated later failure that could otherwise give a false-positive test.
    code='''
import runpy, os
from src.handoff import source
from src.handoff.protocol import HandoffError
class SourceReached(Exception): pass
def forbidden(*args): raise SourceReached()
source.source_identity=forbidden
worker=runpy.run_path('tests/native/handoff_worker.py')
try: worker['run']()
except HandoffError as error:
    if str(error)!='invalid encoding': raise
    os.write(2,b'BOOTSTRAP_ENCODING_REJECTED')
except SourceReached:
    os.write(2,b'BOOTSTRAP_VALID_UTF8_SOURCE_BOUNDARY')
else: raise AssertionError('bootstrap escaped')
'''
    result=subprocess.run([sys.executable,'-B','-c',code],input=encoded_wire('HELLO',encoding),
        capture_output=True,timeout=5)
    expected=b'BOOTSTRAP_VALID_UTF8_SOURCE_BOUNDARY' if encoding=='utf-8' else b'BOOTSTRAP_ENCODING_REJECTED'
    assert result.returncode==0 and result.stdout==b'' and result.stderr==expected


@pytest.mark.parametrize('encoding',_INVALID_WIRE_ENCODINGS)
def test_original_host_broker_rejects_encoding_before_worker_verified_or_approval(monkeypatch,encoding):
    from types import SimpleNamespace
    namespace=HOST['run'].__globals__;original_admission=namespace['_HostAdmission'];observed=[]
    class TrackedAdmission(original_admission):
        def __init__(self,*a,**kw):
            self.transitions=[];observed.append(self);super().__init__(*a,**kw)
        def __setattr__(self,key,value):
            if key=='state':self.transitions.append(value)
            super().__setattr__(key,value)
    context=dict(session='a'*64,container='b'*64,source='c'*64,challenge='a'*64)
    sr,sw=os.pipe();er,ew=os.pipe();ir,iw=os.pipe()
    os.write(sw,encoded_wire('PROPOSAL',encoding,context));os.close(sw);os.close(ew)
    worker=SimpleNamespace(stdin=os.fdopen(iw,'wb',buffering=0),stdout=os.fdopen(sr,'rb',buffering=0),
        stderr=os.fdopen(er,'rb',buffering=0),poll=lambda:0,wait=lambda **kw:0)
    resource=SimpleNamespace(identity='a'*64,name='mock-owned',events=[],pending=False,
        create=lambda args:'b'*64,cleanup=lambda:'CLEANUP_SUCCESS')
    approve=Mock(side_effect=AssertionError('no approval or authority on invalid encoding'))
    monkeypatch.setitem(namespace,'_HostAdmission',TrackedAdmission)
    monkeypatch.setitem(namespace,'OwnedContainer',lambda:resource)
    monkeypatch.setitem(namespace,'source_identity',lambda project:'c'*64)
    monkeypatch.setattr(namespace['secrets'],'token_hex',lambda n:'a'*64)
    monkeypatch.setattr(HOST['P0'],'validate_recovery',lambda **kw:None)
    monkeypatch.setattr(HOST['P0'],'_container',lambda *a:None)
    monkeypatch.setattr(HOST['P0'],'_inspect',lambda args:{'Id':'b'*64,'Config':{
        'Cmd':HOST['ENTRYPOINT'],'Tty':False,'OpenStdin':True,'Volumes':None,'Labels':{'r7_handoff':'a'*64}}})
    monkeypatch.setattr(HOST['P0'],'_docker',Mock(side_effect=AssertionError('no real Docker in broker contract test')))
    monkeypatch.setattr(namespace['subprocess'],'Popen',lambda *a,**kw:worker)
    try:
        with pytest.raises(HOST['HandoffFailure']) as error:
            HOST['run'](allow_native_handoff=True,use_recovered_image=True,approve=approve,timeout=1)
        assert error.value.state=='REJECTED'
        approve.assert_not_called()
        assert observed[0].transitions==['SESSION_CREATED','REJECTED']
    finally:
        os.close(ir)
        for stream in (worker.stdin,worker.stdout,worker.stderr):stream.close()
