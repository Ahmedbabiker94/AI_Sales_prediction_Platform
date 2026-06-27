from sqlalchemy import text

from src.database.db import engine


class HistoryRepository:

    def get_recent_sales(
        self,
        store,
        dept,
        limit=52
    ):

        query = text("""
            SELECT weekly_sales
            FROM sales_history
            WHERE store = :store
            AND dept = :dept
            ORDER BY date DESC
            LIMIT :limit
        """)

        with engine.connect() as conn:

            result = conn.execute(
                query,
                {
                    "store": store,
                    "dept": dept,
                    "limit": limit
                }
            )

            rows = result.fetchall()

        return [
            row[0]
            for row in rows
        ]

    def get_recent_history(
        self,
        store,
        dept,
        limit=52
    ):

        query = text("""
            SELECT
                date,
                store,
                dept,
                weekly_sales,
                is_holiday,
                temperature,
                fuel_price,
                cpi,
                unemployment
            FROM sales_history
            WHERE store = :store
            AND dept = :dept
            ORDER BY date DESC
            LIMIT :limit
        """)

        with engine.connect() as conn:

            result = conn.execute(
                query,
                {
                    "store": store,
                    "dept": dept,
                    "limit": limit
                }
            )

            rows = result.mappings().all()

        return rows
    
    def get_actual_sales(
        self,
        store,
        dept,
        date
    ):

        query = text("""
            SELECT weekly_sales
            FROM sales_history
            WHERE store = :store
            AND dept = :dept
            AND date = :date
            LIMIT 1
        """)

        with engine.connect() as conn:

            row = conn.execute(
                query,
                {
                    "store": store,
                    "dept": dept,
                    "date": date
                }
            ).fetchone()

        if row is None:

            return None

        return float(row[0])