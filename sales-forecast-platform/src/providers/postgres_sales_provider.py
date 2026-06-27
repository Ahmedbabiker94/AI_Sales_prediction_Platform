from src.providers.sales_data_provider import (
    SalesDataProvider
)

from src.services.forecast_history_service import (
    ForecastHistoryService
)


class PostgresSalesProvider(
    SalesDataProvider
):

    def __init__(self):

        self.history_service = (
            ForecastHistoryService()
        )

    def get_history(
        self,
        store: int,
        dept: int
    ):

        return (
            self.history_service
            .get_history(
                store=store,
                dept=dept
            )
        )
    def get_actual_sales(
        self,
        store,
        dept,
        date
    ):

        return (
            self.history_service
            .get_actual_sales(
                store,
                dept,
                date
            )
        )