import time
from datetime import timezone
from src.repositories.job_execution_repository import (
    JobExecutionRepository
)

from src.core.metrics import (
    JOB_LAST_SUCCESS_TIMESTAMP,
    JOB_LAST_FAILURE_TIMESTAMP,
    JOB_LAST_EXECUTION_TIMESTAMP,
    JOB_LAST_EXECUTION_STATUS
)


class JobHealthMetricsService:

    JOBS = [
        "forecast_job",
        "accuracy_job",
        "report_job"
    ]

    def __init__(self):
        self.repo = JobExecutionRepository()

    @staticmethod
    def _timestamp(value):
        if value is None:
            return 0.0

        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)

        return value.timestamp()

    def refresh(self):
        for job_name in self.JOBS:

            last_success = (
                self.repo
                .get_last_success(job_name)
            )

            last_failure = (
                self.repo
                .get_last_failure(job_name)
            )

            last_execution = (
                self.repo
                .get_last_execution(job_name)
            )

            JOB_LAST_SUCCESS_TIMESTAMP.labels(
                job_name=job_name
            ).set(
                self._timestamp(last_success)
            )

            JOB_LAST_FAILURE_TIMESTAMP.labels(
                job_name=job_name
            ).set(
                self._timestamp(last_failure)
            )

            if last_execution:
                JOB_LAST_EXECUTION_TIMESTAMP.labels(
                    job_name=job_name
                ).set(
                    self._timestamp(
                        last_execution["finished_at"]
                    )
                )

                current_status = (
                    last_execution["status"]
                )

                for status in ["success", "failed"]:
                    JOB_LAST_EXECUTION_STATUS.labels(
                        job_name=job_name,
                        status=status
                    ).set(
                        1.0
                        if current_status == status
                        else 0.0
                    )
            else:
                JOB_LAST_EXECUTION_TIMESTAMP.labels(
                    job_name=job_name
                ).set(0.0)

                for status in ["success", "failed"]:
                    JOB_LAST_EXECUTION_STATUS.labels(
                        job_name=job_name,
                        status=status
                    ).set(0.0)

        return {
            "refreshed_at": time.time()
        }