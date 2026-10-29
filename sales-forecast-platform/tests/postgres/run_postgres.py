"""Opt-in disposable PostgreSQL foundation tests; never a native worker launcher.

No TCP, published ports, inherited database credentials, caller-selected modules,
production mounts or Docker socket in either container. New volumes only. The
host Docker daemon is trusted; no claim of protection against a malicious host.

Cleanup is operationally UNCONFIRMED after an ambiguous daemon mutation even if
the bounded ownership-scoped reconciliation currently observes no resources.
Negative tests may PASS by asserting that warning; the warning never becomes
execution permission or proof of daemon quiescence. No blanket pruning occurs.
"""
import argparse
from contextvars import ContextVar
import signal
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time
from uuid import uuid4

BASE = 'sha256:417063b71e35c7a9889c10ac2516c328f16a483e065ec858df101eb9728c2df2'
POSTGRES = 'sha256:6a35283e53fbe883da6a6ffbce8d3999dc66076cc5c4e2f1d21bdbd9e26bf86f'
WHEEL = 'psycopg2_binary-2.9.12-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.whl'
WHEEL_SHA256 = 'e4e184b1fb6072bf05388aa41c697e1b2d01b3473f107e7ec44f186a32cfd0b8'
ROOT = Path(__file__).resolve().parents[2]
_LIFECYCLE = ContextVar('postgres_test_lifecycle', default=None)


class CleanupUnconfirmed(RuntimeError):
    pass


def write_evidence(path, text):
    if path.absolute() != path.resolve():
        raise ValueError('symlink evidence output rejected')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(text)


PROXIES = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')


def command(args, *, timeout=30, stdin=None, check=True, build=False):
    # CLI argv only; never shell=True. Environment is needed only by the host's
    # Docker CLI. Nothing is forwarded into workers except explicit test values.
    env = dict(os.environ)
    for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
        env.pop(key, None)
    if args[0] == 'docker':
        args = ['docker', '--host=unix:///var/run/docker.sock', *args[1:]]
    if build:
        env['DOCKER_BUILDKIT'] = '0'
    lifecycle = _LIFECYCLE.get()
    attempt = None
    if lifecycle is not None:
        deadline = lifecycle.get('cleanup_deadline')
        if deadline is not None:
            timeout = min(timeout, max(.05, deadline-time.monotonic()))
            if time.monotonic() >= deadline:
                raise TimeoutError('bounded reconciliation expired')
        # Register possible daemon mutations BEFORE starting the local CLI.
        raw = args[2:] if args[:2] == ['docker', '--host=unix:///var/run/docker.sock'] else []
        kind, name = None, None
        if raw[:1] in (['run'], ['create']) and '--name' in raw:
            kind, name = 'container', raw[raw.index('--name')+1]
        elif raw[:1] in (['start'], ['restart']):
            kind, name = 'container', raw[-1]
        elif raw[:2] == ['volume', 'create']:
            kind, name = 'volume', raw[-1]
        if kind is not None:
            if not name.startswith('r7-05b3-'+lifecycle['run_hex']+'-'):
                raise ValueError('foreign creation rejected')
            attempt = dict(kind=kind, name=name, started_ns=time.time_ns(), state='PENDING')
            lifecycle['creation_attempts'].append(attempt)
    process = None
    try:
        process = subprocess.Popen(args, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
        if attempt is not None:
            attempt['local_pid'] = process.pid
        stdout, stderr = process.communicate(input=stdin, timeout=timeout)
        result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
        if attempt is not None:
            attempt.update(state='RETURNED' if result.returncode == 0 else 'DAEMON_UNRESOLVED', returned_ns=time.time_ns(), local_reaped=True, cli_returncode=process.returncode)
    except BaseException:
        if attempt is not None:
            attempt.update(state='DAEMON_UNRESOLVED', interrupted_ns=time.time_ns())
        if process is not None:
            # Own session/process group only. Reaping the CLI does NOT cancel Docker.
            try:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=3)
            if attempt is not None:
                attempt['local_reaped'] = process.returncode is not None
        raise
    if check and result.returncode:
        raise RuntimeError('disposable PostgreSQL command failed: '+result.stderr[-2000:])
    return result


def inspect_image(identity):
    value = json.loads(command(['docker', 'image', 'inspect', identity]).stdout)[0]
    if value['Id'] != identity or value['Os'] != 'linux' or value['Architecture'] != 'amd64':
        raise ValueError('exact test image unavailable')
    return value


