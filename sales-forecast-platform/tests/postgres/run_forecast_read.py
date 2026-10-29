"""Explicit B5-1 synthetic PostgreSQL tests via the UNCHANGED accepted harness."""
import argparse
import json
from pathlib import Path
import runpy


def run(wheelhouse, evidence):
    pg = runpy.run_path(str(Path(__file__).with_name('run_postgres.py')))
    def stage(*, socket, client, envfile, run_id, record, **_):
        name='r7-05b3-'+run_id.hex+'-canonical-reader'
        args=['docker','run','--name',name,'--label','r7_05b3_run='+str(run_id),
            '--network=none','--read-only','--user=1000:1000','--cap-drop=ALL',
            '--security-opt=no-new-privileges','--cpus=1','--memory=1g','--memory-swap=1g',
            '--pids-limit=64','--ulimit','cpu=180:180','--tmpfs=/tmp:rw,noexec,nosuid,size=256m',
            '--mount','type=volume,src='+socket+',dst=/run/r7pg,readonly',
            '--mount','type=bind,src='+str(pg['ROOT']/'src')+',dst=/code/src,readonly',
            '--mount','type=bind,src='+str(pg['ROOT']/'tests')+',dst=/code/tests,readonly',
            '--workdir=/code','--env-file',str(envfile)]
        args += [x for key in pg['PROXIES'] for x in ('-e',key+'=')]
        args += ['-e','PYTHONDONTWRITEBYTECODE=1','-e','PYTHONPATH=/code','-e','PYTEST_DISABLE_PLUGIN_AUTOLOAD=1',
            '-e','OMP_NUM_THREADS=1','-e','OPENBLAS_NUM_THREADS=1','-e','HOME=/tmp','--entrypoint','python',client,
            '-m','pytest','-q','--tb=short','--maxfail=1','-p','no:cacheprovider',
            'tests/runtime/test_canonical_forecast_read_contract.py','tests/postgres/test_canonical_forecast_read.py']
        try:
            result=pg['command'](args,timeout=240,check=False)
            pg['write_evidence'](evidence/'reader.stdout',result.stdout)
            pg['write_evidence'](evidence/'reader.stderr',result.stderr)
            info=json.loads(pg['command'](['docker','inspect',name]).stdout)[0]
            info['Config']['Env']=[v.split('=')[0]+'=<test-secret>' if 'PASSWORD=' in v else v for v in info['Config']['Env']]
            record['reader_tests']=dict(exit_code=result.returncode,inspect=info)
            print(result.stdout,flush=True)
            if result.returncode:raise RuntimeError('reader acceptance tests failed')
        finally:
            if not pg['remove_owned']('container',name,run_id):
                raise pg['CleanupUnconfirmed']('reader cleanup unconfirmed')
    pg['run'](allow=True,wheelhouse=wheelhouse,evidence=evidence,_host_stage=stage)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-disposable-postgres',action='store_true')
    parser.add_argument('--wheelhouse',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    args=parser.parse_args()
    if not args.allow_disposable_postgres:parser.error('explicit test authorization required')
    run(args.wheelhouse,args.evidence)
