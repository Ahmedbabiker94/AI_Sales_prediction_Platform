"""Host-composed cache-free presentation of original published source evidence.

No saved-result input, SQL, source selection callback, report lookup or new P.
"""
from dataclasses import dataclass, field
from src.data.contracts.canonical_forecast_read import ExactForecastRead, CanonicalReadOutcome
from src.data.contracts.canonical_reporting import ForecastReportView, SyntheticAccuracyReportView, ReportingOutcome
from src.services.canonical_forecast_read_service import CanonicalForecastReadService
from src.services.stored_forecast_accuracy_service import StoredForecastAccuracyService
from src.data.contracts.stored_forecast_accuracy import ExactLabelRead, AccuracyOutcome


@dataclass(frozen=True)
class CanonicalReportingReadService:
    _reader: CanonicalForecastReadService = field(repr=False)

    _evaluator: StoredForecastAccuracyService | None = field(default=None, repr=False)

    def __post_init__(self):
        if type(self._reader) is not CanonicalForecastReadService:
            raise ValueError('original source required')
        if self._evaluator is not None and (type(self._evaluator) is not StoredForecastAccuracyService
                or self._evaluator._pinned_reader is not self._reader):
            raise ValueError('original bound evaluator required')

    def read_forecast(self, grant, selection):
        if type(selection) is not ExactForecastRead:
            return ReportingOutcome(status='DENIED')
        try:
            approved = ExactForecastRead.model_validate_json(selection.canonical_json)
            result = self._reader.read(grant, approved)
            if type(result) is not CanonicalReadOutcome:
                return ReportingOutcome(status='UNAVAILABLE')
            result = CanonicalReadOutcome.model_validate_json(result.canonical_json)
            if result.status != 'VERIFIED_RECORD_SET':
                return ReportingOutcome(status={'DENIED':'DENIED','NOT_FOUND':'NOT_AVAILABLE',
                    'UNAVAILABLE':'UNAVAILABLE'}[result.status])
            batch = result.records.stored.batch
            if (batch.scope != approved.scope or batch.idempotency_id != approved.idempotency_id
                    or batch.content_sha256 != approved.expected_content_sha256):
                return ReportingOutcome(status='UNAVAILABLE')
            result.records.display_values
            return ReportingOutcome(status='AVAILABLE', view=ForecastReportView(source=result.records))
        except Exception:
            return ReportingOutcome(status='UNAVAILABLE')

    def read_synthetic_accuracy(self, forecast_grant, forecast, label_grant, labels):
        if type(forecast) is not ExactForecastRead or type(labels) is not ExactLabelRead:
            return ReportingOutcome(status='DENIED')
        if self._evaluator is None:
            return ReportingOutcome(status='NOT_AVAILABLE')
        try:
            forecast = ExactForecastRead.model_validate_json(forecast.canonical_json)
            labels = ExactLabelRead.model_validate_json(labels.canonical_json)
            result = self._evaluator.evaluate(forecast_grant, forecast, label_grant, labels)
            if type(result) is not AccuracyOutcome:
                return ReportingOutcome(status='UNAVAILABLE')
            result = AccuracyOutcome.model_validate_json(result.canonical_json)
            if result.status != 'VERIFIED_EVALUATION':
                return ReportingOutcome(status={'DENIED':'DENIED','LABELS_NOT_READY':'NOT_AVAILABLE',
                    'INCOMPATIBLE_SCOPE':'INCOMPATIBLE_SCOPE','UNAVAILABLE':'UNAVAILABLE'}[result.status])
            evidence = result.evidence
            if (evidence.forecast_selection_json != forecast.canonical_json
                    or evidence.label_selection != labels):
                return ReportingOutcome(status='UNAVAILABLE')
            return ReportingOutcome(status='AVAILABLE', view=SyntheticAccuracyReportView(source=evidence))
        except Exception:
            return ReportingOutcome(status='UNAVAILABLE')


def build_canonical_reporting_reader(reader, evaluator=None):
    """Trusted host only; never select a source from request data."""
    return CanonicalReportingReadService(reader, evaluator)
