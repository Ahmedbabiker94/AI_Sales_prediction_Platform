from src.jobs.forecast_job import ForecastJob
from src.jobs.forecast_accuracy_job import ForecastAccuracyJob
from src.jobs.report_generation_job import ReportGenerationJob


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
    }

}