def remove_owned(kind, name, run_id):
    """Never remove a pre-existing/foreign resource after a failed creation."""
    args = ['docker', kind, 'inspect', name]
    info = command(args, check=False)
    if info.returncode:
        return 'no such '+kind in info.stderr.lower()
    value = json.loads(info.stdout)[0]
    labels = value['Config']['Labels'] if kind == 'container' else value['Labels']
    if (labels or {}).get('r7_05b3_run') != str(run_id):
        return False
    delete = ['docker', 'rm', '-f', name] if kind == 'container' else ['docker', 'volume', 'rm', name]
    return command(delete, check=False).returncode == 0


def build_client(wheelhouse, work):
    inspect_image(BASE)
    wheel = wheelhouse/WHEEL
    if wheel.is_symlink() or hashlib.sha256(wheel.read_bytes()).hexdigest() != WHEEL_SHA256:
        raise ValueError('verified exact driver wheel required')
    context = work/'build'
    (context/'wheels').mkdir(parents=True)
    shutil.copyfile(wheel, context/'wheels'/WHEEL)
    for name in ('Dockerfile', 'requirements.lock'):
        shutil.copyfile(ROOT/'tests/postgres'/name, context/name)
    iid = work/'client-image-id'
    command(['docker', 'build', '--network=none', '--pull=false', '--iidfile', str(iid), str(context)], timeout=180, build=True)
    identity = iid.read_text().strip()
    inspect_image(identity)
    return identity


