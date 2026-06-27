from src.database.history_repository import (
    HistoryRepository
)


class ForecastHistoryService:

    def __init__(self):

        self.repository = (
            HistoryRepository()
        )

    def get_history(
        self,
        store,
        dept
    ):

        return (
            self.repository
            .get_recent_history(
                store,
                dept
            )
        )

    def get_actual_sales(
        self,
        store,
        dept,
        date
    ):

        return (
            self.repository
            .get_actual_sales(
                store,
                dept,
                date
            )
        )