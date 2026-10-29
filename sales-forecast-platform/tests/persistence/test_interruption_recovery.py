"""Explicit genuine SIGINT/reconciliation test, not an alternate native path.

The ORIGINAL service propagates SIGINT to this test host. Only then does the host
explicitly invoke SELECT-only exact reconciliation. No exception is converted by
the service into ordinary success; no consumed authority is recreated or retried.
All native work still uses the fixed, P0-validated network-none worker.
"""
import argparse
import inspect
import json
import os
from pathlib import Path
import runpy
import signal
import struct
import sys
from uuid import uuid4
from src.services.authorized_forecast_persistence import AuthorizedForecastPersistence
from src.data.contracts.forecast_evidence_storage import CanonicalForecastStorageBatch
from src.database.offline_forecast_postgres import ForecastStorageConflict


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--authorized-native-postgres',action='store_true')
    parser.add_argument('--wheelhouse',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    args=parser.parse_args()
    if args.authorized_native_postgres is not True:raise SystemExit('explicit bounded native/PG authorization required')
    original=AuthorizedForecastPersistence.commit;first=True;observed={}
    lines,start=inspect.getsourcelines(original)
    target=[start+i for i,line in enumerate(lines) if "r.state='COMMIT_CONFIRMED'" in line]
    assert len(target)==1

    def commit_with_signal(self,h,**kwargs):
        nonlocal first
        if not first:return original(self,h,**kwargs)
        first=False
        def trace(frame,event,arg):
            if event=='line' and frame.f_code is original.__code__ and frame.f_lineno==target[0]:
                observed['before_signal']=frame.f_locals['r'].state # read only; do not reenter held coordinator lock
                os.kill(os.getpid(),signal.SIGINT) # actual OS signal, after real provider COMMIT/validation
            return trace
        sys.settrace(trace)
        try:return original(self,h,**kwargs)
        except KeyboardInterrupt:
            sys.settrace(None)
            observed.update(propagated_to_host=True,after_signal=self.status(h),provider_write_calls=self._storage.calls)
            assert self.status(h)=='COMMIT_OUTCOME_UNKNOWN' and self._storage.calls==1
            r=self._records[id(h)];assert r.reservation is not None and not r.operation.locked()
            batch=CanonicalForecastStorageBatch.model_validate_json(r.projection)
            # Actual SELECT-only queries with wrong expected content/scope. These
            # are observations, never write-authority inputs or replacement rows.
            altered_rows=tuple(row.model_copy(update={'lineage':row.lineage.model_copy(update={'artifact_sha256':'0'*64})}) for row in batch.rows)
            altered=CanonicalForecastStorageBatch.model_validate_json(batch.model_copy(update={'rows':altered_rows}).canonical_json)
            try:self._storage.invoke('lookup',altered)
            except ForecastStorageConflict:observed['exact_content_mismatch']='REJECTED'
            else:raise AssertionError('wrong content confirmed')
            organization=uuid4()
            foreign=CanonicalForecastStorageBatch.model_validate_json(batch.model_copy(update={
                'scope':batch.scope.model_copy(update={'organization_id':organization}),
                'rows':tuple(row.model_copy(update={'key':row.key.model_copy(update={'organization_id':organization})}) for row in batch.rows),
            }).canonical_json)
            assert self._storage.invoke('lookup',foreign) is None
            observed['foreign_tenant_lookup']='NO_RECEIPT'
            recovered=self.reconcile(self._operator,h)
            assert recovered['state']=='COMMIT_CONFIRMED' and recovered['receipt'] is not None
            saved=recovered['receipt'];assert saved.batch.canonical_json==batch.canonical_json
            bits=[x.float32_le for x in saved.batch.rows]
            assert bits==['2d56f943','e9542244','2d56f943','e9542244']
            assert self.reconcile(self._operator,h)['receipt']==saved
            try:original(self,h)
            except ValueError:pass
            else:raise AssertionError('second write attempt')
            assert self._storage.calls==1
            observed.update(after_reconciliation=self.status(h),receipt_id=str(saved.receipt.receipt_id),
                idempotency=batch.idempotency_id,content_sha256=batch.content_sha256,float32_le=bits,
                predictions=[struct.unpack('<f',bytes.fromhex(x))[0] for x in bits],repeated_readonly_recovery=True,replay='REJECTED')
            return recovered
        finally:sys.settrace(None)

    AuthorizedForecastPersistence.commit=commit_with_signal
    try:
        # Fixed accepted test topology and source. Not caller-selected commands.
        runner=runpy.run_path(str(Path(__file__).with_name('run_authorized_persistence.py')))
        runner['main']()
    finally:AuthorizedForecastPersistence.commit=original
    manifest=json.loads((args.evidence/'run-manifest.json').read_text())
    assert manifest['result']=='PASS' and manifest['cleanup_status']=='CLEANUP_SUCCESS'
    assert not manifest['remaining_containers'] and not manifest['remaining_volumes']
    assert len(manifest['b4b_results'])==5 and observed['propagated_to_host']
    helper=runpy.run_path(str(Path(__file__).parents[1]/'postgres/run_postgres.py'))
    helper['write_evidence'](args.evidence/'interruption-proof.json',json.dumps(observed,indent=2,sort_keys=True))
    print(json.dumps(dict(interruption_recovery='PASS',cleanup='CLEANUP_SUCCESS',proof=observed),sort_keys=True))


if __name__=='__main__':main()
