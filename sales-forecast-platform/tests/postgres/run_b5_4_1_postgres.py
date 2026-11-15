"""Opt-in B5-4-1 PostgreSQL integration using the unchanged R7-05B3 harness.

This is an EXTRA host stage after the original foundation + restart checks.
It cannot select arbitrary tests, change the database image, use TCP, or target
an existing database. Use only on a trusted Docker host with the exact pinned
images and verified local wheelhouse required by run_postgres.py.
"""
from pathlib import Path
import argparse
import json

from run_postgres import (
    ROOT, PROXIES, POSTGRES, BASE, command, run, write_evidence,
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
    from run_postgres import _LIFECYCLE
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
    write_evidence(evidence_dir/'b5-4-1.stdout', result.stdout)
    write_evidence(evidence_dir/'b5-4-1.stderr', result.stderr)
    record['operations'].append({
        'stage': 'b5-4-1', 'exit_code': result.returncode,
        'tests': list(TESTS),
    })
    print(redact_test_secrets(result.stdout), flush=True)
    print(redact_test_secrets(result.stderr), flush=True)
    if result.returncode:
        raise RuntimeError('isolated B5-4-1 PostgreSQL tests failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-disposable-postgres', action='store_true')
    parser.add_argument('--wheelhouse', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_disposable_postgres:
        parser.error('explicit disposable PostgreSQL permission required')
    def host_stage(**kwargs):
        kwargs['record']['b5_4_1_evidence_dir'] = str(args.evidence)
        reporting_stage(**kwargs)
    run(allow=True, wheelhouse=args.wheelhouse, evidence=args.evidence,
        _host_stage=host_stage)


if __name__ == '__main__':
    main()
