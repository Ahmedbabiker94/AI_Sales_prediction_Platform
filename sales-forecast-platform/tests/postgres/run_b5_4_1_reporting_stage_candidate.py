"""REVIEW-ONLY candidate B5-4-1 reporting-stage source-parity adapter.

The body of reporting_stage is copied from the PINNED operational wrapper
(blob 040d0d30bb719d646eb350b229ccd9a4c5f2bf79), with its local
_LIFECYCLE import redirected to the same review-only candidate runner module
as command, write_evidence, and redact_test_secrets. The ONLY additional
intentional security delta is a fail-closed try/except around both output
writes: incomplete evidence is EVIDENCE_UNAVAILABLE, preserving the error.

Do not import the frozen runner. Do not add a CLI entrypoint. The candidate
command function has an unconditional OFFLINE_REVIEW_ONLY hard stop; tests
replace command with a synthetic stub. Not approved for Docker/PG execution.
"""
from pathlib import Path
from run_postgres_security_patch_candidate import (
    ROOT, PROXIES, POSTGRES, BASE, command, write_evidence,
    redact_test_secrets,
)

TESTS = (
    'tests/postgres/test_stored_forecast_accuracy_postgres.py',
    'tests/postgres/test_canonical_reporting_read.py',
    'tests/postgres/test_r7_05b5_4_1_host_reporting_requests.py',
)


def reporting_stage(*, server, socket, client, envfile, run_id, database, record):
    # The foundation stage already performed the checked migration; restart
    # stage verified a NEW client with the same durable, isolated cluster.
    prefix = 'r7-05b3-' + run_id.hex
    name = prefix + '-b5-4-1'
    isolation = [
        '--network=none', '--read-only', '--cap-drop=ALL',
        '--security-opt=no-new-privileges', '--cpus=1', '--pids-limit=64',
    ]
    empty_proxies = [
        item for key in PROXIES for item in ('-e', key + '=')
    ]
    # run_postgres.run owns the lifecycle and must also own this extra
    # container; its callback can append a run-scoped container identity.
    # Ensure an independent cleanup attempt even on failed/aborted pytest.
    args = [
        'docker', 'run', '--name', name,
        '--label', 'r7_05b3_run=' + str(run_id), *isolation,
        '--user=1000:1000', '--memory=1g', '--memory-swap=1g',
        '--ulimit', 'cpu=180:180',
        '--tmpfs=/tmp:rw,noexec,nosuid,size=256m',
        '--mount', 'type=volume,src=' + socket + ',dst=/run/r7pg,readonly',
        '--mount', 'type=bind,src=' + str(ROOT/'src') + ',dst=/code/src,readonly',
        '--mount', 'type=bind,src=' + str(ROOT/'tests') + ',dst=/code/tests,readonly',
        '--workdir=/code', '--env-file', str(envfile), *empty_proxies,
        '-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', 'PYTHONPATH=/code',
        '-e', 'OMP_NUM_THREADS=1', '-e', 'OPENBLAS_NUM_THREADS=1',
        '-e', 'HOME=/tmp', '-e', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1',
        '--entrypoint', 'python', client, '-m', 'pytest', '-q',
        '--tb=short', '--maxfail=1', '-p', 'no:cacheprovider', *TESTS,
    ]
    from run_postgres_security_patch_candidate import _LIFECYCLE
    lifecycle = _LIFECYCLE.get()
    if lifecycle is None or lifecycle.get('run_hex') != run_id.hex:
        raise RuntimeError('original disposable run lifecycle required')
    # Add before Docker invocation so the original harness cleanup owns the
    # name even if Docker's run request is interrupted or ambiguous.
    # Lifecycle's retry reconciliation will verify run labels.
    created = lifecycle.setdefault('extra_containers', [])
    created.append(name)
    result = command(args, timeout=240, check=False)
    evidence_dir = Path(record['b5_4_1_evidence_dir'])
    try:
        write_evidence(evidence_dir/'b5-4-1.stdout', result.stdout)
        write_evidence(evidence_dir/'b5-4-1.stderr', result.stderr)
    except BaseException:
        # F04-R4-LOW: a partial B5 output is not complete evidence, even if
        # owned resources later reconcile. Mirror foundation/restart status.
        # Bare raise preserves the original I/O or interruption exception.
        record['evidence_status'] = 'EVIDENCE_UNAVAILABLE'
        record['result'] = 'FAILED'
        raise
    record['operations'].append({
        'stage': 'b5-4-1', 'exit_code': result.returncode,
        'tests': list(TESTS),
    })
    print(redact_test_secrets(result.stdout), flush=True)
    print(redact_test_secrets(result.stderr), flush=True)
    if result.returncode:
        raise RuntimeError('isolated B5-4-1 PostgreSQL tests failed')


