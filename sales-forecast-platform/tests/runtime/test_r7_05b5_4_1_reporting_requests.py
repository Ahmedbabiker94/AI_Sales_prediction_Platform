"""Controlled-offline B5-4-1 request issuance and replay safety.

No PostgreSQL execution, original static source provider mocked only.
"""
import pickle
import runpy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.providers.forecast_read_provider import ForecastReadProvider
from src.services.host_reporting_request_service import (
    OriginalReportRequest, ReportingRequestDenied, build_host_reporting_requests,
)

F = runpy.run_path(str(Path(__file__).with_name('test_stored_forecast_accuracy.py')))


@pytest.fixture
def context(monkeypatch):
    batch = F['make_batch']()
    s = F['setup_context'](batch, (F['snapshot'](batch),))
    s.lookup = Mock(return_value=F['stored'](batch))
    monkeypatch.setattr(ForecastReadProvider, 'lookup', s.lookup)
    s.issuer, s.dispatcher = build_host_reporting_requests(
        s.reader, s.fa, operator=s.op, evaluator=s.service, label_authority=s.la)
    return s


def forecast(s, **kwargs):
    params = dict(actor='synthetic:forecast-reader', action='FORECAST', visibility='full-evidence',
                  forecast_grant=s.fg, forecast=s.fs)
    params.update(kwargs)
    return s.issuer.issue(s.op, **params)


def accuracy(s, **kwargs):
    params = dict(actor='synthetic:forecast-reader',
                  label_actor='synthetic:independent-label-reader',
                  action='SYNTHETIC_ACCURACY', visibility='synthetic-full-evidence',
                  forecast_grant=s.fg, forecast=s.fs, label_grant=s.lg, labels=s.ls)
    params.update(kwargs)
    return s.issuer.issue(s.op, **params)


def test_forecast_exactly_once(context):
    s = context
    h = forecast(s)
    assert s.dispatcher.execute(h).status == 'AVAILABLE'
    assert s.dispatcher.execute(h).status == 'DENIED'
    assert s.lookup.call_count == 1


def test_synthetic_accuracy_exactly_once(context):
    s = context
    h = accuracy(s)
    assert s.dispatcher.execute(h).status == 'AVAILABLE'
    assert s.dispatcher.execute(h).status == 'DENIED'
    assert s.lookup.call_count == 1


def test_forged_and_serialized_handle_denied(context):
    s = context
    assert s.dispatcher.execute(OriginalReportRequest()).status == 'DENIED'
    assert s.dispatcher.execute(object()).status == 'DENIED'
    h = forecast(s)
    with pytest.raises(TypeError):
        pickle.dumps(h)
    s.lookup.assert_not_called()


def test_foreign_dispatcher_denied(context):
    s = context
    other_issuer, other = build_host_reporting_requests(
        s.reader, s.fa, operator=s.op, evaluator=s.service, label_authority=s.la)
    h = forecast(s)
    assert other.execute(h).status == 'DENIED'
    assert s.dispatcher.execute(h).status == 'AVAILABLE'
    assert s.lookup.call_count == 1


def test_bad_actor_or_foreign_operator_denied_without_lookup(context):
    s = context
    with pytest.raises(ReportingRequestDenied):
        forecast(s, actor='unknown:actor')
    with pytest.raises(ReportingRequestDenied):
        s.issuer.issue(object(), actor='synthetic:forecast-reader', action='FORECAST',
                       visibility='full-evidence', forecast_grant=s.fg, forecast=s.fs)
    with pytest.raises(ReportingRequestDenied):
        accuracy(s, label_actor='synthetic:forecast-reader')
    s.lookup.assert_not_called()


def test_revoke_before_dispatch_denied(context):
    s = context
    h = forecast(s)
    s.fa.revoke(s.op, s.fg)
    assert s.dispatcher.execute(h).status == 'DENIED'
    assert s.lookup.call_count == 0


def test_expiry_before_dispatch_denied(context):
    s = context
    h = forecast(s)
    s.clock['wall'] = 131.
    s.clock['elapsed'] = 31.
    assert s.dispatcher.execute(h).status == 'DENIED'
    s.lookup.assert_not_called()


def test_concurrent_same_handle_at_most_one_dispatch(context):
    s = context
    h = forecast(s)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(s.dispatcher.execute, [h] * 8))
    assert sum(r.status == 'AVAILABLE' for r in results) == 1
    assert sum(r.status == 'DENIED' for r in results) == 7
    assert s.lookup.call_count == 1


def test_second_issue_same_grant_requires_new_host_approval(context):
    s = context
    first = forecast(s)
    assert s.dispatcher.execute(first).status == 'AVAILABLE'
    second = forecast(s)
    assert s.dispatcher.execute(first).status == 'DENIED'
    assert s.dispatcher.execute(second).status == 'AVAILABLE'
    assert s.lookup.call_count == 2


