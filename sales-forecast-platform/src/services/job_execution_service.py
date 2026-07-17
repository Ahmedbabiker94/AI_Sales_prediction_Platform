from datetime import datetime

from src.repositories.job_execution_repository import (
    JobExecutionRepository
)


class JobExecutionService:

    def __init__(self):

        self.repo = JobExecutionRepository()

    def start(self):

        return datetime.utcnow()

    def finish_success(

        self,
        job_name,
        started_at,
        records_processed=0

    ):

        finished_at = datetime.utcnow()

        duration = (
            finished_at - started_at
        ).total_seconds()

        self.repo.save_execution(

            job_name=job_name,

            started_at=started_at,
            finished_at=finished_at,

            duration_seconds=duration,

            status="success",

            rows_processed=records_processed,

            error_message=None

        )

    def finish_failed(

        self,
        job_name,
        started_at,
        error_message

    ):

        finished_at = datetime.utcnow()

        duration = (
            finished_at - started_at
        ).total_seconds()

        self.repo.save_execution(

            job_name=job_name,

            started_at=started_at,
            finished_at=finished_at,

            duration_seconds=duration,

            status="failed",

            rows_processed=0,

            error_message=error_message

        )

    def get_recent_executions(
        self,
        limit=50
    ):

        return self.repo.get_recent_executions(limit)