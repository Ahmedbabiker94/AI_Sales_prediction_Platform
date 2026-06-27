from src.repositories.forecast_accuracy_repository import (
    ForecastAccuracyRepository
)


class ForecastAccuracyPersistenceService:

    def __init__(self):

        self.repo = (
            ForecastAccuracyRepository()
        )

    def save(
        self,
        store,
        dept,
        forecast_date,
        predicted_sales,
        actual_sales,
        absolute_error,
        percentage_error
    ):

        self.repo.save_accuracy(
            store=store,
            dept=dept,
            forecast_date=forecast_date,
            predicted_sales=predicted_sales,
            actual_sales=actual_sales,
            absolute_error=absolute_error,
            percentage_error=percentage_error
        )