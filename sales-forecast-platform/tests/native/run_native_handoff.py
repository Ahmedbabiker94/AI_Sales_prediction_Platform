"""Trusted-host fixed synthetic native handoff. Disabled unless explicitly selected.

No CLI worker/module/image overrides. Source, approval callback, Python private
state and Docker daemon belong to trusted host. NOT a public endpoint or IAM.
No PostgreSQL client, database connection, write or durable receipt.
"""
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import secrets
import signal
import subprocess
import time
from src.handoff.protocol import Channel, HandoffError, canonical, digest, frame, hex64
from src.handoff.content import inspect_proposal
from src.handoff.source import source_identity

PROJECT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location('_accepted_handoff_p0', PROJECT/'tests/native/run_recovered_image.py')
P0 = importlib.util.module_from_spec(_SPEC); _SPEC.loader.exec_module(P0)
ENTRYPOINT = ['env','-i','PATH=/usr/local/bin:/usr/bin:/bin','PYTHONPATH=/app',
    'PYTHONDONTWRITEBYTECODE=1','OPENBLAS_NUM_THREADS=1','OMP_NUM_THREADS=1',
    'B1_ISOLATED_NATIVE_TEST=1','python','-B','/app/tests/native/handoff_worker.py']


class HandoffFailure(HandoffError):
    def __init__(self,state,resource):
        super().__init__('protected handoff '+state+'; owned resource '+resource)
        self.state=state
        self.resource=resource


def _env():
    env = dict(os.environ)
    for k in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'): env.pop(k,None)
    return env


def _argv(args): return ['docker','--host=unix:///var/run/docker.sock',*args]


class OwnedContainer:
    """Bounded targeted reconciliation; CLI death never proves daemon cancellation."""
    def __init__(self):
        self.identity = secrets.token_hex(32)
        self.name = 'r7-handoff-'+self.identity
        self.pending = False
        self.status = 'NOT_STARTED'
        self.events = []

    def command(self,args,timeout=30):
        process = None
        try:
            process = subprocess.Popen(_argv(args),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,env=_env(),start_new_session=True)
            out,err = process.communicate(timeout=timeout)
            if len(out)+len(err)>256*1024: raise HandoffError('Docker response budget')
            return subprocess.CompletedProcess(args,process.returncode,out,err)
        except BaseException:
            if process is not None:
                if process.poll() is None: os.killpg(process.pid,signal.SIGKILL)
                process.communicate(timeout=3)
            raise

    def create(self,args):
        # Register BEFORE local CLI. Once ambiguous, never silently clear pending.
        self.pending = True; self.events.append('CREATE_PENDING')
        r=self.command(args)
        if r.returncode: raise HandoffError('creation failed')
        cid=r.stdout.decode().strip(); hex64(cid)
        self.cid=cid; self.pending=False; self.events.append('CREATE_RETURNED')
        return cid

    def cleanup(self):
        end=time.monotonic()+5
        absent=False; foreign=False
        while time.monotonic()<end:
            try:
                r=self.command(['inspect',self.name],timeout=min(1,max(.05,end-time.monotonic())))
                if r.returncode:
                    absent=b'no such' in r.stderr.lower()
                    if absent and not self.pending: break
                else:
                    d=json.loads(r.stdout)[0]
                    if d['Config']['Labels'].get('r7_handoff')!=self.identity:
                        foreign=True; break
                    if hasattr(self,'cid') and d['Id']!=self.cid:
                        foreign=True; break
                    self.command(['rm','-f',d['Id']],timeout=1)
                time.sleep(.1)
            except Exception:
                absent=False
        self.status='CLEANUP_SUCCESS' if absent and not self.pending and not foreign else 'CLEANUP_UNCONFIRMED'
        self.events.append(self.status)
        return self.status


