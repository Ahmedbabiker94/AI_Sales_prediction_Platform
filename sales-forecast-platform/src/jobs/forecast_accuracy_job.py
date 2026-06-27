import time

from src.services.forecast_accuracy_service import (
    ForecastAccuracyService
)

from src.services.job_tracker_service import (
    JobTrackerService
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

    def run(self):

        start = time.time()

        try:

            results = (

                self.service
                .evaluate_all_pending_forecasts()

            )

            ACCURACY_EVALUATIONS.inc(

                len(results)

            )

            ACCURACY_JOB_COUNTER.inc()

            JOB_DURATION.labels(
                "accuracy_job"
            ).observe(

                time.time() - start

            )

            self.tracker.mark_success(

                "accuracy_job"

            )

            print(

                f"Processed {len(results)} forecasts"

            )

            return results

        except Exception:

            self.tracker.mark_failed(

                "accuracy_job"

            )

            raise


if __name__ == "__main__":

    ForecastAccuracyJob().run()