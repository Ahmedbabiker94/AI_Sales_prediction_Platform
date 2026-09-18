from sqlalchemy import text

from src.database.db import engine


class SchedulerLockRepository:

    def acquire_lock(
        self,
        scheduler_name,
        hostname
    ):
        # Create the lock row if it does not exist yet.
        insert_query = text("""
            INSERT INTO scheduler_lock (
                scheduler_name,
                locked,
                locked_at,
                hostname
            )
            VALUES (
                :scheduler_name,
                FALSE,
                NULL,
                NULL
            )
            ON CONFLICT (scheduler_name) DO NOTHING
        """)

        # Acquire the lock only if it is currently free.
        update_query = text("""
            UPDATE scheduler_lock
            SET
                locked = TRUE,
                locked_at = NOW(),
                hostname = :hostname
            WHERE
                scheduler_name = :scheduler_name
                AND locked = FALSE
        """)

        with engine.begin() as conn:
            conn.execute(
                insert_query,
                {
                    "scheduler_name": scheduler_name
                }
            )

            result = conn.execute(
                update_query,
                {
                    "scheduler_name": scheduler_name,
                    "hostname": hostname
                }
            )

        return result.rowcount == 1

    def release_lock(
        self,
        scheduler_name
    ):

        query = text("""

            UPDATE scheduler_lock

            SET

                locked = FALSE,

                locked_at = NULL,

                hostname = NULL

            WHERE

                scheduler_name = :scheduler_name

        """)

        with engine.begin() as conn:

            conn.execute(

                query,

                {

                    "scheduler_name": scheduler_name

                }

            )

    def is_running(
        self,
        scheduler_name="main_scheduler"
    ):

        query = text("""

            SELECT locked

            FROM scheduler_lock

            WHERE scheduler_name = :scheduler_name

        """)

        with engine.connect() as conn:

            result = conn.execute(

                query,

                {

                    "scheduler_name": scheduler_name

                }

            ).scalar()

        return bool(result)