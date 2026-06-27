from src.repositories.accuracy_repository import (
    AccuracyRepository
)

from src.services.insights_service import (
    InsightsService
)


class DashboardService:

    def __init__(self):

        self.accuracy_repo = (
            AccuracyRepository()
        )

        self.insights_service = (
            InsightsService()
        )

    def get_accuracy_summary(self):

        return (
            self.accuracy_repo
            .get_accuracy_summary()
        )

    def get_worst_forecasts(
        self,
        limit=10
    ):

        return (
            self.accuracy_repo
            .get_worst_forecasts(limit)
        )

    def get_forecast_insights(
        self,
        store=None,
        dept=None
    ):

        return (
            self.insights_service
            .get_insights(
                store=store,
                dept=dept
            )
        )