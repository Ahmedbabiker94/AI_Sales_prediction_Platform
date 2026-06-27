from src.repositories.forecast_repository import (
    ForecastRepository
)


class ForecastPersistenceService:

    def __init__(self):

        self.repo = (
            ForecastRepository()
        )

    def save(
        self,
        store,
        dept,
        forecast_date,
        predicted_sales,
        model_version="production"
    ):

        self.repo.save_forecast(
            store=store,
            dept=dept,
            forecast_date=forecast_date,
            predicted_sales=predicted_sales,
            model_version=model_version
        )