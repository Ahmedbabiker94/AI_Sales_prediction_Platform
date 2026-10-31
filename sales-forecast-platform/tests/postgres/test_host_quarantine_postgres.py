"""Genuine SELECT-only PG, original grants; host health faults in owned children."""
from pathlib import Path
import subprocess
import sys
import pytest

@pytest.mark.parametrize('order',['sigint_first','systemexit_first'])
@pytest.mark.parametrize('publication',['none','before_SIGINT','before_SystemExit','after_SIGINT','after_SystemExit'])
@pytest.mark.parametrize('inflight',[False,True])
def test_real_pg_double_cleanup_terminal_quarantine(order,publication,inflight):
    child=Path(__file__).parents[1]/'runtime/test_host_quarantine.py'
    p=subprocess.run([sys.executable,'-B',str(child),order,publication,str(int(inflight)),'postgres'],
        capture_output=True,text=True,timeout=20,start_new_session=True)
    assert p.returncode==0,(p.stdout,p.stderr)
    assert '"postgres": true' in p.stdout and '"within_process_denials": true' in p.stdout
    print(p.stdout)


@pytest.mark.parametrize('order',['sigint_first','systemexit_first'])
@pytest.mark.parametrize('at',[1,2,3,4,5,6])
def test_real_pg_quarantine_inside_original_authorization_callback(order,at):
    code=('import sys,runpy;sys.path.insert(0,"tests/postgres");'
          'f=runpy.run_path("tests/runtime/test_host_quarantine.py"); '
          'f["callback_child"]('+repr(order)+','+str(at)+',True)')
    p=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True,timeout=20,start_new_session=True)
    assert p.returncode==0,(p.stdout,p.stderr)
    print(p.stdout)
