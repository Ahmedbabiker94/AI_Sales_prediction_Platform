"""Static canonical fixtures and original grants. No models or real DB proof."""
import runpy
from unittest.mock import Mock
import pytest
from src.providers.forecast_read_provider import ForecastReadProvider
from src.services.canonical_reporting_read_service import build_canonical_reporting_reader

F = runpy.run_path('tests/runtime/test_stored_forecast_accuracy.py')


@pytest.fixture
def context(monkeypatch):
    batch = F['make_batch']()
    s = F['setup_context'](batch, (F['snapshot'](batch),))
    lookup = Mock(return_value=F['stored'](batch))
    monkeypatch.setattr(ForecastReadProvider, 'lookup', lookup)
    s.lookup = lookup
    s.report = build_canonical_reporting_reader(s.reader)
    return s


def test_forecast_original_source(context):
    s = context
    out = s.report.read_forecast(s.fg, s.fs)
    assert out.status == 'AVAILABLE'
    assert out.view.kind == 'FORECAST_ONLY'
    assert out.view.source.stored.batch == s.batch
    assert out.view.display_values == (498.6732482910156,649.3267211914062)*2
    assert s.lookup.call_count == 1
    with pytest.raises(Exception):
        out.view.source = None


@pytest.mark.parametrize('fault', ['foreign','expired','revoked','detached'])
def test_denied_before_lookup(context, fault):
    s = context
    grant = s.fg
    if fault == 'foreign': grant = object()
    if fault == 'expired': s.clock['wall'] = 130.
    if fault == 'revoked': s.fa.revoke(s.op, grant)
    if fault == 'detached': grant = F['stored'](s.batch)
    out = s.report.read_forecast(grant, s.fs)
    assert out.status == 'DENIED' and out.view is None
    s.lookup.assert_not_called()


@pytest.mark.parametrize('value,status', [(None,'NOT_AVAILABLE'),('corrupt','UNAVAILABLE')])
def test_missing_or_corrupt(context, value, status):
    s = context
    s.lookup.return_value = value
    out = s.report.read_forecast(s.fg,s.fs)
    assert out.status == status and out.view is None
    assert s.lookup.call_count == 1


@pytest.mark.parametrize('revision,mae,rmse,bias', [
    ('A',1.0000152587890625,1.0520395551960913,-1.0000152587890625),
    ('B',2.0000152587890625,2.026528495549795,-2.0000152587890625)])
def test_synthetic_original_metrics(monkeypatch, revision, mae, rmse, bias):
    batch = F['make_batch']()
    snap = F['snapshot'](batch, values=('500','650') if revision=='A' else ('501','651'), revision=revision)
    s = F['setup_context'](batch,(snap,))
    lookup = Mock(return_value=F['stored'](batch))
    monkeypatch.setattr(ForecastReadProvider,'lookup',lookup)
    report = build_canonical_reporting_reader(s.reader,s.service)
    out = report.read_synthetic_accuracy(s.fg,s.fs,s.lg,s.ls)
    assert out.status == 'AVAILABLE'
    assert out.view.kind == 'CONTROLLED_OFFLINE_SYNTHETIC_ACCURACY'
    e = out.view.source
    assert (e.mae,e.rmse,e.bias) == (mae,rmse,bias)
    assert e.label_selection == s.ls
    assert [p.prediction_float32_le for p in e.pairs] == ['2d56f943','e9542244']*2
    assert lookup.call_count == 1
    assert out.view.content_sha256 == out.view.content_sha256


@pytest.mark.parametrize('fault',['forecast','label','expired','revoked'])
def test_independent_accuracy_denial(context,fault):
    s=context
    fg,lg=s.fg,s.lg
    if fault=='forecast': fg=object()
    if fault=='label': lg=object()
    if fault=='expired': s.clock['elapsed']=60.
    if fault=='revoked': s.la.revoke(s.op,lg)
    out=build_canonical_reporting_reader(s.reader,s.service).read_synthetic_accuracy(fg,s.fs,lg,s.ls)
    assert out.status=='DENIED' and out.view is None
    s.lookup.assert_not_called()


def test_no_cache(context):
    s=context
    assert s.report.read_forecast(s.fg,s.fs).status=='AVAILABLE'
    s.fa.revoke(s.op,s.fg)
    assert s.report.read_forecast(s.fg,s.fs).status=='DENIED'
    assert s.lookup.call_count==1
