"""Genuine PostgreSQL16.15 A1 handoff; fixture writes precede read tracking."""
from pathlib import Path
import subprocess,sys
import pytest
import runpy
MODES=runpy.run_path(str(Path(__file__).parents[1]/'runtime/test_a1_handoff.py'))['MODES']

@pytest.mark.parametrize('mode',MODES)
def test_genuine_original_owned_A1_order(mode):
    child=Path(__file__).parents[1]/'runtime/test_a1_handoff.py'
    p=subprocess.run([sys.executable,'-B',str(child),mode,'postgres'],capture_output=True,text=True,timeout=25,start_new_session=True)
    assert p.returncode==0,(p.stdout,p.stderr)
    assert '"postgres": true' in p.stdout
    print(p.stdout)
