from src.database.history_repository import (
    HistoryRepository
)

repo = HistoryRepository()

sales = repo.get_recent_sales(
    store=1,
    dept=1
)

print(sales[:10])
print(len(sales))