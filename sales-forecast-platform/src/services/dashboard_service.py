from src.repositories.accuracy_repository import (
    AccuracyRepository
)

from src.services.insights_service import (
    InsightsService
)
from src.services.job_execution_service import (
    JobExecutionService
)

class DashboardService:

    def __init__(self):

        self.accuracy_repo = (
            AccuracyRepository()
        )

        self.insights_service = (
            InsightsService()
        )
        self.job_execution_service = (
            JobExecutionService()
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
    def get_job_history(
        self,
        limit: int = 50
    ):

        return (
            self.job_execution_service
            .get_recent_executions(limit)
        )    