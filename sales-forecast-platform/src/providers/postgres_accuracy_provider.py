from src.providers.accuracy_storage_provider import (
    AccuracyStorageProvider
)

from src.services.forecast_accuracy_persistence_service import (
    ForecastAccuracyPersistenceService
)


class PostgresAccuracyProvider(
    AccuracyStorageProvider
):

    def __init__(self):

        self.service = (
            ForecastAccuracyPersistenceService()
        )

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

        self.service.save(
            store=store,
            dept=dept,
            forecast_date=forecast_date,
            predicted_sales=predicted_sales,
            actual_sales=actual_sales,
            absolute_error=absolute_error,
            percentage_error=percentage_error
        )