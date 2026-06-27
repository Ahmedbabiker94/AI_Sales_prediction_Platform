from src.services.history_service import (
    HistoryService
)

service = HistoryService()

sales = service.get_recent_sales(
    store=1,
    dept=1
)

print(len(sales))

print(sales[:10])