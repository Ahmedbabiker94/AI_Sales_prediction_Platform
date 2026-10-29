"""Host-owned exact read port. Never exposed to a request as a selectable backend."""
from dataclasses import dataclass, field
from src.repositories.forecast_read_repository import ForecastReadRepository
from src.database.offline_forecast_postgres import reject


@dataclass(frozen=True)
class ForecastReadProvider:
    repository: ForecastReadRepository = field(repr=False)

    def __post_init__(self):
        if type(self.repository) is not ForecastReadRepository:
            reject()

    def lookup(self, idempotency_id):
        return self.repository.lookup(idempotency_id)
