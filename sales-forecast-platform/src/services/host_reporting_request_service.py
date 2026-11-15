"""B5-4-1 controlled-offline, host-issued, single-use reporting requests.

Composition and issue stay in a trusted host. Only dispatcher + opaque issued
requests reach consumers; never hand the issuer/operator to request handlers.
Not production IAM, a Python sandbox, or an external-delivery fence.
"""
from dataclasses import dataclass, field
from sys import implementation, version_info
from threading import RLock
from src.data.contracts.canonical_forecast_read import ExactForecastRead
from src.data.contracts.stored_forecast_accuracy import ExactLabelRead
from src.data.contracts.canonical_reporting import ReportingOutcome
from src.services.canonical_forecast_read_service import ReadAuthority, _HOST_HEALTH, _locked_call
from src.services.stored_forecast_accuracy_service import ActualLabelAuthority
from src.services.canonical_reporting_read_service import build_canonical_reporting_reader
from src.providers._forecast_read_order import _authentic_reporting_sources

_SUPPORTED = implementation.name == 'cpython' and version_info[:2] in ((3, 11), (3, 12))


_MAX_OUTSTANDING_REQUESTS = 4096


class ReportingRequestDenied(ValueError):
    pass


class OriginalReportRequest:
    __slots__ = ()

    def __reduce__(self):
        raise TypeError('issued report request only')


@dataclass(eq=False)
class _Approved:
    handle: object
    issuer: object
    action: str
    visibility: str
    actor: str
    label_actor: str | None
    forecast_grant: object
    forecast_json: str
    label_grant: object
    label_json: str | None
    forecast_expiry: tuple
    label_expiry: tuple | None
    use: dict = field(default_factory=dict)


class ReportingRequestDispatcher:
    """Consumer-facing capability. No issue, source selection or grant renewal."""

    __slots__ = ('_reader', '_evaluator', '_records', '_issuer_identity', '_registry_lock')

    def __init__(self, reader, evaluator, records, issuer_identity, registry_lock):
        self._reader, self._evaluator = reader, evaluator
        self._records, self._issuer_identity = records, issuer_identity
        self._registry_lock = registry_lock

    def execute(self, request):
        # Exact builtin dict lookup with original identity, then one irreversible
        # builtin setdefault. CPython GIL provides the one-use linearization.
        if not _SUPPORTED or type(request) is not OriginalReportRequest:
            return ReportingOutcome(status='DENIED')
        # Lock the exact registry operation; never call arbitrary code under it.
        # The marker is committed before removing the record. An interrupted
        # removal retains a consumed record or a missing record: both deny replay.
        def take_once():
            record = self._records.get(id(request))
            if (record is None or record.handle is not request
                    or record.issuer is not self._issuer_identity):
                return None
            marker = object()
            if record.use.setdefault(0, marker) is not marker:
                return None
            self._records.pop(id(request), None)
            return record
        try:
            approved = _locked_call((self._registry_lock,), take_once)
        except Exception:
            return ReportingOutcome(status='DENIED')
        if approved is None:
            return ReportingOutcome(status='DENIED')
        try:
            _HOST_HEALTH.require()
            if not _authentic_reporting_sources(self._reader, self._evaluator):
                return ReportingOutcome(status='DENIED')
            state = self._reader._state
            forecast = ExactForecastRead.model_validate_json(approved.forecast_json)
            # The original grant is still live, scoped and unrevoked, independently
            # of approval. Source service checks it again at actual read/publication.
            now, elapsed = state.clocks(lambda: False)
            if not (now < approved.forecast_expiry[0] and elapsed < approved.forecast_expiry[1]):
                return ReportingOutcome(status='DENIED')
            state.require(approved.forecast_grant, forecast, lambda: False)
            if approved.action == 'FORECAST':
                return build_canonical_reporting_reader(self._reader).read_forecast(
                    approved.forecast_grant, forecast)
            if approved.action != 'SYNTHETIC_ACCURACY' or self._evaluator is None:
                return ReportingOutcome(status='DENIED')
            labels = ExactLabelRead.model_validate_json(approved.label_json)
            peer = self._evaluator._labels
            lnow, lelapsed = peer.clocks(lambda: False)
            if not (lnow < approved.label_expiry[0] and lelapsed < approved.label_expiry[1]):
                return ReportingOutcome(status='DENIED')
            def check_label():
                return peer.require(approved.label_grant, labels.canonical_json, lnow, lelapsed)
            _locked_call((peer.lock,), check_label)
            return build_canonical_reporting_reader(self._reader, self._evaluator).read_synthetic_accuracy(
                approved.forecast_grant, forecast, approved.label_grant, labels)
        except Exception:
            return ReportingOutcome(status='DENIED')


