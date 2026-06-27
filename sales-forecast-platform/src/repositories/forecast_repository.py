from sqlalchemy import text

from src.database.db import engine


class ForecastRepository:

    def save_forecast(
        self,
        store,
        dept,
        forecast_date,
        predicted_sales,
        model_version
    ):

        query = text("""
            INSERT INTO forecast_predictions (
                store,
                dept,
                forecast_date,
                predicted_sales,
                model_version
            )
            VALUES (
                :store,
                :dept,
                :forecast_date,
                :predicted_sales,
                :model_version
            )
        """)

        with engine.begin() as conn:

            conn.execute(
                query,
                {
                    "store": store,
                    "dept": dept,
                    "forecast_date": forecast_date,
                    "predicted_sales": predicted_sales,
                    "model_version": model_version
                }
            )

    def get_forecasts_without_accuracy(self):

        query = text("""
            SELECT
                fp.id,
                fp.store,
                fp.dept,
                fp.forecast_date,
                fp.predicted_sales
            FROM forecast_predictions fp

            LEFT JOIN forecast_accuracy fa
                ON fp.store = fa.store
                AND fp.dept = fa.dept
                AND fp.forecast_date = fa.forecast_date

            WHERE fa.id IS NULL
        """)

        with engine.connect() as conn:

            rows = conn.execute(query).mappings().all()

        return rows
    
    def get_recent_forecasts(
        self,
        limit=10
    ):

        query = text("""
            SELECT
                store,
                dept,
                forecast_date,
                predicted_sales
            FROM forecast_predictions
            ORDER BY created_at DESC
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
        
    def get_forecast_by_id(
        self,
        forecast_id
    ):

        query = text("""
            SELECT
                id,
                store,
                dept,
                forecast_date,
                predicted_sales,
                model_version
            FROM forecast_predictions
            WHERE id = :forecast_id
        """)

        with engine.connect() as conn:

            row = conn.execute(
                query,
                {
                    "forecast_id": forecast_id
                }
            ).mappings().first()

        return row

