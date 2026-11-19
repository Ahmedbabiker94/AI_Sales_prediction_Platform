"""B5-4-1 provenance tests only; these are not request authorization tests.

Original B5-1/B5-2 factories are the fixture's host bootstrap. No database
connection or model execution is required for constructor-denial cases.
"""
import runpy
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.providers.forecast_read_provider import ForecastReadProvider
from src.services.canonical_forecast_read_service import CanonicalForecastReadService
from src.services.stored_forecast_accuracy_service import StoredForecastAccuracyService
from src.services.canonical_reporting_read_service import (
    CanonicalReportingReadService, build_canonical_reporting_reader,
)

F = runpy.run_path(str(Path(__file__).with_name('test_stored_forecast_accuracy.py')))


@pytest.fixture
def host(monkeypatch):
    batch = F['make_batch']()
    s = F['setup_context'](batch, (F['snapshot'](batch),))
    lookup = Mock(return_value=F['stored'](batch))
    monkeypatch.setattr(ForecastReadProvider, 'lookup', lookup)
    s.lookup = lookup
    return s


def test_original_host_forecast_and_synthetic_evaluation(host):
    s = host
    forecast_only = build_canonical_reporting_reader(s.reader)
    assert forecast_only.read_forecast(s.fg, s.fs).status == 'AVAILABLE'
    assert s.lookup.call_count == 1
    report = build_canonical_reporting_reader(s.reader, s.service)
    assert report.read_synthetic_accuracy(s.fg, s.fs, s.lg, s.ls).status == 'AVAILABLE'
    assert s.lookup.call_count == 2


def test_public_reader_reconstruction_denied_before_source_access(host):
    s = host
    impostor = CanonicalForecastReadService(s.reader._state)
    with pytest.raises(ValueError, match='original host sources'):
        build_canonical_reporting_reader(impostor)
    with pytest.raises(ValueError, match='original host sources'):
        build_canonical_reporting_reader(impostor, s.service)
    s.lookup.assert_not_called()


def test_public_evaluator_reconstruction_denied_before_source_access(host):
    s = host
    impostor = StoredForecastAccuracyService(s.reader, s.service._labels)
    with pytest.raises(ValueError, match='original host sources'):
        build_canonical_reporting_reader(s.reader, impostor)
    s.lookup.assert_not_called()


def test_cross_host_evaluator_denied_before_source_access(monkeypatch):
    first = F['make_batch']()
    second = F['make_batch']()
    a = F['setup_context'](first, (F['snapshot'](first),))
    b = F['setup_context'](second, (F['snapshot'](second),))
    lookup = Mock()
    monkeypatch.setattr(ForecastReadProvider, 'lookup', lookup)
    with pytest.raises(ValueError, match='original host sources'):
        build_canonical_reporting_reader(a.reader, b.service)
    lookup.assert_not_called()


def test_public_dataclass_constructor_cannot_bypass_provenance(host):
    s = host
    with pytest.raises(ValueError, match='original host sources'):
        CanonicalReportingReadService(CanonicalForecastReadService(s.reader._state), None)
    s.lookup.assert_not_called()
