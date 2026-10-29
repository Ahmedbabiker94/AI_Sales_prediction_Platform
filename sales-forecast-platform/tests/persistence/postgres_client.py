"""Fixed test-only PG client. No native imports, Docker socket or public endpoint.

Only the protected test host can create this credentialed client/stdio channel.
Serialized requests are content, not producer authority. Authority stays on host.
"""
import json
import os
import sys
from uuid import UUID
from contextlib import contextmanager
from src.data.contracts.forecast_evidence_storage import CanonicalForecastStorageBatch
from src.database.offline_forecast_postgres import OfflineForecastPostgres,ForecastStorageRejected,ForecastStorageError,driver
from src.repositories.postgres_forecast_evidence_repository import _PostgresForecastEvidenceRepository
from src.providers.forecast_evidence_storage_provider import _ForecastEvidenceStorageProvider


def main():
    raw=sys.stdin.buffer.read(1024*1024+1)
    if len(raw)>1024*1024:raise ValueError('request budget')
    data=json.loads(raw.decode('utf-8',errors='strict'))
    if set(data)!={'operation','batch'} or data['operation'] not in ('append','lookup','rollback','ack_lost','ack_absent','reader_proof'):
        raise ValueError('fixed client request required')
    operation=data['operation']
    batch=CanonicalForecastStorageBatch.model_validate_json(data['batch'])
    instance=UUID(os.environ['R7_PG_INSTANCE']);database=os.environ['R7_PG_DATABASE']
    def target(role):return OfflineForecastPostgres(database=database,instance_id=instance,access=role,
        password=('unused-no-writer-credential' if role=='writer' and operation in ('lookup','reader_proof') else os.environ['R7_PG_'+role.upper()+'_PASSWORD']),allow_disposable=True)
    repo=_PostgresForecastEvidenceRepository(target('reader'),target('writer'),batch.scope)
    provider=_ForecastEvidenceStorageProvider(repo);operation=data['operation']
    if operation=='rollback':
        original=_PostgresForecastEvidenceRepository._insert_row
        def insert(self,cursor,records,ordinal):
            if ordinal==1:raise ForecastStorageRejected('injected known precommit rollback')
            return original(self,cursor,records,ordinal)
        _PostgresForecastEvidenceRepository._insert_row=insert
    if operation in ('ack_lost','ack_absent'):
        original=OfflineForecastPostgres._open
        class LostAck:
            def __init__(self,c):self.c=c
            def cursor(self):return self.c.cursor()
            def commit(self):
                if operation=='ack_lost':self.c.commit() # REAL COMMIT then lost acknowledgement.
                # ack_absent injects a failure at commit submission; the real
                # transaction is rolled back on close. Host cannot infer this.
                raise driver().OperationalError('injected acknowledgement lost after real commit')
        @contextmanager
        def opened(self,scope=None):
            with original(self,scope) as c:yield LostAck(c) if self.access=='writer' else c
        OfflineForecastPostgres._open=opened
    if operation=='reader_proof':
        # Test infrastructure SQL; never business-service SQL.
        with target('reader')._open(batch.scope) as c,c.cursor() as cursor:
            cursor.execute('SHOW transaction_read_only');readonly=cursor.fetchone()[0]
            cursor.execute('SELECT encode(float32_le,\'hex\') FROM canonical_forecast.evidence WHERE organization_id=%s AND dataset_id=%s AND idempotency_id=%s ORDER BY ordinal',
                (str(batch.scope.organization_id),str(batch.scope.dataset_id),batch.idempotency_id))
            bits=[r[0] for r in cursor.fetchall()]
            cursor.execute('SELECT current_user');role=cursor.fetchone()[0]
        print(json.dumps(dict(readonly=readonly,role=role,float32_le=bits)));return
    try:
        saved=provider._lookup(batch.idempotency_id,expected=batch) if operation=='lookup' else provider._append_initial(batch)
        result=dict(saved=None if saved is None else saved.canonical_json,error=None)
    except ForecastStorageError as e:result=dict(saved=None,error=type(e).__name__)
    print(json.dumps(result))

if __name__=='__main__':main()
