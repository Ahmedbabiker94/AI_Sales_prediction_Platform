import time

from src.services.recursive_forecast_service import (
    RecursiveForecastService
)

from src.repositories.job_status_repository import (
    JobStatusRepository
)

from src.core.metrics import (
    FORECAST_JOB_COUNTER,
    JOB_DURATION
)


class ForecastJob:

    def __init__(self):

        self.service = RecursiveForecastService()

        self.status_repo = JobStatusRepository()

    def run(self):

        start = time.time()

        try:

            print("Running Forecast Job...")

            forecast_targets = [

                {"store": 1, "dept": 1},
                {"store": 2, "dept": 1},

            ]

            for item in forecast_targets:

                self.service.forecast(

                    store=item["store"],
                    dept=item["dept"],
                    weeks=4

                )

            self.status_repo.update_status(

                "forecast_job",
                "success"

            )

            FORECAST_JOB_COUNTER.inc()

            JOB_DURATION.labels(
                "forecast_job"
            ).observe(

                time.time() - start

            )

            print("Forecast Job Completed")

        except Exception:

            self.status_repo.update_status(

                "forecast_job",
                "failed"

            )

            raise


if __name__ == "__main__":

    ForecastJob().run()