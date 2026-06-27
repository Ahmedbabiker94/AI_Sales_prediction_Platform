from src.providers.postgres_sales_provider import (
    PostgresSalesProvider
)

from src.providers.postgres_accuracy_provider import (
    PostgresAccuracyProvider
)

from src.repositories.forecast_repository import (
    ForecastRepository
)


class ForecastAccuracyService:

    def __init__(self):

        self.sales_provider = (
            PostgresSalesProvider()
        )

        self.accuracy_provider = (
            PostgresAccuracyProvider()
        )

        self.forecast_repo = (
            ForecastRepository()
        )

    def evaluate_all_pending_forecasts(self):

        forecasts = (
            self.forecast_repo
            .get_forecasts_without_accuracy()
        )

        results = []

        for forecast in forecasts:

            actual_sales = (
                self.sales_provider
                .get_actual_sales(
                    store=forecast["store"],
                    dept=forecast["dept"],
                    date=forecast["forecast_date"]
                )
            )

            if actual_sales is None:
            
                print(
                    f"No actual sales found "
                    f"Store={forecast['store']} "
                    f"Dept={forecast['dept']} "
                    f"Date={forecast['forecast_date']}"
                )

    

                continue

            predicted_sales = (
                forecast["predicted_sales"]
            )

            absolute_error = abs(
                predicted_sales -
                actual_sales
            )

            if actual_sales == 0:

                percentage_error = 0

            else:

                percentage_error = (
                    absolute_error /
                    actual_sales
                ) * 100

            self.accuracy_provider.save_accuracy(
                store=forecast["store"],
                dept=forecast["dept"],
                forecast_date=forecast["forecast_date"],
                predicted_sales=predicted_sales,
                actual_sales=actual_sales,
                absolute_error=absolute_error,
                percentage_error=percentage_error
            )

            results.append(
                {
                    "store": forecast["store"],
                    "dept": forecast["dept"],
                    "absolute_error": absolute_error,
                    "percentage_error": percentage_error
                }
            )

        return results