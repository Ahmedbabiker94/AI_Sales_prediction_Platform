from src.services.recursive_forecast_service import (
    RecursiveForecastService
)

service = (
    RecursiveForecastService()
)

result = service.forecast(
    store=1,
    dept=1,
    weeks=4
)

print(result)