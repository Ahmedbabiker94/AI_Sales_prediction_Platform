"""Private typed storage composition. Not imported by any application consumer.

The protected host owns this provider and its credentials. A Python underscore
is not authentication against a host that can mutate code, credentials or state.
"""
from dataclasses import dataclass
from src.repositories.postgres_forecast_evidence_repository import _PostgresForecastEvidenceRepository
from src.database.offline_forecast_postgres import reject


@dataclass(frozen=True)
class _ForecastEvidenceStorageProvider:
    repository: _PostgresForecastEvidenceRepository

    def __post_init__(self):
        if type(self.repository) is not _PostgresForecastEvidenceRepository:
            reject()

    def _append_initial(self, records):
        return self.repository._append_initial(records)

    def _lookup(self, idempotency_id, *, expected=None):
        return self.repository._lookup(idempotency_id, expected=expected)