class _HostAdmission:
    """Original session/channel only. Serialized frames cannot construct this state."""
    def __init__(self,context,channel,*,deadline,idempotency,approve,cancelled,wall=time.time,elapsed=time.monotonic):
        self.context=dict(context); self.channel=channel
        self.deadline,self.idempotency=deadline,idempotency
        self.approve,self.cancelled,self.wall,self.elapsed=approve,cancelled,wall,elapsed
        self.end=elapsed()+min(240,deadline-wall()); self.last_wall=None; self.last_elapsed=None
        self.state='SESSION_CREATED'; self._self=self; self.active=True

    def check(self):
        stop=self.cancelled(); elapsed=self.elapsed(); now=self.wall()
        if (stop is not False or type(now) not in (int,float) or type(elapsed) not in (int,float)
                or not math.isfinite(now) or not math.isfinite(elapsed)
                or now>=self.deadline or elapsed>=self.end
                or self.last_wall is not None and now<self.last_wall
                or self.last_elapsed is not None and elapsed<self.last_elapsed):
            raise HandoffError('host cancelled or expired')
        # LAST callbacks above; only protected live state checks below.
        if self._self is not self or not self.active: raise HandoffError('invalidated host session')
        self.last_wall=now; self.last_elapsed=elapsed
        return now

    def proposal(self,body,channel):
        if channel is not self.channel or self.state!='WORKER_VERIFIED': raise HandoffError('unauthenticated proposal')
        records,batch=inspect_proposal(body)
        if records.idempotency_id!=self.idempotency: raise HandoffError('foreign idempotency')
        self.body=canonical(body); self.proposal_id=digest(body)
        self.state='PROPOSAL_RECEIVED'
        # Approval inspects full validated scope, rows AND reconstructed projection.
        # Protected host callback is independent intent, never an actor label/hash.
        if self.approve(records,batch) is not True: raise HandoffError('host did not approve')
        now=self.check()
        if canonical({'records':records.canonical_json,'projection':batch.canonical_json})!=self.body:
            raise HandoffError('approval mutated content')
        if now>=records.producer.expires_at: raise HandoffError('producer expired')
        self.approval=secrets.token_hex(32)
        self.state='HOST_APPROVED'
        return {'proposal':self.proposal_id,'approval':self.approval}

    def reserved(self,body,channel):
        if (channel is not self.channel or self.state!='HOST_APPROVED'
                or body['proposal']!=self.proposal_id or body['approval']!=self.approval):
            raise HandoffError('foreign reservation')
        self.check(); self.reservation=body['reservation']; self.grant=body['grant']
        self.final_challenge=secrets.token_hex(32); self.state='GRANT_RESERVED'
        return {'proposal':self.proposal_id,'approval':self.approval,'reservation':self.reservation,
                'final_challenge':self.final_challenge}

    def admit(self,body,channel):
        if channel is not self.channel or self.state!='GRANT_RESERVED': raise HandoffError('admission unavailable')
        self.state='UNKNOWN'  # Any failure burns this host-side attempt too.
        expected=dict(proposal=self.proposal_id,approval=self.approval,reservation=self.reservation,
                      final_challenge=self.final_challenge,grant=self.grant)
        if any(body[k]!=v for k,v in expected.items()): raise HandoffError('substituted release')
        challenge={k:expected[k] for k in ('proposal','approval','reservation','final_challenge')}
        if body['release']!=digest([self.context,challenge,self.grant]): raise HandoffError('release mismatch')
        records,batch=inspect_proposal(json.loads(self.body))
        # Precompute detached result BEFORE final callbacks/live admission state.
        result={'state':'VALIDATED_NO_WRITE','session':self.context['session'],'release':body['release'],
            'records':records.canonical_json,'projection':batch.canonical_json,
            'record_set_id':records.record_set_id,'projection_sha256':batch.content_sha256,
            'writes_performed':False,'durable_receipt':None}
        now=self.check()
        if not now<body['expires']<=min(self.deadline,records.producer.expires_at): raise HandoffError('release expired')
        if self.state!='UNKNOWN' or not self.active: raise HandoffError('terminal invalidation')
        self.state='HOST_ADMITTED_NO_WRITE'
        return result


