from src.database.history_repository import (
    HistoryRepository
)


class HistoryService:

    def __init__(self):

        self.repository = (
            HistoryRepository()
        )

    def get_recent_sales(
        self,
        store: int,
        dept: int,
        limit: int = 52
    ):

        return self.repository.get_recent_sales(
            store=store,
            dept=dept,
            limit=limit
        )