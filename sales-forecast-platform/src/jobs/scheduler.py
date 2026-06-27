from apscheduler.schedulers.blocking import (
    BlockingScheduler
)

from src.jobs.forecast_accuracy_job import (
    ForecastAccuracyJob
)
from src.jobs.forecast_job import (
    ForecastJob
)
from src.jobs.job_registry import (
    JOB_REGISTRY
)

@scheduler.scheduled_job(
    "interval",
    minutes=60
)
def run_forecast_job():

    JOB_REGISTRY[
        "forecast_job"
    ].run()


@scheduler.scheduled_job(
    "interval",
    minutes=10
)
def run_accuracy_job():

    JOB_REGISTRY[
        "accuracy_job"
    ].run()


@scheduler.scheduled_job(
    "interval",
    minutes=120
)
def run_report_job():

    JOB_REGISTRY[
        "report_job"
    ].run()