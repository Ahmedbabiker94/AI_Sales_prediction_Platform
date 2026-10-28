"""Explicit recovered-image TEST launcher; not model-use or production authority.

Expected identities below come from the operator-reviewed recovery report. They
are not configurable through requests, tags or environment variables. Evidence
under /workspace is not durable or an authentication sandbox. Native tests still
need separate authorization; P0 verification runs non-model tests only.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
from uuid import uuid4

_IMAGE = 'sha256:417063b71e35c7a9889c10ac2516c328f16a483e065ec858df101eb9728c2df2'
_MANIFEST_SHA = 'd199a85c61ed01e3ac8da447ae32a16cd606d2e029b5c42d76aa0d8d0cb0b3f5'
_MANIFEST = Path('/workspace/r7-recovery-evidence/fresh-20261028T045146Z-uc5_ii2b/fresh-manifest.json')
_BASELINE = '098d0c0c47a8e35d41c9f5dff57428ac162690f1'
_TREE = 'cb68e22fa9b06f6f866520160737bcbad2c67347'
_PROJECT = Path(__file__).resolve().parents[2]
_PROOFS = ('base-provenance.json', 'distribution-provenance.json', 'embedded-requirements-b1.lock',
           'installed-dependencies-and-isolation-v3.json', 'worker-configurations-v3.json',
           'non-native-runs-v3.json', 'docker-discovery.json', 'container-preservation-v3.json')
_PROXY_KEYS = ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','FTP_PROXY',
               'http_proxy','https_proxy','all_proxy','no_proxy','ftp_proxy')


class CompatibilityError(ValueError):
    pass


def _read(path, limit):
    if path.resolve(strict=True) != path.absolute(): raise ValueError()
    # Nonblocking open is required before fstat: a substituted FIFO must not
    # wait for a writer. Missing required Unix flags fail closed; no fallback.
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit: raise ValueError()
        with os.fdopen(fd, 'rb', closefd=False) as stream: raw = stream.read(limit+1)
        if len(raw) > limit: raise ValueError()
        return raw
    finally: os.close(fd)


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError()
            result[key] = value
        return result
    def invalid(_): raise ValueError()
    return json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=pairs, parse_constant=invalid)


def _evidence():
    raw = _read(_MANIFEST, 128*1024)
    if hashlib.sha256(raw).hexdigest() != _MANIFEST_SHA: raise ValueError()
    m = _json(raw)
    if (m['schema'] != 'r7_fresh_test_environment_validation' or m['version'] != '1.0'
            or m['classification'] != 'FRESH_NON_NATIVE_VALIDATION_NOT_EXECUTION_AUTHORITY'
            or m['image']['id'] != _IMAGE or m['image']['platform'] != 'linux/amd64'
            or m['git']['live_head'] != _BASELINE or m['git']['tree'] != _TREE
            or m['non_native_validation']['python'] != '3.11.18'
            or m['non_native_validation']['imports']['xgboost'] != '3.2.0'
            or m['fresh_provenance']['verified_wheels'] != 37
            or m['fresh_provenance']['base_layer_prefix_verified'] is not True
            or m['native_model_workloads'] != 0 or m['producer_implementation'] is not False): raise ValueError()
    proofs = {}
    for name in _PROOFS:
        data = _read(_MANIFEST.parent/name, 2*1024*1024)
        if hashlib.sha256(data).hexdigest() != m['evidence_sha256'][name]: raise ValueError()
        proofs[name] = data
    base = _json(proofs['base-provenance.json'])
    if base != m['fresh_provenance']['base']: raise ValueError()
    for name, field in (('Dockerfile','dockerfile_sha256'),('requirements-b1.lock','accepted_lock_sha256')):
        if hashlib.sha256(_read(_PROJECT/'tests/native'/name, 64*1024)).hexdigest() != base[field]: raise ValueError()
    distributions = _json(proofs['distribution-provenance.json'])['distributions']
    if len(distributions) != 37 or len({d['name'].lower().replace('_','-') for d in distributions}) != 37: raise ValueError()
    installed = _json(proofs['installed-dependencies-and-isolation-v3.json'])
    if (installed['status'] != 'PASS' or installed['wheel_files_byte_identical'] != 8602
            or installed['native_model_operations'] != 0 or installed['database_operations'] != 0): raise ValueError()
    if not _json(proofs['container-preservation-v3.json'])['unchanged']: raise ValueError()
    return base


def _docker(args, *, timeout=15):
    env = dict(os.environ)
    for key in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'): env.pop(key, None)
    return subprocess.run(['docker','--host=unix:///var/run/docker.sock',*args], env=env,
                          capture_output=True, text=True, timeout=timeout)


def _inspect(args):
    result = _docker(args)
    if result.returncode or len(result.stdout.encode()) > 256*1024: raise ValueError()
    data = _json(result.stdout.encode())
    if type(data) is not list or len(data) != 1: raise ValueError()
    return data[0]


def validate_recovery(*, use_recovered_image=False):
    """Integrity/readiness only; result is not an executable capability."""
    try:
        if use_recovered_image is not True: raise ValueError()
        base = _evidence()
        image = _inspect(['image','inspect',_IMAGE])
        if (image['Id'] != _IMAGE or image['Os'] != 'linux' or image['Architecture'] != 'amd64'
                or image['Config']['Entrypoint'] is not None
                or image['RootFS']['Layers'][:len(base['base_diff_ids'])] != base['base_diff_ids']): raise ValueError()
        return {'image_id':_IMAGE, 'manifest_sha256':_MANIFEST_SHA, 'classification':'TEST_ENVIRONMENT_ONLY'}
    except Exception: pass
    raise CompatibilityError('recovered test environment unavailable or not explicitly selected') from None


def _container(data, project):
    h = data['HostConfig']; c = data['Config']
    if (data['Image'] != _IMAGE or c['Image'] != _IMAGE or c['User'] != '1000:1000'
            or h['NetworkMode'] != 'none' or not h['ReadonlyRootfs'] or h['Privileged']
            or h.get('PidMode','') not in ('',None) or h.get('IpcMode','private') not in ('private','')
            or h['CapDrop'] != ['ALL'] or h['SecurityOpt'] != ['no-new-privileges']
            or h['Memory'] != 1073741824 or h['MemorySwap'] != 1073741824
            or h['NanoCpus'] != 1000000000 or h['PidsLimit'] != 64
            or h['Ulimits'] != [{'Name':'cpu','Hard':180,'Soft':180}]
            or h['Tmpfs'] != {'/tmp':'rw,nosuid,nodev,size=256m'}): raise ValueError()
    binds = [m for m in data['Mounts'] if m['Type'] == 'bind']
    if (len(binds) != 2 or {(m['Source'],m['Destination']) for m in binds} != {(str(project/d),'/app/'+d) for d in ('src','tests')}
            or any(m['RW'] for m in binds)
            or any(m['Type'] != 'bind' and not (m['Type'] == 'tmpfs' and m['Destination'] == '/tmp') for m in data['Mounts'])): raise ValueError()
    values = dict(item.split('=',1) for item in c['Env'])
    if any(values.get(key) != '' for key in _PROXY_KEYS): raise ValueError()
    if set(values)-set(_PROXY_KEYS)-{'PATH','LANG','GPG_KEY','PYTHON_VERSION','PYTHON_SHA256'}: raise ValueError()


def run(arguments, *, use_recovered_image=False, timeout=240):
    """Explicit pytest selections, no default tests and no image/path overrides."""
    name = None; created = False
    try:
        if (type(arguments) not in (list,tuple) or not 1 <= len(arguments) <= 64
                or type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0 < timeout <= 240): raise ValueError()
        selected = []
        for value in arguments:
            if type(value) is not str or len(value) > 512: raise ValueError()
            if value in ('-s','-q'): continue
            if (not value.startswith('tests/') or not value.split('::')[0].endswith('.py')
                    or '..' in value.split('/') or any(ch in value for ch in '\x00\n\r;$`')): raise ValueError()
            selected.append(value)
        if not selected: raise ValueError()
        validate_recovery(use_recovered_image=use_recovered_image)
        project = _PROJECT.resolve(strict=True)
        for directory in ('src','tests'):
            path = project/directory
            if path.is_symlink() or not path.is_dir(): raise ValueError()
        name = 'r7-recovered-test-'+uuid4().hex
        cmd = ['create','--name',name,'--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
               '--memory','1g','--memory-swap','1g','--cpus','1','--pids-limit','64','--ulimit','cpu=180:180',
               '--user','1000:1000','--tmpfs','/tmp:rw,nosuid,nodev,size=256m']
        for key in _PROXY_KEYS: cmd += ['--env',key+'=']
        for directory in ('src','tests'): cmd += ['--mount',f'type=bind,src={project/directory},dst=/app/{directory},readonly']
        cmd += ['--workdir','/app',_IMAGE,'env','-i','PATH=/usr/local/bin:/usr/bin:/bin','PYTHONPATH=/app',
                'PYTHONDONTWRITEBYTECODE=1','PYTEST_DISABLE_PLUGIN_AUTOLOAD=1','OPENBLAS_NUM_THREADS=1','OMP_NUM_THREADS=1',
                'B1_ISOLATED_NATIVE_TEST=1','python','-m','pytest','-q','--tb=short','-p','no:cacheprovider',*arguments]
        result = _docker(cmd, timeout=30)
        if result.returncode: raise ValueError()
        created = True
        _container(_inspect(['inspect',name]), project)
        # Evidence/identity is rechecked immediately before starting by immutable ID.
        validate_recovery(use_recovered_image=use_recovered_image)
        worker = _docker(['start','--attach',name], timeout=timeout)
        print(worker.stdout, end='')
        print(worker.stderr, end='', file=sys.stderr)
        return worker.returncode
    except subprocess.TimeoutExpired:
        if not created: raise CompatibilityError('recovered worker setup timed out') from None
        return 124
    except Exception: pass
    finally:
        if name is not None:
            try:
                cleanup = _docker(['rm','-f',name])
                remaining = _docker(['ps','-a','--filter','name=^/'+name+'$','--format={{.ID}}'])
                if remaining.returncode or remaining.stdout.strip() or created and cleanup.returncode: raise ValueError()
            except Exception:
                raise CompatibilityError('recovered worker cleanup could not be verified') from None
    raise CompatibilityError('recovered test invocation rejected') from None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--use-validated-recovered-image', action='store_true')
    parser.add_argument('tests', nargs='+')
    cli = parser.parse_args()
    try: code = run(cli.tests, use_recovered_image=cli.use_validated_recovered_image)
    except CompatibilityError as error: parser.exit(2, str(error)+'\n')
    raise SystemExit(code)
