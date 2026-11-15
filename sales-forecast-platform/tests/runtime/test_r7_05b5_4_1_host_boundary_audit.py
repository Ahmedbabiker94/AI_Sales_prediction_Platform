"""B5-4-1 threat-boundary audit: positive evidence of in-process exposure.

These tests DO NOT certify hostile Python isolation. They show why the request
dispatcher MUST remain in the trusted host. Fail if an operational API starts
importing the sensitive B5-4-1 services without a separate boundary review.
"""
import ast
from pathlib import Path
import runpy
from unittest.mock import Mock

import pytest

from src.providers.forecast_read_provider import ForecastReadProvider
from src.services.host_reporting_request_service import build_host_reporting_requests

ROOT = Path(__file__).resolve().parents[2]
F = runpy.run_path(str(Path(__file__).with_name('test_stored_forecast_accuracy.py')))


def test_dispatcher_is_not_safe_to_transfer_to_untrusted_python(monkeypatch):
    batch = F['make_batch']()
    s = F['setup_context'](batch, (F['snapshot'](batch),))
    lookup = Mock(return_value=F['stored'](batch))
    monkeypatch.setattr(ForecastReadProvider, 'lookup', lookup)
    issuer, dispatcher = build_host_reporting_requests(
        s.reader, s.fa, operator=s.op, evaluator=s.service, label_authority=s.la)
    selection = s.fs
    handle = issuer.issue(
        s.op, actor='synthetic:forecast-reader', action='FORECAST',
        visibility='full-evidence', forecast_grant=s.fg, forecast=selection)

    # This is an intentional counterexample, not a recommended caller workflow:
    # shared-process Python has access to privileged internal references.
    assert dispatcher._reader is s.reader
    record = dispatcher._records[id(handle)]
    assert record.forecast_grant is s.fg
    assert record.forecast_json == selection.canonical_json
    assert record.issuer is dispatcher._issuer_identity
    # Prove one-use dispatch still works in its documented trusted-host scope.
    assert dispatcher.execute(handle).status == 'AVAILABLE'
    assert dispatcher.execute(handle).status == 'DENIED'
    assert lookup.call_count == 1


@pytest.mark.parametrize('path', [
    'api/main.py',
    'src/runtime/bootstrap.py',
])
def test_current_operational_entrypoints_do_not_import_b5_4_1(path):
    source = (ROOT / path).read_text(encoding='utf-8')
    tree = ast.parse(source)
    sensitive = {'host_reporting_request_service', 'canonical_reporting_read_service'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(
                item.name.rsplit('.', 1)[-1] in sensitive for item in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.rsplit('.', 1)[-1] not in sensitive
    # String-level guard on dynamic imports; this only covers these files and
    # cannot establish a full deployment/import graph.
    assert not any(name in source for name in sensitive)


def test_exposed_dispatcher_reference_allows_bypass_of_request_one_use(monkeypatch):
    """Deliberate adversarial counterexample in the SAME trusted Python process.

    This test passing demonstrates the untrusted-in-process threat is NOT solved.
    The actual authorized host-only request path remains one-use.
    """
    batch = F['make_batch']()
    s = F['setup_context'](batch, (F['snapshot'](batch),))
    lookup = Mock(return_value=F['stored'](batch))
    monkeypatch.setattr(ForecastReadProvider, 'lookup', lookup)
    issuer, dispatcher = build_host_reporting_requests(
        s.reader, s.fa, operator=s.op, evaluator=s.service, label_authority=s.la)
    handle = issuer.issue(
        s.op, actor='synthetic:forecast-reader', action='FORECAST',
        visibility='full-evidence', forecast_grant=s.fg, forecast=s.fs)
    original = dispatcher._records[id(handle)]
    assert dispatcher.execute(handle).status == 'AVAILABLE'
    assert dispatcher.execute(handle).status == 'DENIED'
    assert lookup.call_count == 1

    # Forbidden caller behavior, possible if the dispatcher object escapes host:
    from src.data.contracts.canonical_forecast_read import ExactForecastRead
    direct = dispatcher._reader.read(
        original.forecast_grant,
        ExactForecastRead.model_validate_json(original.forecast_json))
    assert direct.status == 'VERIFIED_RECORD_SET'
    assert lookup.call_count == 2
