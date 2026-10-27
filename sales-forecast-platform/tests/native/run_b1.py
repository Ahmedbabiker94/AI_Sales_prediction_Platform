"""Run explicitly selected tests with no network, credentials or production mounts.

Build tests/native/Dockerfile first. No fallback to a host Python environment.
"""
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4


def run(arguments, *, timeout=240):
    project = Path(__file__).resolve().parents[2]
    docker = ['docker', '--host=unix:///var/run/docker.sock']
    env = dict(os.environ)
    for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
        env.pop(key, None)
    image = subprocess.check_output(docker+['image', 'inspect', '--format={{.Id}}', 'r7-04b1-test:local'], env=env, text=True).strip()
    name = 'r7-04b1-'+uuid4().hex
    command = docker+['create', '--name', name, '--network', 'none', '--read-only', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges', '--memory', '1g', '--memory-swap', '1g', '--cpus', '1',
        '--pids-limit', '64', '--ulimit', 'cpu=180:180', '--user', '1000:1000',
        '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m']
    # Deliberately exclude models/, artifacts/, data/, .git, host home and Docker socket.
    for directory in ('src', 'tests'):
        command += ['--mount', f'type=bind,src={project/directory},dst=/app/{directory},readonly']
    command += ['--workdir', '/app', image, 'env', '-i', 'PATH=/usr/local/bin:/usr/bin:/bin',
        'PYTHONPATH=/app', 'PYTHONDONTWRITEBYTECODE=1', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1',
        'OPENBLAS_NUM_THREADS=1', 'OMP_NUM_THREADS=1', 'B1_ISOLATED_NATIVE_TEST=1',
        'python', '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider', *arguments]
    try:
        subprocess.run(command, env=env, timeout=30, check=True, stdout=subprocess.DEVNULL)
        return subprocess.run(docker+['start', '--attach', name], env=env, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        print('B1 isolated subprocess timed out', file=sys.stderr)
        return 124
    finally:
        subprocess.run(docker+['rm', '-f', name], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)


if __name__ == '__main__':
    tests = sys.argv[1:] or ['tests/training/test_owned_xgboost_roundtrip.py']
    raise SystemExit(run(tests))