def run(*, allow, wheelhouse, evidence, _host_stage=None):
    if allow is not True:
        raise ValueError('explicit disposable-database authorization required')
    if evidence.absolute() != evidence.resolve():
        raise ValueError('symlink evidence directory rejected')
    evidence.mkdir(mode=0o700, parents=False, exist_ok=False)
    run_id = uuid4()
    prefix = 'r7-05b3-'+run_id.hex
    database = 'r7_05b3_'+run_id.hex
    data, socket, server = prefix+'-data', prefix+'-socket', prefix+'-server'
    containers = []
    volumes = []
    record = {'version': '1.0', 'run_id': str(run_id), 'database': database,
              'source_commit': command(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).stdout.strip(),
              'source_status': command(['git', '-C', str(ROOT), 'status', '--porcelain']).stdout,
              'operations': [], 'native_execution': False, 'production_connections': False}
    record.update(run_hex=run_id.hex, creation_attempts=[])
    token = _LIFECYCLE.set(record)
    previous_term = signal.getsignal(signal.SIGTERM)
    def interrupted(signum, frame):
        raise KeyboardInterrupt('test runner interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    passwords = {role: 'test-only-'+secrets.token_hex(20) for role in ('admin', 'migrator', 'writer', 'reader')}
    empty_proxies = [item for key in PROXIES for item in ('-e', key+'=')]
    isolation = ['--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--cpus=1', '--pids-limit=64']

    def ready():
        end = time.monotonic()+45
        while time.monotonic() < end:
            final = command(['docker', 'exec', server, 'cat', '/proc/1/comm'], check=False)
            if final.stdout.strip() == 'postgres':
                result = command(['docker', 'exec', '-e', 'PGPASSWORD='+passwords['admin'], server,
                    'psql', '-h', '/var/run/postgresql', '-U', 'r7_test_owner', '-d', database, '-At', '-c',
                    'SELECT current_database(),current_user,version(),system_identifier FROM pg_control_system()'], check=False)
                if result.returncode == 0:
                    return result.stdout.strip()
            time.sleep(.2)
        raise TimeoutError('final disposable PostgreSQL server not ready')

    try:
        inspect_image(POSTGRES)
        client = build_client(wheelhouse.resolve(strict=True), evidence)
        record.update(postgres_image=POSTGRES, client_image=client, base_image=BASE, driver_wheel_sha256=WHEEL_SHA256)
        for name, options in ((data, []), (socket, ['--driver=local', '--opt=type=tmpfs', '--opt=device=tmpfs', '--opt=o=size=16m,mode=1777'])):
            absent = command(['docker', 'volume', 'inspect', name], check=False)
            if absent.returncode == 0 or 'no such volume' not in absent.stderr.lower():
                raise ValueError('fresh disposable volume required')
            command(['docker', 'volume', 'create', '--label', 'r7_05b3_run='+str(run_id), *options, name])
            volumes.append(name)
        provision = prefix+'-provision'
        containers.append(provision)
        command(['docker', 'run', '--name', provision, '--label', 'r7_05b3_run='+str(run_id), '--network=none', '--read-only', '--user=0:0',
            '--cap-drop=ALL', '--cap-add=CHOWN', '--security-opt=no-new-privileges', '--cpus=1',
            '--memory=64m', '--memory-swap=64m', '--pids-limit=32', *empty_proxies,
            '--mount', 'type=volume,src='+data+',dst=/var/lib/postgresql/data', '--entrypoint', 'chown', POSTGRES, '999:999', '/var/lib/postgresql/data'])
        command(['docker', 'rm', provision])
        containers.remove(provision)
        containers.append(server)
        server_args = ['docker', 'run', '-d', '--name', server, '--label', 'r7_05b3_run='+str(run_id), *isolation,
            '--user=999:999', '--memory=512m', '--memory-swap=512m', '--tmpfs=/tmp:rw,noexec,nosuid,size=32m',
            '--mount', 'type=volume,src='+data+',dst=/var/lib/postgresql/data',
            '--mount', 'type=volume,src='+socket+',dst=/var/run/postgresql', *empty_proxies,
            '-e', 'PGDATA=/var/lib/postgresql/data/pgdata', '-e', 'POSTGRES_DB='+database,
            '-e', 'POSTGRES_USER=r7_test_owner', '-e', 'POSTGRES_PASSWORD='+passwords['admin'],
            '-e', 'POSTGRES_INITDB_ARGS=--auth-local=scram-sha-256 --auth-host=scram-sha-256', POSTGRES,
            '-c', 'listen_addresses=', '-c', 'shared_buffers=32MB', '-c', 'max_connections=20',
            '-c', 'fsync=on', '-c', 'synchronous_commit=on', '-c', 'full_page_writes=on']
        command(server_args)
        record['cluster_before_restart'] = ready()
        variables = [item for role in ('migrator', 'writer', 'reader') for item in ('-v', role+'_password='+passwords[role])]
        command(['docker', 'exec', '-i', '-e', 'PGPASSWORD='+passwords['admin'], server,
            'psql', '-h', '/var/run/postgresql', '-U', 'r7_test_owner', '-d', database, '-v', 'ON_ERROR_STOP=1',
            '-v', 'test_database='+database, '-v', 'test_identity=r7_05b3_disposable:'+str(run_id), *variables],
            stdin=(ROOT/'tests/postgres/bootstrap.sql').read_text())
        record['server_inspect'] = json.loads(command(['docker', 'inspect', server]).stdout)[0]
        # Only redact test passwords; no production credentials are read.
        record['server_inspect']['Config']['Env'] = [s.split('=')[0]+'=<test-secret>' if 'PASSWORD=' in s else s for s in record['server_inspect']['Config']['Env']]
        envfile = evidence/'test-client.env'
        with open(envfile, 'x', opener=lambda path, flags: os.open(path, flags, 0o600)) as handle:
            handle.write('R7_PG_INSTANCE='+str(run_id)+'\nR7_PG_DATABASE='+database+'\n')
            handle.write(''.join('R7_PG_'+role.upper()+'_PASSWORD='+password+'\n' for role, password in passwords.items()))
        for stage, module in (('foundation', 'tests/postgres/test_forecast_evidence_postgres.py'),
                              ('restart', 'tests/postgres/test_forecast_evidence_restart.py')):
            if stage == 'restart':
                command(['docker', 'restart', '--time=5', server], timeout=20)
                record['cluster_after_restart'] = ready()
                if record['cluster_before_restart'] != record['cluster_after_restart']:
                    raise ValueError('disposable cluster changed on restart')
            name = prefix+'-'+stage
            containers.append(name)
            args = ['docker', 'run', '--name', name, '--label', 'r7_05b3_run='+str(run_id), *isolation,
                '--user=1000:1000', '--memory=1g', '--memory-swap=1g', '--ulimit', 'cpu=180:180',
                '--tmpfs=/tmp:rw,noexec,nosuid,size=256m', '--mount', 'type=volume,src='+socket+',dst=/run/r7pg,readonly',
                '--mount', 'type=bind,src='+str(ROOT/'src')+',dst=/code/src,readonly',
                '--mount', 'type=bind,src='+str(ROOT/'tests')+',dst=/code/tests,readonly',
                '--workdir=/code', '--env-file', str(envfile), *empty_proxies,
                '-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', 'PYTHONPATH=/code', '-e', 'OMP_NUM_THREADS=1',
                '-e', 'OPENBLAS_NUM_THREADS=1', '-e', 'HOME=/tmp', '-e', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1',
                '--entrypoint', 'python', client,
                '-m', 'pytest', '-q', '--tb=short', '--maxfail=1', '-p', 'no:cacheprovider', module]
            result = command(args, timeout=240, check=False)
            write_evidence(evidence/(stage+'.stdout'), result.stdout)
            write_evidence(evidence/(stage+'.stderr'), result.stderr)
            info = json.loads(command(['docker', 'inspect', name]).stdout)[0]
            info['Config']['Env'] = [s.split('=')[0]+'=<test-secret>' if 'PASSWORD=' in s else s for s in info['Config']['Env']]
            record['operations'].append({'stage': stage, 'exit_code': result.returncode, 'inspect': info})
            print(result.stdout, flush=True)
            print(result.stderr, flush=True)
            if result.returncode:
                raise RuntimeError('bounded PostgreSQL '+stage+' tests failed')
            command(['docker', 'rm', name])
            containers.remove(name)
        if _host_stage is not None:
            _host_stage(server=server, socket=socket, client=client, envfile=envfile,
                run_id=run_id, database=database, record=record)
        record['result'] = 'PASS'
    except BaseException as error:
        record['result'] = 'FAILED'
        record['error_type'] = type(error).__name__
        raise
    finally:
        # Keep named volumes until the bounded container reconciliation finishes.
        # A timed-out create can still attach them after the local CLI is gone.
        attempts = record['creation_attempts']
        owned_containers = list(dict.fromkeys(containers+[x['name'] for x in attempts if x['kind']=='container']))
        owned_volumes = list(dict.fromkeys(volumes+[x['name'] for x in attempts if x['kind']=='volume']))
        ambiguous = any(x['state'] != 'RETURNED' for x in attempts)
        record['cleanup_deadline'] = time.monotonic()+10
        cleanup = []
        try:
            while True:
                round_ok = True
                for name in reversed(owned_containers):
                    try:
                        round_ok = remove_owned('container', name, run_id) and round_ok
                    except Exception:
                        round_ok = False
                if not ambiguous or time.monotonic() >= record['cleanup_deadline']-.3:
                    cleanup.append(round_ok)
                    break
                time.sleep(.25)
            record['cleanup_deadline'] = time.monotonic()+6
            for name in reversed(owned_volumes):
                try:
                    cleanup.append(remove_owned('volume', name, run_id))
                except Exception:
                    cleanup.append(False)
        finally:
            record.pop('cleanup_deadline', None)
            _LIFECYCLE.reset(token)
            signal.signal(signal.SIGTERM, previous_term)
        for key, query in (('remaining_containers', ['ps','-aq','--filter','label=r7_05b3_run='+str(run_id)]),
                           ('remaining_volumes', ['volume','ls','-q','--filter','label=r7_05b3_run='+str(run_id)])):
            try:
                record[key] = command(['docker', *query], timeout=2).stdout.strip()
            except Exception:
                record[key] = 'UNAVAILABLE'
        record['possible_owned_containers'] = owned_containers
        record['possible_owned_volumes'] = owned_volumes
        # No finite polling period proves an interrupted daemon request cannot
        # finish later. Absence is insufficient while any creation is unresolved.
        record['cleanup_passed'] = bool(cleanup) and all(cleanup) and not ambiguous and not record['remaining_containers'] and not record['remaining_volumes']
        record['cleanup_status'] = 'CLEANUP_SUCCESS' if record['cleanup_passed'] else 'CLEANUP_UNCONFIRMED'
        write_evidence(evidence/'run-manifest.json', json.dumps(record, indent=2, sort_keys=True))
        if not record['cleanup_passed']:
            raise CleanupUnconfirmed('CLEANUP_UNCONFIRMED: inspect recorded exact owned identities') from None



if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-disposable-postgres', action='store_true')
    parser.add_argument('--wheelhouse', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    run(allow=args.allow_disposable_postgres, wheelhouse=args.wheelhouse, evidence=args.evidence)
