"""Real SELECT-only fixture with original grants; failed issuance does no SQL."""
from pathlib import Path
from types import SimpleNamespace
import runpy
import pytest
from conftest import driver
from src.database import forecast_read_connection as connections
from src.providers.forecast_read_provider import ForecastReadProvider
R=runpy.run_path(str(Path(__file__).parents[1]/'runtime/test_joint_checkpoint_lifetime.py'))
PG=runpy.run_path(str(Path(__file__).with_name('test_stored_forecast_accuracy_postgres.py')))

@pytest.mark.parametrize('edge',R['EDGES'])
@pytest.mark.parametrize('kind',['SIGINT','SystemExit'])
def test_original_grants_interrupted_lifecycle_real_pg(monkeypatch,edge,kind):
    s=PG['setup']();opened=[];sql=[];dispatch=[];real=driver()
    class Cursor(real.extensions.cursor):
        def execute(self,q,*a,**kw):sql.append(q);return super().execute(q,*a,**kw)
    class Connection(real.extensions.connection):
        def cursor(self,*a,**kw):kw['cursor_factory']=Cursor;return super().cursor(*a,**kw)
    def connect(**kw):
        assert kw['user']=='r7_forecast_reader'
        c=real.connect(**kw,connection_factory=Connection);opened.append(c);return c
    original=ForecastReadProvider.lookup
    def lookup(self,*a,**kw):dispatch.append(True);return original(self,*a,**kw)
    monkeypatch.setattr(connections,'driver',lambda:SimpleNamespace(connect=connect))
    monkeypatch.setattr(ForecastReadProvider,'lookup',lookup)
    class Count:
        @property
        def call_count(self):return len(dispatch)
    s.lookup=Count()
    result=R['interrupt_at'](s,edge,kind,lambda:PG['evaluate'](s))
    assert len(opened)==len(dispatch) and all(c.closed for c in opened)
    assert all(q.split()[0] in ('SELECT','SHOW') for q in sql)
    print('LIFECYCLE_PG',dict(result,connections=len(opened),all_closed=True))
