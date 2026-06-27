from sqlalchemy import text

from src.database.db import engine


class SalesRepository:

    def get_sales_history(
        self,
        store,
        dept
    ):

        query = text(
            """
            SELECT
                date,
                weekly_sales
            FROM sales_history
            WHERE store = :store
            AND dept = :dept
            ORDER BY date
            """
        )

        with engine.connect() as conn:

            result = conn.execute(
                query,
                {
                    "store": store,
                    "dept": dept
                }
            )

            rows = result.fetchall()

        return rows