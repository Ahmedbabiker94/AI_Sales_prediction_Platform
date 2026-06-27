from sqlalchemy import text

from src.database.db import engine


class ForecastAccuracyRepository:

    def save_accuracy(
        self,
        store,
        dept,
        forecast_date,
        predicted_sales,
        actual_sales,
        absolute_error,
        percentage_error
    ):

        query = text("""
            INSERT INTO forecast_accuracy (
                store,
                dept,
                forecast_date,
                predicted_sales,
                actual_sales,
                absolute_error,
                percentage_error
            )
            VALUES (
                :store,
                :dept,
                :forecast_date,
                :predicted_sales,
                :actual_sales,
                :absolute_error,
                :percentage_error
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
                    "actual_sales": actual_sales,
                    "absolute_error": absolute_error,
                    "percentage_error": percentage_error
                }
            )