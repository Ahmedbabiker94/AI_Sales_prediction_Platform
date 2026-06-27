from src.database.sales_repository import (
    SalesRepository
)

repo = SalesRepository()

rows = repo.get_sales_history(
    store=1,
    dept=1
)

print(
    f"Rows found: {len(rows)}"
)

print(rows[:5])