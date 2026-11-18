"""REVIEW-ONLY fork of accepted runner; NEVER execute against Docker.

No TCP, published ports, inherited database credentials, caller-selected modules,
production mounts or Docker socket in either container. New volumes only. The
host Docker daemon is trusted; no claim of protection against a malicious host.

Cleanup is operationally UNCONFIRMED after an ambiguous daemon mutation even if
the bounded ownership-scoped reconciliation currently observes no resources.
Negative tests may PASS by asserting that warning; the warning never becomes
execution permission or proof of daemon quiescence. No blanket pruning occurs.
"""
import argparse
from redaction_finalization_candidate import finalize_manifest_candidate
from contextvars import ContextVar
import signal
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

BASE = 'sha256:417063b71e35c7a9889c10ac2516c328f16a483e065ec858df101eb9728c2df2'
POSTGRES = 'sha256:6a35283e53fbe883da6a6ffbce8d3999dc66076cc5c4e2f1d21bdbd9e26bf86f'
WHEEL = 'psycopg2_binary-2.9.12-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.whl'
WHEEL_SHA256 = 'e4e184b1fb6072bf05388aa41c697e1b2d01b3473f107e7ec44f186a32cfd0b8'
ROOT = Path(__file__).resolve().parents[2]
_LIFECYCLE = ContextVar('postgres_test_lifecycle', default=None)
_TEST_SECRETS = ContextVar('postgres_test_secret_values', default=())


class CleanupUnconfirmed(RuntimeError):
    pass


def redact_test_secrets(value):
    """Remove exact disposable password values before publishing any text."""
    if type(value) is not str:
        raise TypeError('text expected')
    matches = 0
    for secret in _TEST_SECRETS.get():
        if secret and secret in value:
            matches += value.count(secret)
            value = value.replace(secret, '<redacted-disposable-secret>')
    if matches:
        lifecycle = _LIFECYCLE.get()
        if lifecycle is not None:
            lifecycle['secret_redactions'] = lifecycle.get('secret_redactions', 0) + matches
    return value


def write_evidence(path, text):
    text = redact_test_secrets(text)
    if path.absolute() != path.resolve():
        raise ValueError('symlink evidence output rejected')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(text)


def publish_terminal_manifest_candidate(path, text):
    """Stage all bytes before atomically creating the one accepted manifest.

    Same-directory hard-link publication preserves the O_EXCL/no-overwrite
    property. An incomplete write is never published at run-manifest.json.
    This is candidate-only; host filesystem and crash durability remain outside
    the proof of synthetic tests.
    """
    staging = path.with_name('.run-manifest-' + secrets.token_hex(12) + '.tmp')
    try:
        write_evidence(staging, text)
        # Link is atomic on the local filesystem: if destination already
        # exists, do not overwrite someone else's evidence, even a symlink.
        os.link(staging, path, follow_symlinks=False)
    except BaseException:
        # Some injected fault models create the hard link then raise. In that
        # ambiguous case remove ONLY a destination known to share the exact
        # staging inode. Never unlink an unrelated/pre-existing destination.
        try:
            if (not staging.is_symlink() and staging.exists()
                    and not path.is_symlink() and path.exists()
                    and os.path.samefile(staging, path)):
                path.unlink()
        except BaseException:
            pass  # Cannot prove cleanup after a hostile host/filesystem race.
        raise
    finally:
        # An error AFTER successful publication must not make a valid PASS
        # manifest appear failed. A leftover non-accepted staging name is
        # safer than throwing here after the terminal commit point.
        try:
            if not staging.is_symlink():
                staging.unlink(missing_ok=True)
        except BaseException:
            pass


PROXIES = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')


