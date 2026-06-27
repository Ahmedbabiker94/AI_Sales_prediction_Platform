from src.services.forecast_accuracy_service import (
    ForecastAccuracyService
)

from src.providers.sales_data_provider import (
    SalesDataProvider
)

from src.providers.forecast_provider import (
    ForecastProvider
)


class ForecastEvaluationService:

    def __init__(self):

        self.sales_provider = (
            SalesDataProvider()
        )

        self.forecast_provider = (
            ForecastProvider()
        )

        self.accuracy_service = (
            ForecastAccuracyService()
        )

    def evaluate_forecast(
        self,
        store,
        dept,
        forecast_date
    ):

        forecast = (
            self.forecast_provider
            .get_forecast(
                store=store,
                dept=dept,
                forecast_date=forecast_date
            )
        )

        if forecast is None:
            return

        actual_sales = (
            self.sales_provider
            .get_actual_sales(
                store=store,
                dept=dept,
                date=forecast_date
            )
        )

        if actual_sales is None:
            return

        self.accuracy_service.save_accuracy(
            store=store,
            dept=dept,
            forecast_date=forecast_date,
            predicted_sales=forecast["predicted_sales"],
            actual_sales=actual_sales
        )