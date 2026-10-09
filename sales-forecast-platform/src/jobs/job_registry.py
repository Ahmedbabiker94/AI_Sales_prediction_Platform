from src.jobs.forecast_job import ForecastJob
from src.jobs.forecast_accuracy_job import ForecastAccuracyJob
from src.jobs.report_generation_job import ReportGenerationJob
from src.jobs.production_model_sync_job import (
    ProductionModelSyncJob
)

JOB_REGISTRY = {

    "forecast_job": {
        "job": ForecastJob(),
        "trigger": "interval",
        "minutes": 60
    },

    "accuracy_job": {
        "job": ForecastAccuracyJob(),
        "trigger": "interval",
        "minutes": 10
    },

    "report_job": {
        "job": ReportGenerationJob(),
        "trigger": "interval",
        "minutes": 120
    },
    "production_model_sync_job": {
        "job": ProductionModelSyncJob(),
        "trigger": "interval",
        "minutes": 5
    }

}