def run(*, allow_native_handoff=False, use_recovered_image=False, approve=None, cancelled=lambda: False, timeout=240):
    """Trusted host Python API; explicit authorization plus full-content approval.

    No detached channel, command, source path, image or saved log argument exists.
    Source is pinned before creation, rechecked before start AND before admission.
    """
    if allow_native_handoff is not True or use_recovered_image is not True or not callable(approve):
        raise HandoffError('explicit protected native handoff authorization required')
    if type(timeout) not in (int,float) or not 0<timeout<=240: raise HandoffError('invalid timeout')
    P0.validate_recovery(use_recovered_image=True)
    source=source_identity(PROJECT)
    owned=OwnedContainer(); process=None; admission=None; result=None; channel=None
    previous=signal.getsignal(signal.SIGTERM)
    def interrupted(*_): raise KeyboardInterrupt('handoff interrupted')
    signal.signal(signal.SIGTERM,interrupted)
    try:
        args=['create','-i','--name',owned.name,'--label','r7_handoff='+owned.identity,
            '--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
            '--memory','1g','--memory-swap','1g','--cpus','1','--pids-limit','64','--ulimit','cpu=180:180',
            '--user','1000:1000','--tmpfs','/tmp:rw,nosuid,nodev,size=256m']
        for key in P0._PROXY_KEYS: args+=['--env',key+'=']
        for directory in ('src','tests'):
            args+=['--mount',f'type=bind,src={PROJECT/directory},dst=/app/{directory},readonly']
        args+=['--workdir','/app',P0._IMAGE,*ENTRYPOINT]
        cid=owned.create(args)
        data=P0._inspect(['inspect',cid]); P0._container(data,PROJECT)
        if (data['Id']!=cid or data['Config']['Cmd']!=ENTRYPOINT or data['Config']['Tty']
                or not data['Config']['OpenStdin'] or data['Config'].get('Volumes')
                or data['Config']['Labels']!={'r7_handoff':owned.identity}): raise HandoffError('worker identity')
        P0.validate_recovery(use_recovered_image=True)
        if source_identity(PROJECT)!=source: raise HandoffError('source changed before start')
        # Pending start remains uncertain until protocol from this exact attach proves it ran.
        owned.pending=True; owned.events.append('START_PENDING')
        process=subprocess.Popen(_argv(['start','--attach','--interactive',cid]),stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=_env(),start_new_session=True,bufsize=0)
        channel=Channel(process.stdout.fileno(),process.stdin.fileno(),stderr=process.stderr.fileno(),
            deadline=time.monotonic()+timeout,cancelled=cancelled)
        context=dict(session=secrets.token_hex(32),container=cid,source=source,challenge=secrets.token_hex(32))
        deadline=time.time()+timeout; idempotency=secrets.token_hex(32)
        admission=_HostAdmission(context,channel,deadline=deadline,idempotency=idempotency,approve=approve,cancelled=cancelled)
        admission.check()
        channel.send(frame('HELLO',context,{'idempotency':idempotency,'deadline':deadline}))
        proposal=channel.receive('PROPOSAL',context)
        owned.pending=False; owned.events.append('START_AUTHENTICATED')
        admission.state='WORKER_VERIFIED'
        channel.send(frame('APPROVAL',context,admission.proposal(proposal,channel)))
        reservation=channel.receive('RESERVED',context)
        channel.send(frame('CHALLENGE',context,admission.reserved(reservation,channel)))
        release=channel.receive('FINAL_RELEASE',context)
        if source_identity(PROJECT)!=source: raise HandoffError('source changed before admission')
        result=admission.admit(release,channel)
        channel.send(frame('ACK',context,{'release':release['release'],'state':'VALIDATED_NO_WRITE'}))
        # Continue bounded draining; unexpected trailing stdout is not a second release.
        process.stdin.close()
        while True:
            channel._ready(channel.reader,1)
            raw=os.read(channel.reader,8192)
            if not raw: break
            raise HandoffError('duplicate or trailing protocol')
        if process.wait(timeout=3)!=0: raise HandoffError('worker failed after release; admission status retained')
        result['worker_exit']=process.returncode
        result['worker_identity']=dict(container=cid,source=source,image=P0._IMAGE,manifest=P0._MANIFEST_SHA)
        result['isolation']=dict(network=data['HostConfig']['NetworkMode'],readonly_root=data['HostConfig']['ReadonlyRootfs'],
            uid_gid=data['Config']['User'],tty=data['Config']['Tty'],cap_drop=data['HostConfig']['CapDrop'],
            security=data['HostConfig']['SecurityOpt'],memory=data['HostConfig']['Memory'],
            cpus=data['HostConfig']['NanoCpus'],pids=data['HostConfig']['PidsLimit'],mounts=data['Mounts'])
        return result
    except BaseException:
        state='REJECTED'
        if admission is not None:
            state='UNKNOWN' if admission.state in ('HOST_APPROVED','GRANT_RESERVED','HOST_ADMITTED_NO_WRITE','UNKNOWN') else 'REJECTED'
            admission.state=state
        raise HandoffFailure(state,owned.name) from None
    finally:
        if process is not None:
            if process.poll() is None:
                try: os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError: pass
            process.wait(timeout=3)
            for stream in (process.stdin,process.stdout,process.stderr):
                if stream is not None: stream.close()
        status=owned.cleanup()
        signal.signal(signal.SIGTERM,previous)
        if result is not None:
            result['cleanup']=status
            result['lifecycle']=owned.events
        if status!='CLEANUP_SUCCESS':
            raise HandoffError('CLEANUP_UNCONFIRMED: '+owned.name+'; no daemon quiescence guarantee')
