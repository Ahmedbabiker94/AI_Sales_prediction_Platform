from src.providers.forecast_storage_provider import (
    ForecastStorageProvider
)

from src.repositories.forecast_repository import (
    ForecastRepository
)


class PostgresForecastProvider(
    ForecastStorageProvider
):

    def __init__(self):

        self.repo = (
            ForecastRepository()
        )

    def save_forecast(
        self,
        store,
        dept,
        forecast_date,
        predicted_sales,
        model_version
    ):

        self.repo.save_forecast(
            store=store,
            dept=dept,
            forecast_date=forecast_date,
            predicted_sales=predicted_sales,
            model_version=model_version
        )
    def get_forecasts_without_accuracy(self):

        return (
            self.repo
            .get_forecasts_without_accuracy()
    )