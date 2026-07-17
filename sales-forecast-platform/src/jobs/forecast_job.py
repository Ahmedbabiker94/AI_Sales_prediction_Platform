from src.services.recursive_forecast_service import (
    RecursiveForecastService
)

from src.repositories.job_status_repository import (
    JobStatusRepository
)

from src.services.job_execution_service import (
    JobExecutionService
)

from src.core.metrics import (
    FORECAST_JOB_COUNTER,
    JOB_DURATION
)


class ForecastJob:

    def __init__(self):

        self.service = RecursiveForecastService()

        self.status_repo = JobStatusRepository()

        self.execution_service = JobExecutionService()

    def run(self):

        started_at = self.execution_service.start()

        FORECAST_JOB_COUNTER.inc()

        with JOB_DURATION.labels(
            job_name="forecast_job"
        ).time():

            try:

                print(
                    "Running Forecast Job..."
                )

                forecast_targets = [

                    {
                        "store": 1,
                        "dept": 1
                    },

                    {
                        "store": 2,
                        "dept": 1
                    }

                ]

                total_predictions = 0

                for item in forecast_targets:

                    predictions = self.service.forecast(

                        store=item["store"],
                        dept=item["dept"],
                        weeks=4

                    )

                    total_predictions += len(predictions)

                FORECAST_JOB_COUNTER.inc()

                self.status_repo.update_status(

                    "forecast_job",
                    "success"

                )

                self.execution_service.finish_success(

                    job_name="forecast_job",
                    started_at=started_at,
                    records_processed=total_predictions

                )

                print(
                    "Forecast Job Completed"
                )

            except Exception as e:

                self.status_repo.update_status(

                    "forecast_job",
                    "failed"

                )

                self.execution_service.finish_failed(

                    job_name="forecast_job",
                    started_at=started_at,
                    error_message=str(e)

                )

                raise


if __name__ == "__main__":

    ForecastJob().run()