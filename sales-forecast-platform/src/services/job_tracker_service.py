from src.repositories.job_status_repository import (
    JobStatusRepository
)


class JobTrackerService:

    def __init__(self):

        self.repo = (
            JobStatusRepository()
        )

    def mark_success(
        self,
        job_name
    ):

        self.repo.update_status(
            job_name=job_name,
            status="success"
        )

    def mark_failed(
        self,
        job_name
    ):

        self.repo.update_status(
            job_name=job_name,
            status="failed"
        )