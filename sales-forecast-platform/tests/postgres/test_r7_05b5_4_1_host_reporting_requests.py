"""B5-4-1 original host request adapter against disposable PostgreSQL 16.15.

This module MUST run only after the approved bootstrap.sql + migration.
The existing PG fixture alone writes synthetic initial rows. Reporting access
remains SELECT-only using the real original B5-1/B5-2 source chain.
"""
from pathlib import Path
import runpy
import pytest

from src.services.host_reporting_request_service import build_host_reporting_requests

PG = runpy.run_path(str(Path(__file__).with_name('test_stored_forecast_accuracy_postgres.py')))
tracked = PG['tracked']


@pytest.fixture(scope='module', autouse=True)
def original_schema_precondition():
    from conftest import admin
    with admin() as connection, connection.cursor() as cursor:
        cursor.execute('SELECT version FROM canonical_forecast.schema_migrations')
        assert cursor.fetchall() == [(1,)]


@pytest.mark.parametrize('action', ['FORECAST', 'SYNTHETIC_ACCURACY'])
def test_original_one_time_host_read_is_select_only(tracked, action):
    s = PG['setup']()
    issuer, dispatcher = build_host_reporting_requests(
        s.reader, s.fa, operator=s.op, evaluator=s.service, label_authority=s.la)
    options = dict(actor='synthetic:forecast-only', action=action,
                   visibility=('full-evidence' if action == 'FORECAST'
                               else 'synthetic-full-evidence'),
                   forecast_grant=s.fg, forecast=s.fs)
    if action == 'SYNTHETIC_ACCURACY':
        options.update(label_actor='synthetic:labels-only', label_grant=s.lg, labels=s.ls)
    permit = issuer.issue(s.op, **options)
    out = dispatcher.execute(permit)
    assert out.status == 'AVAILABLE'
    if action == 'FORECAST':
        assert [row.float32_le for row in out.view.source.stored.batch.rows] == [
            '2d56f943', 'e9542244'] * 2
    else:
        assert out.view.source.population_size == 4
    assert dispatcher.execute(permit).status == 'DENIED'
    assert len(tracked.opened) == 1 and tracked.opened[0].closed
    assert tracked.readonly == [dict(readonly=True, isolation_level='REPEATABLE READ',
                                      autocommit=False)]
    assert all(query.split()[0] in ('SELECT', 'SHOW') for query in tracked.statements)


@pytest.mark.parametrize('action', ['FORECAST', 'SYNTHETIC_ACCURACY'])
def test_original_denial_never_opens_connection(tracked, action):
    s = PG['setup']()
    issuer, dispatcher = build_host_reporting_requests(
        s.reader, s.fa, operator=s.op, evaluator=s.service, label_authority=s.la)
    options = dict(actor='synthetic:forecast-only', action=action,
                   visibility=('full-evidence' if action == 'FORECAST'
                               else 'synthetic-full-evidence'),
                   forecast_grant=s.fg, forecast=s.fs)
    if action == 'SYNTHETIC_ACCURACY':
        options.update(label_actor='synthetic:labels-only', label_grant=s.lg, labels=s.ls)
    permit = issuer.issue(s.op, **options)
    if action == 'FORECAST':
        s.fa.revoke(s.op, s.fg)
    else:
        s.la.revoke(s.op, s.lg)
    assert dispatcher.execute(permit).status == 'DENIED'
    assert tracked.opened == tracked.statements == []
