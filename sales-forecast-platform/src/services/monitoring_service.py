from sqlalchemy import text

from src.database.db import engine
from src.repositories.job_status_repository import JobStatusRepository


class MonitoringService:

    def __init__(self):

        self.job_repo = JobStatusRepository()

    def get_health_status(self):

        status = {
            "api": "healthy",
            "database": "unhealthy"
        }

        try:

            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))

            status["database"] = "healthy"

        except Exception:
            pass

        jobs = self.job_repo.get_all_statuses()

        for job in jobs:

            status[job["job_name"]] = job["status"]

        return status