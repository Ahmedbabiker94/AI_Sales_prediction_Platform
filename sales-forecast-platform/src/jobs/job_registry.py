from src.jobs.forecast_job import (
    ForecastJob
)

from src.jobs.forecast_accuracy_job import (
    ForecastAccuracyJob
)

from src.jobs.report_generation_job import (
    ReportGenerationJob
)


JOB_REGISTRY = {

    "forecast_job":
        ForecastJob(),

    "accuracy_job":
        ForecastAccuracyJob(),

    "report_job":
        ReportGenerationJob()

}