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

    def lookup_claimed(self, idempotency_id, claim):
        from src.providers._forecast_read_order import _consume_claim
        original = _consume_claim(self, claim, idempotency_id)
        try:
            return self.lookup(idempotency_id)
        finally:
            original.finished = True
