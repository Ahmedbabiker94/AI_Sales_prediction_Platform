from prometheus_client import Counter
from prometheus_client import Histogram

forecast_counter = Counter(
    "forecast_requests_total",
    "Total forecast requests"
)

forecast_duration = Histogram(
    "forecast_duration_seconds",
    "Forecast execution time"
)

report_counter = Counter(
    "report_generation_total",
    "Generated reports"
)

accuracy_counter = Counter(
    "forecast_accuracy_jobs_total",
    "Forecast accuracy evaluations"
)