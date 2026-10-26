"""Legacy job orchestration; operational collaborators require host composition."""
from src.runtime.bootstrap import Runtime, require_runtime


class ForecastAccuracyJob:
    def __init__(self, *, runtime=None):
        self._runtime = require_runtime(runtime) if runtime is not None else Runtime()

    def run(self):
        service = self._runtime.get('accuracy_service')
        tracker = self._runtime.get('job_tracker')
        execution_service = self._runtime.get('job_execution')
        metrics = self._runtime.get('job_metrics')
        ACCURACY_EVALUATIONS = metrics.ACCURACY_EVALUATIONS
        ACCURACY_JOB_COUNTER = metrics.ACCURACY_JOB_COUNTER
        JOB_DURATION = metrics.JOB_DURATION

        started_at = execution_service.start()

        ACCURACY_JOB_COUNTER.inc()

        with JOB_DURATION.labels(
            job_name="accuracy_job"
        ).time():

            try:

                results = (
                    service
                    .evaluate_all_pending_forecasts()
                )

                ACCURACY_EVALUATIONS.inc(
                    len(results)
                )

                tracker.mark_success(
                    "accuracy_job"
                )

                execution_service.finish_success(

                    job_name="accuracy_job",

                    started_at=started_at,

                    records_processed=len(results)

                )

                print(
                    f"Processed {len(results)} forecasts"
                )

                return results

            except Exception as e:

                tracker.mark_failed(
                    "accuracy_job"
                )

                execution_service.finish_failed(

                    job_name="accuracy_job",

                    started_at=started_at,

                    error_message=str(e)

                )

                raise


if __name__ == "__main__":

    ForecastAccuracyJob().run()