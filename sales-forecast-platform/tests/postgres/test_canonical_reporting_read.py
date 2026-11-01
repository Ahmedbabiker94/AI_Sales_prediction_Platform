"""Original reader/evaluator through reporting; disposable PG fixture writes only."""
from pathlib import Path
import runpy
import pytest
from src.services.canonical_reporting_read_service import build_canonical_reporting_reader

PG = runpy.run_path(str(Path(__file__).with_name('test_stored_forecast_accuracy_postgres.py')))
tracked = PG['tracked']
@pytest.fixture(scope='module', autouse=True)
def schema_precondition():
    # The original approved bootstrap must precede this reader-only test suite.
    from conftest import admin
    with admin() as connection, connection.cursor() as cursor:
        cursor.execute('SELECT version FROM canonical_forecast.schema_migrations')
        assert cursor.fetchall() == [(1,)]


@pytest.mark.parametrize('kind',['forecast','accuracy'])
def test_original_readonly_source(tracked,kind):
    s=PG['setup']()
    report=build_canonical_reporting_reader(s.reader,s.service)
    out=(report.read_forecast(s.fg,s.fs) if kind=='forecast' else
         report.read_synthetic_accuracy(s.fg,s.fs,s.lg,s.ls))
    assert out.status=='AVAILABLE'
    if kind=='forecast':
        assert [r.float32_le for r in out.view.source.stored.batch.rows]==['2d56f943','e9542244']*2
    else:
        e=out.view.source
        assert (e.mae,e.rmse,e.bias)==(1.0000152587890625,1.0520395551960913,-1.0000152587890625)
    assert len(tracked.opened)==1 and tracked.opened[0].closed
    assert tracked.readonly==[dict(readonly=True,isolation_level='REPEATABLE READ',autocommit=False)]
    assert all(q.split()[0] in ('SELECT','SHOW') for q in tracked.statements)


@pytest.mark.parametrize('kind',['forecast','accuracy'])
def test_original_denial_zero_connection(tracked,kind):
    s=PG['setup']()
    report=build_canonical_reporting_reader(s.reader,s.service)
    out=(report.read_forecast(object(),s.fs) if kind=='forecast' else
         report.read_synthetic_accuracy(s.fg,s.fs,object(),s.ls))
    assert out.status=='DENIED' and out.view is None
    assert tracked.opened==tracked.statements==[]
