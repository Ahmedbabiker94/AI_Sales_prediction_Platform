"""Presentation data only: never authentication or a new verified publication."""
import hashlib
from typing import Literal
from pydantic import model_validator
from src.data.contracts.forecast_evidence_storage import StorageContract
from src.data.contracts.canonical_forecast_read import CanonicalReadRecordSet
from src.data.contracts.stored_forecast_accuracy import AccuracyEvidence


class ForecastReportView(StorageContract):
    kind: Literal['FORECAST_ONLY'] = 'FORECAST_ONLY'
    boundary: Literal['ORIGINAL_B5_1_PUBLICATION'] = 'ORIGINAL_B5_1_PUBLICATION'
    source: CanonicalReadRecordSet

    @property
    def display_values(self):
        return self.source.display_values

    @property
    def content_sha256(self):
        """Presentation integrity, not access authority."""
        return hashlib.sha256(self.canonical_bytes).hexdigest()


class SyntheticAccuracyReportView(StorageContract):
    kind: Literal['CONTROLLED_OFFLINE_SYNTHETIC_ACCURACY'] = 'CONTROLLED_OFFLINE_SYNTHETIC_ACCURACY'
    boundary: Literal['ORIGINAL_B5_2_PUBLICATION'] = 'ORIGINAL_B5_2_PUBLICATION'
    source: AccuracyEvidence

    @property
    def content_sha256(self):
        return hashlib.sha256(self.canonical_bytes).hexdigest()


class ReportingOutcome(StorageContract):
    status: Literal['AVAILABLE', 'DENIED', 'NOT_AVAILABLE', 'INCOMPATIBLE_SCOPE', 'UNAVAILABLE']
    view: ForecastReportView | SyntheticAccuracyReportView | None = None

    @model_validator(mode='after')
    def coherent(self):
        if (self.status == 'AVAILABLE') != (self.view is not None):
            raise ValueError('view only on available presentation')
        return self
