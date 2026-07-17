from src.services.forecast_accuracy_service import (
    ForecastAccuracyService
)

from src.services.job_tracker_service import (
    JobTrackerService
)

from src.services.job_execution_service import (
    JobExecutionService
)

from src.core.metrics import (
    ACCURACY_EVALUATIONS,
    ACCURACY_JOB_COUNTER,
    JOB_DURATION
)


class ForecastAccuracyJob:

    def __init__(self):

        self.service = ForecastAccuracyService()

        self.tracker = JobTrackerService()

        self.execution_service = JobExecutionService()

    def run(self):

        started_at = self.execution_service.start()

        ACCURACY_JOB_COUNTER.inc()

        with JOB_DURATION.labels(
            job_name="accuracy_job"
        ).time():

            try:

                results = (
                    self.service
                    .evaluate_all_pending_forecasts()
                )

                ACCURACY_EVALUATIONS.inc(
                    len(results)
                )

                self.tracker.mark_success(
                    "accuracy_job"
                )

                self.execution_service.finish_success(

                    job_name="accuracy_job",

                    started_at=started_at,

                    records_processed=len(results)

                )

                print(
                    f"Processed {len(results)} forecasts"
                )

                return results

            except Exception as e:

                self.tracker.mark_failed(
                    "accuracy_job"
                )

                self.execution_service.finish_failed(

                    job_name="accuracy_job",

                    started_at=started_at,

                    error_message=str(e)

                )

                raise


if __name__ == "__main__":

    ForecastAccuracyJob().run()