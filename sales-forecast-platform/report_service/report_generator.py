from src.repositories.accuracy_repository import (
    AccuracyRepository
)

from src.repositories.forecast_repository import (
    ForecastRepository
)


class ReportGenerator:

    def __init__(self):

        self.accuracy_repo = (
            AccuracyRepository()
        )

        self.forecast_repo = (
            ForecastRepository()
        )

    def generate_text_report(self):

        accuracy = (
            self.accuracy_repo
            .get_accuracy_summary()
        )

        forecasts = (
            self.forecast_repo
            .get_recent_forecasts(limit=10)
        )

        report = []

        report.append(
            "=== SALES FORECAST REPORT ==="
        )

        report.append("")

        report.append(
            f"Total Forecasts: {accuracy['total_forecasts']}"
        )

        report.append(
            f"Average Absolute Error: {accuracy['avg_absolute_error']}"
        )

        report.append(
            f"Average Percentage Error: {accuracy['avg_percentage_error']}"
        )

        report.append("")

        report.append(
            "Recent Forecasts:"
        )

        report.append("")

        for item in forecasts:

            report.append(
                f"Store={item['store']} "
                f"Dept={item['dept']} "
                f"Date={item['forecast_date']} "
                f"Prediction={item['predicted_sales']}"
            )

        return "\n".join(report)