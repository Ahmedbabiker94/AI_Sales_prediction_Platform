from sqlalchemy import text

from src.database.db import engine


class AccuracyRepository:

    def get_accuracy_summary(self):

        query = text("""
            SELECT
                AVG(absolute_error) as avg_absolute_error,
                AVG(percentage_error) as avg_percentage_error,
                COUNT(*) as total_forecasts
            FROM forecast_accuracy
        """)

        with engine.connect() as conn:

            row = conn.execute(query).fetchone()

        return {
            "avg_absolute_error": float(row[0] or 0),
            "avg_percentage_error": float(row[1] or 0),
            "total_forecasts": int(row[2] or 0)
        }

    
    def get_worst_forecasts(
        self,
        limit=10
    ):

        query = text("""
            SELECT
                store,
                dept,
                forecast_date,
                actual_sales,
                predicted_sales,
                percentage_error
            FROM forecast_accuracy
            ORDER BY percentage_error DESC
            LIMIT :limit
        """)

        with engine.connect() as conn:

            rows = conn.execute(
                query,
                {"limit": limit}
            ).fetchall()

        return [
            {
                "store": row[0],
                "dept": row[1],
                "forecast_date": row[2],
                "actual_sales": row[3],
                "predicted_sales": row[4],
                "percentage_error": row[5]
            }
            for row in rows
        ]