def command(args, *, timeout=30, stdin=None, check=True, build=False):
    # CANDIDATE HARD STOP: never launch a subprocess from the review fork.
    # Mock tests replace this function with a bounded synthetic stub.
    # Original CLI implementation below is retained solely for source diff review.
    raise RuntimeError('OFFLINE_REVIEW_ONLY: candidate command execution forbidden')
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
    sanitized_timeout = None
    try:
        process = subprocess.Popen(args, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
        if attempt is not None:
            attempt['local_pid'] = process.pid
        stdout, stderr = process.communicate(input=stdin, timeout=timeout)
        result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
        if attempt is not None:
            attempt.update(state='RETURNED' if result.returncode == 0 else 'DAEMON_UNRESOLVED', returned_ns=time.time_ns(), local_reaped=True, cli_returncode=process.returncode)
    except BaseException as error:
        if attempt is not None:
            attempt.update(state='DAEMON_UNRESOLVED', interrupted_ns=time.time_ns())
        # R2-01: a *secondary* failure during bounded local CLI cleanup must
        # never escape from within the raw TimeoutExpired exception handler.
        # Such an escape implicitly chains the original credential-bearing
        # TimeoutExpired (including .cmd, .stdout and .stderr) into tracebacks.
        # Record only fixed stage codes, not raw secondary error objects/text.
        secondary_interrupt = None
        local_reap_issues = []
        local_reaped_confirmed = False
        if process is not None:
            # Own session/process group only. Reaping the CLI does NOT cancel
            # an uncertain Docker daemon request; leave that state unresolved.
            try:
                polled = process.poll()
            except BaseException as reap_error:
                local_reap_issues.append('POLL_EXCEPTION')
                if isinstance(reap_error, KeyboardInterrupt):
                    secondary_interrupt = secondary_interrupt or 'KEYBOARD_INTERRUPT'
                elif isinstance(reap_error, SystemExit):
                    secondary_interrupt = secondary_interrupt or 'SYSTEM_EXIT'
                # If poll is unreliable, avoid killing a potentially reused
                # process group. Still attempt the bounded local wait below.
                should_kill = False
            else:
                should_kill = polled is None
                if not should_kill:
                    local_reaped_confirmed = True
            if should_kill:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except BaseException as reap_error:
                    local_reap_issues.append('KILLPG_EXCEPTION')
                    if isinstance(reap_error, KeyboardInterrupt):
                        secondary_interrupt = secondary_interrupt or 'KEYBOARD_INTERRUPT'
                    elif isinstance(reap_error, SystemExit):
                        secondary_interrupt = secondary_interrupt or 'SYSTEM_EXIT'
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                # Never expose a second TimeoutExpired (which may retain argv).
                local_reap_issues.append('WAIT_TIMEOUT')
            except BaseException as reap_error:
                local_reap_issues.append('WAIT_EXCEPTION')
                if isinstance(reap_error, KeyboardInterrupt):
                    secondary_interrupt = secondary_interrupt or 'KEYBOARD_INTERRUPT'
                elif isinstance(reap_error, SystemExit):
                    secondary_interrupt = secondary_interrupt or 'SYSTEM_EXIT'
            else:
                local_reaped_confirmed = True
            if attempt is not None:
                # A PID or returncode alone is not proof that a failed
                # communicate() reaped the local child process.
                attempt['local_reaped'] = local_reaped_confirmed
                if local_reap_issues:
                    attempt['local_reap_issues'] = local_reap_issues
        if isinstance(error, subprocess.TimeoutExpired):
            # Preserve the class of a secondary control-flow interrupt using a
            # *new*, public credential-free exception. No secondary error text,
            # raw TimeoutExpired, .cmd or output is retained in public chains.
            # All new exceptions are raised OUTSIDE this exception handler.
            if secondary_interrupt == 'KEYBOARD_INTERRUPT':
                sanitized_timeout = KeyboardInterrupt(
                    'disposable PostgreSQL timeout cleanup interrupted; details suppressed')
            elif secondary_interrupt == 'SYSTEM_EXIT':
                sanitized_timeout = SystemExit(
                    'disposable PostgreSQL timeout cleanup interrupted; details suppressed')
            else:
                sanitized_timeout = RuntimeError(
                    'disposable PostgreSQL command timed out; command details suppressed')
        else:
            raise
    if sanitized_timeout is not None:
        raise sanitized_timeout
    if check and result.returncode:
        raise RuntimeError('disposable PostgreSQL command failed: '+redact_test_secrets(result.stderr[-2000:]))
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


def remove_transient_credential_file(path):
    """Fail closed for uncertain removal; never include plaintext in evidence."""
    try:
        if path.is_symlink():
            return 'UNCONFIRMED'
        if path.exists():
            path.unlink()
            return 'REMOVED'
        return 'NOT_PRESENT'
    except Exception:
        return 'UNCONFIRMED'


def normalize_transient_credential_cleanup_status(status):
    """Trust only literal built-in str statuses; never call custom __eq__.

    Comparing an arbitrary return value to a string can execute attacker-defined
    equality code or accept a forged REMOVED/NOT_PRESENT signal. Keep output
    JSON-safe, deterministic, and fail-closed for every non-exact input.
    """
    if type(status) is str and status in ('REMOVED', 'NOT_PRESENT'):
        return status
    return 'UNCONFIRMED'


def verified_clean_source():
    """Fail closed on untrusted/dirty Git provenance before Docker mutations.

    A Windows .git pointer that WSL Git cannot read is rejected. This check
    establishes a clean source snapshot, not a trust boundary against a
    malicious host or Docker daemon.
    """
    top = command(['git', '-C', str(ROOT), 'rev-parse', '--show-toplevel']).stdout.strip()
    # This repository keeps the Python project in sales-forecast-platform/.
    # Validate the entire containing Git worktree, not only the subdirectory.
    try:
        root_matches = (bool(top)
                        and Path(top).resolve(strict=True) == ROOT.parent.resolve(strict=True)
                        and ROOT.name == 'sales-forecast-platform')
    except (OSError, RuntimeError):
        root_matches = False
    if not root_matches:
        raise ValueError('disposable test source must be expected Git worktree')
    commit = command(['git', '-C', str(ROOT), 'rev-parse', '--verify', 'HEAD']).stdout.strip()
    if len(commit) not in (40, 64) or any(ch not in '0123456789abcdef' for ch in commit):
        raise ValueError('invalid source commit identity')
    status = command(['git', '-C', str(ROOT), 'status', '--porcelain',
                      '--untracked-files=all']).stdout
    if status:
        raise ValueError('disposable test source checkout must be clean')
    return commit


def run(*, allow, wheelhouse, evidence, _host_stage=None):
    if allow is not True:
        raise ValueError('explicit disposable-database authorization required')
    if evidence.absolute() != evidence.resolve():
        raise ValueError('symlink evidence directory rejected')
    # Validate provenance BEFORE creating evidence or touching Docker.
    source_commit = verified_clean_source()
    evidence.mkdir(mode=0o700, parents=False, exist_ok=False)
    run_id = uuid4()
    prefix = 'r7-05b3-'+run_id.hex
    database = 'r7_05b3_'+run_id.hex
    data, socket, server = prefix+'-data', prefix+'-socket', prefix+'-server'
    containers = []
    volumes = []
    envfile = evidence/'test-client.env'  # transient credentials, NEVER evidence
    record = {'version': '1.0', 'run_id': str(run_id), 'database': database,
              'source_commit': source_commit,
              'source_status': '', 'result': 'FAILED',
              'cleanup_passed': False, 'cleanup_status': 'CLEANUP_UNCONFIRMED',
              'operations': [], 'native_execution': False, 'production_connections': False}
    record.update(run_hex=run_id.hex, creation_attempts=[])
    token = _LIFECYCLE.set(record)
    inspection_token = None  # suspend resource-ownership context during read-only listings
    credential_cleanup_attempted = False
    # Cooperative SIGTERM deferral ONLY during the short owned-envfile unlink.
    # This protects the intent-flag -> helper-call seam; a hard kill, another
    # process, or arbitrary KeyboardInterrupt cannot be made atomic by Python.
    deferred_term = {'active': False, 'pending': False}
    secret_token = None
    handler_attempted = False
    handler_restore_attempted = False
    handler_restore_failed = False
    previous_term = None
    # Body completion is local bookkeeping, never terminal PASS evidence.
    body_passed = False

    def restore_handler_once():
        """Attempt SIGTERM restoration once; never silently approve failure."""
        nonlocal handler_restore_attempted, handler_restore_failed
        if handler_attempted and previous_term is not None and not handler_restore_attempted:
            # Mark BEFORE invoking signal.signal: a restoration fault must not
            # produce an unbounded retry or an accepted success manifest.
            handler_restore_attempted = True
            try:
                signal.signal(signal.SIGTERM, previous_term)
            except BaseException:
                handler_restore_failed = True
                record['handler_restoration'] = 'UNCONFIRMED'
                record['result'] = 'FAILED'
                record['cleanup_passed'] = False
                record['cleanup_status'] = 'CLEANUP_UNCONFIRMED'
            else:
                record['handler_restoration'] = 'RESTORED'

    try:
        previous_term = signal.getsignal(signal.SIGTERM)
        handler_attempted = False
        def interrupted(signum, frame):
            if deferred_term['active']:
                deferred_term['pending'] = True
                return
            raise KeyboardInterrupt('test runner interrupted')
        handler_attempted = True
        signal.signal(signal.SIGTERM, interrupted)
        passwords = {role: 'test-only-'+secrets.token_hex(20) for role in ('admin', 'migrator', 'writer', 'reader')}
        secret_token = _TEST_SECRETS.set(tuple(passwords.values()))
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
                try:
                    write_evidence(evidence/(stage+'.stdout'), result.stdout)
                    write_evidence(evidence/(stage+'.stderr'), result.stderr)
                except BaseException:
                    # Even if resources later clean up, stage evidence is
                    # incomplete. Preserve the original I/O exception.
                    record['evidence_status'] = 'EVIDENCE_UNAVAILABLE'
                    record['result'] = 'FAILED'
                    raise
                info = json.loads(command(['docker', 'inspect', name]).stdout)[0]
                info['Config']['Env'] = [s.split('=')[0]+'=<test-secret>' if 'PASSWORD=' in s else s for s in info['Config']['Env']]
                record['operations'].append({'stage': stage, 'exit_code': result.returncode, 'inspect': info})
                print(redact_test_secrets(result.stdout), flush=True)
                print(redact_test_secrets(result.stderr), flush=True)
                if result.returncode:
                    raise RuntimeError('bounded PostgreSQL '+stage+' tests failed')
                command(['docker', 'rm', name])
                containers.remove(name)
            if _host_stage is not None:
                _host_stage(server=server, socket=socket, client=client, envfile=envfile,
                    run_id=run_id, database=database, record=record)
            body_passed = True
        except BaseException as error:
            record['result'] = 'FAILED'
            record['error_type'] = type(error).__name__
            raise
        finally:
            # F04-M2: a successful body is NOT terminal success. Keep the
            # lifecycle fail-closed during all resource reconciliation, owned
            # credential unlink, and prior SIGTERM handler restoration. In
            # particular, BaseException/KeyboardInterrupt escaping cleanup
            # cannot leave a retained in-memory PASS record.
            record['result'] = 'FAILED'
            record['cleanup_passed'] = False
            record['cleanup_status'] = 'CLEANUP_UNCONFIRMED'
            # Keep named volumes until the bounded container reconciliation finishes.
            # A timed-out create can still attach them after the local CLI is gone.
            attempts = record['creation_attempts']
            owned_containers = list(dict.fromkeys(containers + record.get('extra_containers', [])
                + [x['name'] for x in attempts if x['kind']=='container']))
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
            # Match accepted runner: no active lifecycle context while issuing
            # final read-only inventory queries and publishing evidence.
            # Keep the original binding token for outer finally restoration.
            inspection_token = _LIFECYCLE.set(None)
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
            # Do not retain plaintext disposable credentials in the evidence archive.
            # This is only best-effort filesystem cleanup, not secure erase of bytes
            # from an underlying disk, Docker daemon metadata or host privileges.
            # If the credentials file cannot be removed, mark the run unaccepted.
            # Defer the installed SIGTERM handler *before* marking the unlink
            # attempt. A signal after the flag but before the helper now queues
            # the interrupt instead of skipping the owned file removal.
            deferred_term['active'] = True
            try:
                credential_cleanup_attempted = True  # no unsafe duplicate unlink
                try:
                    removal_status = remove_transient_credential_file(envfile)
                except BaseException:
                    record['credential_file_cleanup'] = 'UNCONFIRMED'
                    record['result'] = 'FAILED'
                    record['cleanup_passed'] = False
                    record['cleanup_status'] = 'CLEANUP_UNCONFIRMED'
                    raise
                record['credential_file_cleanup'] = (
                    normalize_transient_credential_cleanup_status(removal_status))
                if record['credential_file_cleanup'] == 'UNCONFIRMED':
                    record['result'] = 'FAILED'
                    record['cleanup_passed'] = False
                    record['cleanup_status'] = 'CLEANUP_UNCONFIRMED'
            finally:
                if deferred_term['pending']:
                    # Interrupted finalization cannot retain a PASS result,
                    # regardless of whether the owned envfile was removed.
                    record['result'] = 'FAILED'
                    record['cleanup_passed'] = False
                    record['cleanup_status'] = 'CLEANUP_UNCONFIRMED'
                deferred_term['active'] = False
            if deferred_term['pending']:
                deferred_term['pending'] = False
                raise KeyboardInterrupt('deferred SIGTERM during credential cleanup')
            # No success evidence can be published until the previous SIGTERM
            # handler has been restored. A restoration fault fails closed.
            # Preserve a pending original exception rather than masking it
            # when *both* body and restoration fail.
            pending_error = sys.exc_info()[1]
            restore_handler_once()
            if handler_restore_failed and pending_error is None:
                raise CleanupUnconfirmed(
                    'CLEANUP_UNCONFIRMED: SIGTERM handler restoration not confirmed') from None
            # Promote to PASS only after every terminal success prerequisite.
            # No exception raised during reconciliation reaches this point.
            if (body_passed and record['cleanup_passed']
                    and type(record.get('credential_file_cleanup')) is str
                    and record['credential_file_cleanup'] in ('REMOVED', 'NOT_PRESENT')
                    and not handler_restore_failed):
                record['result'] = 'PASS'
            # Detect and redact secrets even in unexpected metadata keys.
            # EVIDENCE_UNAVAILABLE is distinct from owned-resource cleanup
            # status: cleanup can succeed even when publication cannot.
            if record.get('evidence_status') != 'EVIDENCE_UNAVAILABLE':
                record['evidence_status'] = 'AVAILABLE'
            try:
                manifest_text, rejected = finalize_manifest_candidate(
                    record, tuple(passwords.values()))
                publish_terminal_manifest_candidate(
                    evidence/'run-manifest.json', manifest_text)
            except BaseException:
                record['result'] = 'FAILED'
                record['evidence_status'] = 'EVIDENCE_UNAVAILABLE'
                if pending_error is not None:
                    # Do not replace the primary preflight/test failure with
                    # a secondary serialization, staging or publication fault.
                    raise pending_error.with_traceback(
                        pending_error.__traceback__) from None
                raise
            if rejected and not (handler_restore_failed and pending_error is not None):
                raise CleanupUnconfirmed('CLEANUP_UNCONFIRMED: owned resource or credential cleanup not confirmed') from None
    finally:
        try:
            # A reconciliation interruption must never skip a bounded attempt
            # to remove only the owned transient credential file. This path
            # is reached after an exceptional exit before normal cleanup.
            if not credential_cleanup_attempted:
                deferred_term['active'] = True
                try:
                    credential_cleanup_attempted = True
                    try:
                        removal_status = remove_transient_credential_file(envfile)
                    except BaseException:
                        removal_status = 'UNCONFIRMED'
                    record['credential_file_cleanup'] = (
                        normalize_transient_credential_cleanup_status(removal_status))
                    if record['credential_file_cleanup'] == 'UNCONFIRMED':
                        record['result'] = 'FAILED'
                        record['cleanup_passed'] = False
                        record['cleanup_status'] = 'CLEANUP_UNCONFIRMED'
                finally:
                    if deferred_term['pending']:
                        record['result'] = 'FAILED'
                        record['cleanup_passed'] = False
                        record['cleanup_status'] = 'CLEANUP_UNCONFIRMED'
                    deferred_term['active'] = False
                if deferred_term['pending']:
                    deferred_term['pending'] = False
                    raise KeyboardInterrupt('deferred SIGTERM during credential cleanup')
        finally:
            pending_exit_error = sys.exc_info()[1]
            try:
                restore_handler_once()
                if handler_restore_failed and pending_exit_error is None:
                    raise CleanupUnconfirmed(
                        'CLEANUP_UNCONFIRMED: SIGTERM handler restoration not confirmed') from None
            finally:
                try:
                    if secret_token is not None:
                        _TEST_SECRETS.reset(secret_token)
                finally:
                    try:
                        if inspection_token is not None:
                            _LIFECYCLE.reset(inspection_token)
                    finally:
                        _LIFECYCLE.reset(token)



# REVIEW-ONLY CANDIDATE: no CLI entrypoint. Never execute against Docker.
