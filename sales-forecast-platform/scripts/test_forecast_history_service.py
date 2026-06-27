from src.services.forecast_history_service import (
    ForecastHistoryService
)

service = ForecastHistoryService()

history = service.get_history(
    store=1,
    dept=1
)

print(len(history))
print(history[0])