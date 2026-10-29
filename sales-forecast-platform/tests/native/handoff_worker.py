"""FIXED synthetic controlled-offline worker. No selectable command or pytest.

Only protected host invocation authorizes this fixture. Stdout is exclusively
framed data; redirect fd 1 BEFORE importing native libraries/test fixtures.
"""
import os
import sys
import signal
import time
from pathlib import Path


def run():
    def interrupted(*_): raise KeyboardInterrupt('worker interrupted')
    # Explicit PID-1 signal handling: default SIGTERM may otherwise be ignored.
    signal.signal(signal.SIGTERM,interrupted)
    protocol_out = os.dup(1); os.set_inheritable(protocol_out,False); os.dup2(2,1)
    from src.handoff.protocol import Channel, decode, validate, HandoffError, MAX_FRAME
    from src.handoff.source import source_identity
    from src.handoff.worker_export import _ProtectedExport
    import struct
    channel = Channel(0,protocol_out,deadline=time.monotonic()+240)
    # Exactly one bounded bootstrap. No command, path, source or image override.
    n, = struct.unpack('>I',channel._read(4))
    if not 0<n<=MAX_FRAME: raise HandoffError('bootstrap size')
    initial = decode(channel._read(n)); validate(initial,'HELLO',{})
    channel.received = n+4
    context = {k:initial[k] for k in ('session','container','source','challenge')}
    hello = initial['body']; now=time.time()
    if not now < hello['deadline'] <= now+240: raise HandoffError('expired session')
    channel.deadline = min(channel.deadline,time.monotonic()+hello['deadline']-now)
    if source_identity(Path('/app')) != context['source']: raise HandoffError('unreviewed source')
    if os.getuid()!=1000 or os.environ.get('B1_ISOLATED_NATIVE_TEST')!='1': raise HandoffError('not isolated')
    import runpy
    import tempfile
    from contextlib import ExitStack
    from src.services.controlled_execution_authority import ControlledExecutionAuthority
    from src.services.controlled_h1_execution_service import ControlledH1ExecutionService
    from src.services.successful_b3_producer_service import SuccessfulB3ProducerService
    from src.services.forecast_write_intent_authority import ForecastWriteIntentAuthority
    base = runpy.run_path('tests/runtime/test_controlled_h1_execution.py')
    class TemporaryFactory:
        def mktemp(self,name): return Path(tempfile.mkdtemp(prefix=name,dir='/tmp'))
    with ExitStack() as stack:
        isolated=base['isolated_only'].__wrapped__(); next(isolated)
        stack.callback(lambda: next(isolated,None))
        fixture=base['genuine'].__wrapped__(TemporaryFactory()); s=next(fixture)
        stack.callback(lambda: next(fixture,None))
        op, pop, wop = object(),object(),object()
        authority=ControlledExecutionAuthority(s['provider'],operator=op)
        service=ControlledH1ExecutionService(authority)
        producer=SuccessfulB3ProducerService(service,operator=pop)
        cap=authority.issue(op,s['ref'],s['native'],s['inputs'],s['request'],lifetime_seconds=60,execution_seconds=30)
        receipt=producer.produce(pop,s['ref'],s['native'],s['inputs'],s['request'],
            capability=cap,deadline=min(time.time()+30,hello['deadline']),lifetime_seconds=60)
        replay=service.execute(s['ref'],s['native'],s['inputs'],s['request'],capability=cap)
        if replay.status!='DENIED' or replay.result is not None:
            raise HandoffError('execution grant replay succeeded')
        issuer=ForecastWriteIntentAuthority(producer,operator=wop)
        export=_ProtectedExport(issuer,wop,receipt,s['ref'],s['native'],s['inputs'],s['request'],context,
            idempotency=hello['idempotency'],deadline=hello['deadline'])
        channel.send(export.proposal())
        approval=channel.receive('APPROVAL',context)
        channel.send(export.reserve(approval))
        challenge=channel.receive('CHALLENGE',context)
        channel.send(export.release(challenge))
        ack=channel.receive('ACK',context)
        if ack['release'] != decode_release_id(export,challenge): raise HandoffError('foreign acknowledgement')
        # Test fixture itself already proves original fit/reload numerical parity.
        # The sink has no model/database operation and can grant no additional use.
        try: issuer.simulate_consume(export._grant,deadline=hello['deadline'])
        except ValueError: pass
        else: raise HandoffError('grant replay succeeded')
    os.close(protocol_out)


def decode_release_id(export,challenge):
    from src.handoff.protocol import digest
    return digest([export._context,challenge,export._consumed.grant.grant_id])


if __name__=='__main__':
    try: run()
    except BaseException:
        # No sensitive object repr or protocol stdout on failure.
        print('protected native handoff rejected',file=sys.stderr)
        raise SystemExit(2)
