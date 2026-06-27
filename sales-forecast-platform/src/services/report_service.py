from src.services.dashboard_service import (
    DashboardService
)

from src.pdf_report import (
    save_report_as_pdf
)


class ReportService:

    def __init__(self):

        self.dashboard_service = (
            DashboardService()
        )

    def generate_report(self):

        accuracy = (
            self.dashboard_service
            .get_accuracy_summary()
        )

        worst_forecasts = (
            self.dashboard_service
            .get_worst_forecasts(10)
        )

        insights = (
            self.dashboard_service
            .get_forecast_insights()
        )

        report_text = f"""
SALES FORECAST REPORT

================================

ACCURACY SUMMARY

Average Absolute Error:
{accuracy.get("avg_absolute_error", 0)}

Average Percentage Error:
{accuracy.get("avg_percentage_error", 0)}

Total Forecasts:
{accuracy.get("total_forecasts", 0)}

================================

FORECAST INSIGHTS

{insights["summary"]}

"""

        for item in insights["insights"]:

            report_text += f"\n- {item}"

        report_text += "\n\n================================\n"
        report_text += "\nWORST FORECASTS\n\n"

        for row in worst_forecasts:

            report_text += (
                f"Store={row['store']} "
                f"Dept={row['dept']} "
                f"Error={row['percentage_error']:.2f}%\n"
            )

        pdf_path = save_report_as_pdf(
            report_text
        )

        return {
            "report_path": str(pdf_path)
        }