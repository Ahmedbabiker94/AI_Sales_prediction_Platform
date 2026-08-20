from sqlalchemy import text

from src.database.db import engine


class SchedulerLockRepository:

    def acquire_lock(
        self,
        scheduler_name,
        hostname
    ):

        query = text("""

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

            result = conn.execute(

                query,

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