"""Explicit host-only genuine native -> separate PG client integration.

Reuses accepted PG topology/lifecycle, unchanged network-none native launcher.
No application imports/consumers. No inherited application DSN is consulted.
"""
import argparse
import json
from pathlib import Path
import runpy
from uuid import uuid4
from src.handoff.host_admission import ProtectedHostAdmissions
from src.handoff.protocol import HandoffError
from src.services.authorized_forecast_persistence import AuthorizedForecastPersistence
from src.data.contracts.forecast_evidence_storage import StoredForecastBatch,CanonicalForecastStorageBatch
from src.database.offline_forecast_postgres import ForecastStorageConflict,ForecastStorageRejected,ForecastStorageRetryable,AmbiguousForecastCommit,ForecastStorageUnavailable

ROOT=Path(__file__).resolve().parents[2]
ERRORS={c.__name__:c for c in (ForecastStorageConflict,ForecastStorageRejected,ForecastStorageRetryable,AmbiguousForecastCommit,ForecastStorageUnavailable)}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--authorized-native-postgres',action='store_true')
    parser.add_argument('--wheelhouse',type=Path,required=True);parser.add_argument('--evidence',type=Path,required=True)
    args=parser.parse_args()
    if not args.authorized_native_postgres:raise SystemExit('explicit genuine integration authorization required')
    pg=runpy.run_path(str(ROOT/'tests/postgres/run_postgres.py'));namespace=pg['run'].__globals__
    policy=runpy.run_path(str(ROOT/'tests/native/test_native_handoff_integration.py'))['approved_synthetic_intent']
    # Revalidate recovered image/manifest BEFORE any native or DB workload.
    p0=runpy.run_path(str(ROOT/'tests/native/run_recovered_image.py'));p0['validate_recovery'](use_recovered_image=True)
    results=[]
    def stage(*,server,socket,client,envfile,run_id,database,record):
        command=pg['command'];prefix='r7-05b3-'+run_id.hex
        class Storage:
            mode='append'
            calls=0
            def invoke(self,operation,batch):
                name=prefix+'-b4b-'+uuid4().hex
                argv=['docker','run','-i','--name',name,'--label','r7_05b3_run='+str(run_id),
                    '--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
                    '--user=1000:1000','--cpus=1','--memory=1g','--memory-swap=1g','--pids-limit=64','--ulimit','cpu=180:180',
                    '--tmpfs=/tmp:rw,noexec,nosuid,size=256m','--mount','type=volume,src='+socket+',dst=/run/r7pg,readonly',
                    '--mount','type=bind,src='+str(ROOT/'src')+',dst=/code/src,readonly',
                    '--mount','type=bind,src='+str(ROOT/'tests')+',dst=/code/tests,readonly','--workdir=/code','--env-file',str(envfile)]
                # No admin or migration capability in fixed persistence clients.
                for key in ('R7_PG_ADMIN_PASSWORD','R7_PG_MIGRATOR_PASSWORD'):argv+=['-e',key+'=']
                if operation in ('lookup','reader_proof'):argv+=['-e','R7_PG_WRITER_PASSWORD=']
                for key in pg['PROXIES']:argv+=['-e',key+'=']
                argv+=['-e','PYTHONPATH=/code','-e','PYTHONDONTWRITEBYTECODE=1','-e','OPENBLAS_NUM_THREADS=1','-e','OMP_NUM_THREADS=1',
                    '--entrypoint','python',client,'-B','tests/persistence/postgres_client.py']
                try:
                    r=command(argv,stdin=json.dumps(dict(operation=operation,batch=batch.canonical_json)),timeout=40)
                    if len(r.stdout)>1024*1024:raise ValueError('client response budget')
                    info=json.loads(command(['docker','inspect',name]).stdout)[0]
                    values=dict(x.split('=',1) for x in info['Config']['Env'])
                    assert values.get('R7_PG_ADMIN_PASSWORD')==values.get('R7_PG_MIGRATOR_PASSWORD')==''
                    if operation in ('lookup','reader_proof'):assert values.get('R7_PG_WRITER_PASSWORD')==''
                    info['Config']['Env']=[s.split('=')[0]+'=<test-secret>' if 'PASSWORD=' in s else s for s in info['Config']['Env']]
                    record.setdefault('b4b_clients',[]).append(dict(operation=operation,inspect=info))
                    value=json.loads(r.stdout)
                    if operation=='reader_proof':return value
                    if value['error']:raise ERRORS[value['error']]('isolated storage rejection')
                    return None if value['saved'] is None else StoredForecastBatch.model_validate_json(value['saved'])
                finally:
                    if not pg['remove_owned']('container',name,run_id):raise RuntimeError('client cleanup unconfirmed')
            def _append_initial(self,batch):self.calls+=1;return self.invoke(self.mode,batch)
            def _lookup(self,key,*,expected):
                assert key==expected.idempotency_id
                return self.invoke('lookup',expected)
        # ORIGINAL native broker inside host retention. PG cannot enter native worker.
        op=object();admissions=ProtectedHostAdmissions(operator=op)
        def capture():return admissions.capture(op,approve=policy)
        def service(store):
            operator=object();return AuthorizedForecastPersistence(admissions,store,operator=operator),operator
        # Three fresh genuine worker-local owner/producer/grant lifecycles.
        for fault in ('success','rollback','ack_lost'):
            handle=capture();batch=admissions.verify(handle,deadline=__import__('time').time()+30)
            pg['write_evidence'](args.evidence/(fault+'-approved-projection.json'),batch.canonical_json)
            store=Storage();store.mode={'success':'append','rollback':'rollback','ack_lost':'ack_lost'}[fault]
            coordinator,operator=service(store);grant=coordinator.authorize(operator,handle,deadline=__import__('time').time()+30)
            if fault=='ack_lost':
                try:coordinator.commit(grant)
                except AmbiguousForecastCommit:pass
                else:raise AssertionError('lost acknowledgement not surfaced')
                assert coordinator.status(grant)=='COMMIT_OUTCOME_UNKNOWN'
                saved=coordinator.reconcile(operator,grant)['receipt']
                assert saved is not None
            else:
                committed=coordinator.commit(grant)
                assert committed['state']==('COMMIT_CONFIRMED' if fault=='success' else 'COMMIT_REJECTED')
                saved=committed['receipt']
            try:coordinator.commit(grant)
            except ValueError:pass
            else:raise AssertionError('second write attempt')
            assert store.calls==1
            # Every lookup is a NEW credential-restricted client/connection.
            found=store._lookup(batch.idempotency_id,expected=batch)
            if fault=='rollback':assert found is None
            else:
                assert found==saved and found.batch.canonical_json==batch.canonical_json
                pg['write_evidence'](args.evidence/(fault+'-durable-readback.json'),found.canonical_json)
                proof=store.invoke('reader_proof',batch)
                assert proof==dict(readonly='on',role='r7_forecast_reader',float32_le=['2d56f943','e9542244','2d56f943','e9542244'])
                results.append(dict(case=fault,state=coordinator.status(grant),receipt_id=str(saved.receipt.receipt_id),
                    content_sha256=batch.content_sha256,proof=proof,write_calls=store.calls))
                # Real DB restart and fresh trusted SELECT-only client. Detached
                # content used only for lookup, never reconstructs write authority.
                command(['docker','restart','--time=5',server],timeout=20)
                import time
                end=time.monotonic()+30
                while True:
                    try:recovered=store._lookup(batch.idempotency_id,expected=batch);break
                    except ForecastStorageUnavailable:
                        if time.monotonic()>=end:raise
                        time.sleep(.2)
                assert recovered==saved
            if fault=='rollback':results.append(dict(case=fault,state=coordinator.status(grant),write_calls=store.calls,receipt=None))
        # Genuine admission and real PG transaction with an operational failure
        # at commit submission: receipt absent, authority remains spent/UNKNOWN.
        handle=capture();store=Storage();coordinator,operator=service(store)
        grant=coordinator.authorize(operator,handle,deadline=__import__('time').time()+30)
        store.mode='ack_absent'
        try:coordinator.commit(grant)
        except AmbiguousForecastCommit:pass
        assert coordinator.reconcile(operator,grant)==dict(state='COMMIT_OUTCOME_UNKNOWN',receipt=None)
        try:coordinator.commit(grant)
        except ValueError:pass
        else:raise AssertionError('UNKNOWN restored write permission')
        assert store.calls==1;results.append(dict(case='unknown_absent',state=coordinator.status(grant),write_calls=1))
        # Genuine original admission retained, but independently revoked BEFORE
        # transaction cutoff. Provider must never be contacted.
        handle=capture();store=Storage();coordinator,operator=service(store)
        grant=coordinator.authorize(operator,handle,deadline=__import__('time').time()+30);coordinator.revoke(operator,grant)
        try:coordinator.commit(grant)
        except ValueError:pass
        else:raise AssertionError('revoked commit')
        assert store.calls==0;results.append(dict(case='host_revoked',write_calls=0))
        record['b4b_results']=results
        pg['write_evidence'](args.evidence/'b4b-results.json',json.dumps(results,indent=2))
    pg['run'](allow=True,wheelhouse=args.wheelhouse,evidence=args.evidence,_host_stage=stage)
    print(json.dumps(dict(b4b='PASS',genuine_native_invocations=5,results=results)))

if __name__=='__main__':main()
