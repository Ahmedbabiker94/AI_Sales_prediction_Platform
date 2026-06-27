from prometheus_client import (
    Counter,
    Histogram,
    Gauge
)

# ==================================================
# Forecast API
# ==================================================

FORECAST_COUNT = Counter(
    "forecast_requests_total",
    "Total forecast requests"
)

FORECAST_DURATION = Histogram(
    "forecast_duration_seconds",
    "Forecast execution duration"
)

# ==================================================
# Background Jobs
# ==================================================

FORECAST_JOB_COUNTER = Counter(
    "forecast_jobs_total",
    "Number of forecast jobs executed"
)

ACCURACY_JOB_COUNTER = Counter(
    "accuracy_jobs_total",
    "Number of accuracy jobs executed"
)

REPORT_GENERATED = Counter(
    "reports_generated_total",
    "Number of generated reports"
)

JOB_DURATION = Histogram(
    "job_duration_seconds",
    "Background job execution duration",
    ["job_name"]
)

# ==================================================
# Accuracy
# ==================================================

ACCURACY_EVALUATIONS = Counter(
    "accuracy_evaluations_total",
    "Forecast evaluations"
)

# ==================================================
# Model Monitoring
# ==================================================

MODEL_PREDICTIONS = Counter(
    "model_predictions_total",
    "Model predictions"
)

# ==================================================
# Health
# ==================================================

DATABASE_STATUS = Gauge(
    "database_status",
    "Database status"
)

API_STATUS = Gauge(
    "api_status",
    "API status"
)