class ReportingRequestIssuer:
    """Retained ONLY by authenticated host code; never sent to a consumer."""

    __slots__ = ('_reader', '_evaluator', '_forecast_authority', '_label_authority',
                 '_operator', '_identity', '_records', '_registry_lock')

    def __init__(self, reader, evaluator, forecast_authority, label_authority, operator,
                 identity, records, registry_lock):
        self._reader, self._evaluator = reader, evaluator
        self._forecast_authority, self._label_authority = forecast_authority, label_authority
        self._operator, self._identity, self._records = operator, identity, records
        self._registry_lock = registry_lock

    def issue(self, operator, *, actor, action, visibility, forecast_grant, forecast,
              label_grant=None, labels=None, label_actor=None, lifetime_seconds=15):
        """Host-authenticated decision, fixed action/scope, original grants only.

        actor must come from host authentication, not request metadata. Visibility
        is fixed per reviewed offline action and cannot be widened by a request.
        """
        if (not _SUPPORTED or operator is not self._operator
                or type(actor) is not str or not actor or len(actor) > 256
                or type(visibility) is not str or not visibility or len(visibility) > 256
                or type(action) is not str
                or action not in ('FORECAST', 'SYNTHETIC_ACCURACY')
                or visibility != {'FORECAST': 'full-evidence',
                                  'SYNTHETIC_ACCURACY': 'synthetic-full-evidence'}[action]
                or type(lifetime_seconds) is not int or not 1 <= lifetime_seconds <= 30
                or type(forecast) is not ExactForecastRead):
            raise ReportingRequestDenied('report request denied')
        try:
            _HOST_HEALTH.require()
            if not _authentic_reporting_sources(self._reader, self._evaluator):
                raise ReportingRequestDenied('report request denied')
            checked = ExactForecastRead.model_validate_json(forecast.canonical_json)
            state = self._reader._state
            if checked.scope.canonical_json != state.scope:
                raise ReportingRequestDenied('report request denied')
            now, elapsed = state.clocks(lambda: False)
            f = state.require(forecast_grant, checked, lambda: False)
            p = state.principals.get(id(f.principal))
            if p is None or p.handle is not f.principal or p.subject != actor:
                raise ReportingRequestDenied('report request denied')
            fexpiry = (min(f.expires, p.expires, now + lifetime_seconds),
                       min(f.end, p.end, elapsed + lifetime_seconds))
            label_json, lexpiry = None, None
            if action == 'FORECAST':
                if label_grant is not None or labels is not None or label_actor is not None:
                    raise ReportingRequestDenied('report request denied')
            else:
                if (self._evaluator is None or type(labels) is not ExactLabelRead
                        or label_grant is None or type(label_actor) is not str
                        or not label_actor or len(label_actor) > 256):
                    raise ReportingRequestDenied('report request denied')
                checked_labels = ExactLabelRead.model_validate_json(labels.canonical_json)
                peer = self._evaluator._labels
                if checked_labels.scope.canonical_json != state.scope:
                    raise ReportingRequestDenied('report request denied')
                ln, le = peer.clocks(lambda: False)
                def check_label():
                    return peer.require(label_grant, checked_labels.canonical_json, ln, le)
                l = _locked_call((peer.lock,), check_label)
                lp = peer.principals.get(id(l.principal))
                if lp is None or lp.handle is not l.principal or lp.subject != label_actor:
                    raise ReportingRequestDenied('report request denied')
                label_json = checked_labels.canonical_json
                lexpiry = (min(l.expires, lp.expires, ln + lifetime_seconds),
                           min(l.end, lp.end, le + lifetime_seconds))
            h = OriginalReportRequest()
            record = _Approved(h, self._identity, action, visibility, actor, label_actor, forecast_grant,
                               checked.canonical_json, label_grant, label_json, fexpiry, lexpiry)
            # Exact dict operations only; no unbounded outstanding authorities.
            # Denial does not evict a legitimate pending approval or renew it.
            def register():
                if (id(h) in self._records
                        or len(self._records) >= _MAX_OUTSTANDING_REQUESTS):
                    raise ReportingRequestDenied('report request denied')
                self._records[id(h)] = record
            _locked_call((self._registry_lock,), register)
            return h
        except Exception:
            raise ReportingRequestDenied('report request denied') from None


def build_host_reporting_requests(reader, forecast_authority, *, operator,
                                  evaluator=None, label_authority=None):
    """Host-only bootstrap. Retain issuer; expose dispatcher only to consumers."""
    if (not _SUPPORTED or type(operator) is not object
            or type(forecast_authority) is not ReadAuthority
            or forecast_authority._state is not reader._state
            or reader._state.operator is not operator
            or not _authentic_reporting_sources(reader, evaluator)
            or (evaluator is None) != (label_authority is None)):
        raise ReportingRequestDenied('protected reporting bootstrap required')
    if evaluator is not None and (
            type(label_authority) is not ActualLabelAuthority
            or label_authority._state is not evaluator._labels
            or evaluator._labels.operator is not operator):
        raise ReportingRequestDenied('protected reporting bootstrap required')
    _HOST_HEALTH.require()
    identity, records, registry_lock = object(), {}, RLock()
    issuer = ReportingRequestIssuer(reader, evaluator, forecast_authority, label_authority,
                                    operator, identity, records, registry_lock)
    dispatcher = ReportingRequestDispatcher(reader, evaluator, records, identity, registry_lock)
    return issuer, dispatcher
