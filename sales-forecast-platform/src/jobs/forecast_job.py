"""Legacy job orchestration; operational collaborators require host composition."""
from src.runtime.bootstrap import Runtime, require_runtime


class ForecastJob:
    def __init__(self, *, runtime=None):
        self._runtime = require_runtime(runtime) if runtime is not None else Runtime()

    def run(self):
        service = self._runtime.get('recursive_forecast')
        status_repo = self._runtime.get('job_status')
        execution_service = self._runtime.get('job_execution')
        metrics = self._runtime.get('job_metrics')
        FORECAST_JOB_COUNTER = metrics.FORECAST_JOB_COUNTER
        JOB_DURATION = metrics.JOB_DURATION

        started_at = execution_service.start()

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

                    predictions = service.forecast(

                        store=item["store"],
                        dept=item["dept"],
                        weeks=4

                    )

                    total_predictions += len(predictions)


                status_repo.update_status(

                    "forecast_job",
                    "success"

                )

                execution_service.finish_success(

                    job_name="forecast_job",
                    started_at=started_at,
                    records_processed=total_predictions

                )

                print(
                    "Forecast Job Completed"
                )

            except Exception as e:

                status_repo.update_status(

                    "forecast_job",
                    "failed"

                )

                execution_service.finish_failed(

                    job_name="forecast_job",
                    started_at=started_at,
                    error_message=str(e)

                )

                raise


if __name__ == "__main__":

    ForecastJob().run()