def test_unreviewed_visibility_denied_before_lookup(context):
    s = context
    with pytest.raises(ReportingRequestDenied):
        forecast(s, visibility='public-summary')
    with pytest.raises(ReportingRequestDenied):
        accuracy(s, visibility='full-evidence')
    s.lookup.assert_not_called()


def test_label_revoked_after_approval_denied_before_lookup(context):
    s = context
    h = accuracy(s)
    s.la.revoke(s.op, s.lg)
    assert s.dispatcher.execute(h).status == 'DENIED'
    s.lookup.assert_not_called()


def test_bad_request_action_denied_before_lookup(context):
    s = context
    with pytest.raises(ReportingRequestDenied):
        forecast(s, action='EXPORT')
    s.lookup.assert_not_called()


def test_consumed_approval_removed_from_registry(context):
    s = context
    h = forecast(s)
    assert id(h) in s.dispatcher._records
    assert s.dispatcher.execute(h).status == 'AVAILABLE'
    assert id(h) not in s.dispatcher._records
    assert s.dispatcher.execute(h).status == 'DENIED'
    assert s.lookup.call_count == 1


def test_pending_capacity_denies_without_eviction(context, monkeypatch):
    from src.services import host_reporting_request_service as requests
    s = context
    monkeypatch.setattr(requests, '_MAX_OUTSTANDING_REQUESTS', 1)
    first = forecast(s)
    with pytest.raises(ReportingRequestDenied):
        forecast(s)
    assert id(first) in s.dispatcher._records
    assert s.dispatcher.execute(first).status == 'AVAILABLE'
    second = forecast(s)
    assert s.dispatcher.execute(second).status == 'AVAILABLE'
    assert s.lookup.call_count == 2


def test_concurrent_capacity_never_exceeds_limit(context, monkeypatch):
    from src.services import host_reporting_request_service as requests
    s = context
    monkeypatch.setattr(requests, '_MAX_OUTSTANDING_REQUESTS', 4)

    def attempt(_):
        try:
            return forecast(s)
        except ReportingRequestDenied:
            return None

    with ThreadPoolExecutor(max_workers=16) as pool:
        handles = list(pool.map(attempt, range(32)))
    issued = [h for h in handles if h is not None]
    assert len(issued) <= 4
    assert len(s.dispatcher._records) == len(issued)
    s.lookup.assert_not_called()


def test_capacity_recovers_after_approved_request_consumption(context, monkeypatch):
    from src.services import host_reporting_request_service as requests
    s = context
    monkeypatch.setattr(requests, '_MAX_OUTSTANDING_REQUESTS', 1)
    first = forecast(s)
    with pytest.raises(ReportingRequestDenied):
        forecast(s)
    assert s.dispatcher.execute(first).status == 'AVAILABLE'
    assert len(s.dispatcher._records) == 0
    second = forecast(s)
    assert s.dispatcher.execute(second).status == 'AVAILABLE'
    assert s.lookup.call_count == 2


@pytest.mark.parametrize('interruption', [KeyboardInterrupt, SystemExit])
def test_interrupted_original_lookup_never_reuses_approval(context, interruption):
    s = context
    approved = forecast(s)
    def interrupted_lookup(*args, **kwargs):
        raise interruption('injected after one-time request consumption')
    s.lookup.side_effect = interrupted_lookup
    with pytest.raises(interruption):
        s.dispatcher.execute(approved)
    assert s.lookup.call_count == 1
    assert s.dispatcher.execute(approved).status == 'DENIED'
    assert s.lookup.call_count == 1
    assert id(approved) not in s.dispatcher._records


@pytest.mark.parametrize('interruption', [KeyboardInterrupt, SystemExit])
def test_interruption_at_post_consume_handoff_denies_replay(context, monkeypatch, interruption):
    from src.services import host_reporting_request_service as requests
    s = context
    approved = forecast(s)
    original = requests._locked_call

    def interrupt_after_consumption(locks, body):
        outcome = original(locks, body)
        raise interruption('injected after registry consumption')

    with monkeypatch.context() as patch:
        patch.setattr(requests, '_locked_call', interrupt_after_consumption)
        with pytest.raises(interruption):
            s.dispatcher.execute(approved)
    assert s.dispatcher.execute(approved).status == 'DENIED'
    assert s.lookup.call_count == 0
    assert id(approved) not in s.dispatcher._records


def test_principal_revoked_after_issue_denies_before_lookup(context):
    s = context
    approved = forecast(s)
    s.fa.revoke(s.op, s.fp)
    assert s.dispatcher.execute(approved).status == 'DENIED'
    s.lookup.assert_not_called()


def test_label_principal_revoked_after_issue_denies_before_lookup(context):
    s = context
    approved = accuracy(s)
    s.la.revoke(s.op, s.lp)
    assert s.dispatcher.execute(approved).status == 'DENIED'
    s.lookup.assert_not_called()
