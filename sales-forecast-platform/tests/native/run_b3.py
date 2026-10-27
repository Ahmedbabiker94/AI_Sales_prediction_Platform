"""B3 explicitly selected tests only, reusing the accepted disposable B1 worker."""
import os
from pathlib import Path
import runpy
import subprocess
import sys

IMAGE='sha256:8adc380a27f6dfde49066ac0ed323678783ca58e553272d3409e0fe9d04cf7c8'

def run(arguments, *, timeout=240):
    if not arguments: raise ValueError('explicit test selection required')
    env=dict(os.environ)
    for key in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):env.pop(key,None)
    actual=subprocess.check_output(['docker','--host=unix:///var/run/docker.sock','image','inspect','--format={{.Id}}','r7-04b1-test:local'],env=env,text=True).strip()
    if actual!=IMAGE: raise ValueError('pinned native environment unavailable')
    return runpy.run_path(str(Path(__file__).with_name('run_b1.py')))['run'](arguments,timeout=timeout)

if __name__=='__main__':raise SystemExit(run(sys.argv[1:]))
