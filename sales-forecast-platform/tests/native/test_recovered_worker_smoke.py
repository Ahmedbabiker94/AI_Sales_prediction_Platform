"""Non-model worker checks only; no preparation, fitting, loading or prediction."""
import hashlib
import importlib.metadata
import os
from pathlib import Path
import resource
import socket
import sys


def test_non_root_and_capability_restrictions():
    assert os.getuid()==os.getgid()==1000
    status=Path('/proc/self/status').read_text()
    assert 'CapEff:\t0000000000000000' in status and 'NoNewPrivs:\t1' in status


def test_read_only_root_and_mounts():
    mounts=[line.split() for line in Path('/proc/mounts').read_text().splitlines()]
    for target in ('/','/app/src','/app/tests'):
        rows=[m for m in mounts if m[1]==target]
        assert len(rows)==1 and 'ro' in rows[0][3].split(',')
    assert set(os.listdir('/app'))=={'src','tests'}
    assert not Path('/var/run/docker.sock').exists()


def test_resource_configuration():
    assert Path('/sys/fs/cgroup/memory.max').read_text().strip()=='1073741824'
    assert Path('/sys/fs/cgroup/memory.swap.max').read_text().strip()=='0'
    assert Path('/sys/fs/cgroup/pids.max').read_text().strip()=='64'
    quota,period=map(int,Path('/sys/fs/cgroup/cpu.max').read_text().split());assert quota==period
    assert resource.getrlimit(resource.RLIMIT_CPU)==(180,180)


def test_network_none():
    assert {name for _,name in socket.if_nameindex()}=={'lo'}
    assert not any(line.split()[1]=='00000000' for line in Path('/proc/net/route').read_text().splitlines()[1:])


def test_no_host_environment_or_operational_modules():
    assert set(os.environ)<={'PATH','PYTHONPATH','PYTHONDONTWRITEBYTECODE','PYTEST_DISABLE_PLUGIN_AUTOLOAD','OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','B1_ISOLATED_NATIVE_TEST','LC_CTYPE','PYTEST_VERSION','PYTEST_CURRENT_TEST'}
    forbidden=('xgboost','lightgbm','torch','tensorflow','mlflow','src.database.db','src.inference.predictor',
               'src.deployment.production_artifact_sync','src.ml.production_resolver','src.repositories.forecast_repository')
    assert not any(any(name==p or name.startswith(p+'.') for p in forbidden) for name in sys.modules)


def test_locked_dependency_metadata_only():
    assert sys.version.split()[0]=='3.11.18'
    requirements=Path('tests/native/requirements-b1.lock').read_text().splitlines()
    assert len(requirements)==37
    for item in requirements:
        name,version=item.split('==')
        assert importlib.metadata.version(name)==version


def test_accepted_b3_guard_file_is_original():
    path=Path('tests/native/run_b3.py')
    assert hashlib.sha256(path.read_bytes()).hexdigest()=='27c965cb607f614976515777b6dc1cd3c617b763ebc760e35a00bc1d3b880012'
