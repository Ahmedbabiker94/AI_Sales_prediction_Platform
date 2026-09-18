from sqlalchemy import text

from src.database.db import engine


class JobExecutionRepository:

    def save_execution(
        self,
        job_name,
        started_at,
        finished_at,
        duration_seconds,
        status,
        rows_processed=0,
        error_message=None
    ):

        query = text("""
            INSERT INTO job_execution_history (

                job_name,
                started_at,
                finished_at,
                duration_seconds,
                status,
                rows_processed,
                error_message

            )
            VALUES (

                :job_name,
                :started_at,
                :finished_at,
                :duration_seconds,
                :status,
                :rows_processed,
                :error_message

            )
        """)

        with engine.begin() as conn:

            conn.execute(
                query,
                {
                    "job_name": job_name,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "duration_seconds": duration_seconds,
                    "status": status,
                    "rows_processed": rows_processed,
                    "error_message": error_message
                }
            )

    def get_recent_executions(
        self,
        limit=50
    ):

        query = text("""
            SELECT

                id,
                job_name,
                started_at,
                finished_at,
                duration_seconds,
                status,
                rows_processed,
                error_message

            FROM job_execution_history

            ORDER BY started_at DESC

            LIMIT :limit
        """)

        with engine.connect() as conn:

            rows = conn.execute(
                query,
                {
                    "limit": limit
                }
            ).mappings().all()

        return rows

    def get_last_success(self, job_name):
        query = text("""
            SELECT finished_at
            FROM job_execution_history
            WHERE job_name = :job_name
              AND status = 'success'
              AND finished_at IS NOT NULL
            ORDER BY finished_at DESC
            LIMIT 1
        """)

        with engine.connect() as conn:
            return conn.execute(
                query,
                {"job_name": job_name}
            ).scalar()

    def get_last_failure(self, job_name):
        query = text("""
            SELECT finished_at
            FROM job_execution_history
            WHERE job_name = :job_name
              AND status = 'failed'
              AND finished_at IS NOT NULL
            ORDER BY finished_at DESC
            LIMIT 1
        """)

        with engine.connect() as conn:
            return conn.execute(
                query,
                {"job_name": job_name}
            ).scalar()

    def get_last_execution(self, job_name):
        query = text("""
            SELECT
                finished_at,
                status
            FROM job_execution_history
            WHERE job_name = :job_name
              AND finished_at IS NOT NULL
            ORDER BY finished_at DESC
            LIMIT 1
        """)

        with engine.connect() as conn:
            row = conn.execute(
                query,
                {"job_name": job_name}
            ).mappings().first()

        return row
