"""Reader-only adapter of the ORIGINAL accepted exact batch verifier.

No inheritance from the write repository, no instance of it and no writer field.
Only its three read methods are reused; accepted SQL and byte checks stay exact.
"""
from dataclasses import dataclass, field
from src.database.forecast_read_connection import ForecastReadConnectionFactory
from src.database.offline_forecast_postgres import reject
from src.data.contracts.forecast_evidence_storage import ForecastStorageScope
from src.repositories.postgres_forecast_evidence_repository import _PostgresForecastEvidenceRepository


@dataclass(frozen=True)
class ForecastReadRepository:
    reader: ForecastReadConnectionFactory = field(repr=False)
    scope: ForecastStorageScope

    def __post_init__(self):
        if type(self.reader) is not ForecastReadConnectionFactory or type(self.scope) is not ForecastStorageScope:
            reject()
        ForecastStorageScope.model_validate_json(self.scope.canonical_json)

    _batch = _PostgresForecastEvidenceRepository._batch
    _find = _PostgresForecastEvidenceRepository._find
    lookup = _PostgresForecastEvidenceRepository._lookup
