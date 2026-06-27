from src.services.history_service import (
    HistoryService
)

from src.features.history_feature_calculator import (
    HistoryFeatureCalculator
)


class HistoryFeatureBuilder:

    def __init__(self):

        self.history_service = (
            HistoryService()
        )

    def build(
        self,
        store: int,
        dept: int
    ):

        sales = (
            self.history_service
            .get_recent_sales(
                store=store,
                dept=dept,
                limit=52
            )
        )

        return (
            HistoryFeatureCalculator
            .calculate(sales)
        )