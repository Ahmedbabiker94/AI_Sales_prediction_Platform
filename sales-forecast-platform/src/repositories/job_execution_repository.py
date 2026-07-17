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