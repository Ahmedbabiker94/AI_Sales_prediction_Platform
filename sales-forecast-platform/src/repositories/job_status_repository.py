from sqlalchemy import text

from src.database.db import engine


class JobStatusRepository:

    def update_status(
        self,
        job_name,
        status
    ):

        query = text("""
            INSERT INTO job_status (
                job_name,
                last_run,
                status
            )
            VALUES (
                :job_name,
                NOW(),
                :status
            )

            ON CONFLICT (job_name)

            DO UPDATE SET

                last_run = NOW(),
                status = EXCLUDED.status
        """)

        with engine.begin() as conn:

            conn.execute(
                query,
                {
                    "job_name": job_name,
                    "status": status
                }
            )

    def get_all_statuses(self):

        query = text("""
            SELECT
                job_name,
                last_run,
                status
            FROM job_status
        """)

        with engine.connect() as conn:

            rows = conn.execute(
                query
            ).mappings().all()

        return rows