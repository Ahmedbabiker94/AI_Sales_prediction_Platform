
"""
main.py
--------
Enterprise FastAPI prediction service.

Application layer ONLY.
No direct MLflow.
No direct XGBoost.
No direct preprocessing.
"""

from pathlib import Path
import sys

import pandas as pd

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.celery_app import (
    celery_app
)
from src.tasks import (
    forecast_next_week_task
)

from src.services.forecast_service import (
    ForecastService
)

from src.services.recursive_forecast_service import (
    RecursiveForecastService
)

from api.schemas import (
    PredictRequest,
    PredictResponse,
    BatchPredictRequest,
    BatchPredictResponse,
    HealthResponse,
    ForecastRequest,
    ForecastResponse,
    ForecastItem,
    ForecastWeekRequest,
    ForecastWeekResponse,
    ForecastWeekItem,
    AccuracySummaryResponse,
    ForecastInsightsResponse,
    HealthMonitoringResponse,
    MetricsResponse,
    JobExecutionHistoryResponse,
    JobExecutionItem
)
import time

from starlette.middleware.base import (
    BaseHTTPMiddleware
)

from src.core.logger import (
    api_logger
)
# from src.core.metrics import (
#     metrics_service
# )
from src.core.model_monitor import (
    model_monitor
)
from src.services.dashboard_service import (
    DashboardService
)
from src.insights import (
    generate_forecast_insights
)
from src.services.report_service import (
    ReportService
)
from src.services.monitoring_service import (
    MonitoringService
)
from src.services.monitoring_service import (
    MonitoringService
)
from fastapi.responses import Response

from prometheus_client import (
    generate_latest,
    CONTENT_TYPE_LATEST
)

# ─────────────────────────────────────
# PATH SETUP
# ─────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(
    0,
    str(ROOT / "src")
)

#_____________________________________
# class Middleware 
#______________________________________

class RequestLoggingMiddleware(
    BaseHTTPMiddleware
):

    async def dispatch(
        self,
        request,
        call_next
    ):

        start_time = time.time()

        response = await call_next(
            request
        )

        duration = (
            time.time()
            -
            start_time
        )

        api_logger.info(
            f"{request.method} "
            f"{request.url.path} "
            f"Status={response.status_code} "
            f"Duration={duration:.3f}s"
        )

        return response

# ─────────────────────────────────────
# APP SETUP
# ─────────────────────────────────────

app = FastAPI(
    title="AI Sales Forecast Platform",
    version="3.0.0"
)

from src.core.logger import (
    error_logger
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    RequestLoggingMiddleware
)

# ─────────────────────────────────────
# GLOBAL FORECAST SERVICE
# ─────────────────────────────────────

forecast_service = None
recursive_forecast_service = None
dashboard_service = DashboardService()
# dashboard_service = None
report_service = ReportService()
# monitoring_service = MonitoringService()
monitoring_service = (
    MonitoringService()
)
# ─────────────────────────────────────
# STARTUP
# ─────────────────────────────────────

@app.on_event("startup")
def startup_event():

    global forecast_service
    global recursive_forecast_service
    global dashboard_service

    dashboard_service = (
        DashboardService()
    )

    print(
        "Initializing Forecast Service..."
    )

    forecast_service = ForecastService(
    model_type="production"
    )

    recursive_forecast_service = (
        RecursiveForecastService()
    )
    print(
        "Forecast Service initialized."
    )
    

# ─────────────────────────────────────
# HEALTH
# ─────────────────────────────────────

@app.get(
    "/health",
    response_model=HealthResponse
)
def health():

    return HealthResponse(
        status="ok",
        model_loaded=True,
        model_version="production"
    )

# ─────────────────────────────────────
# SINGLE PREDICTION
# ─────────────────────────────────────

@app.post(
    "/predict",
    response_model=PredictResponse
)
def predict(req: PredictRequest):

    df = pd.DataFrame([
        req.model_dump()
    ])

    prediction = (
        forecast_service.forecast_single(df)
    )

    prediction = round(
        max(0, prediction),
        2
    )

    return PredictResponse(

        predicted_units=prediction,

        model_version="production",

        Store=req.Store,

        Dept=req.Dept,

        Date=req.Date
    )
# ─────────────────────────────────────
# BATCH PREDICTION
# ─────────────────────────────────────

@app.post(
    "/batch_predict",
    response_model=BatchPredictResponse
)
def batch_predict(req: BatchPredictRequest):

    rows = [
        item.model_dump()
        for item in req.items
    ]

    df = pd.DataFrame(rows)

    predictions = (
        forecast_service.forecast_dataframe(df)
    )

    predictions = [
        round(max(0, float(p)), 2)
        for p in predictions
    ]

    return BatchPredictResponse(
        predictions=predictions,
        model_version="production"
    )

# ─────────────────────────────────────
# FORECAST WEEK
# ─────────────────────────────────────
@app.post(
    "/forecast/week",
    response_model=ForecastWeekResponse
)
def forecast_week(req: ForecastWeekRequest):

    today = (
        pd.Timestamp.today()
        .normalize()
    )

    predictions = (
        recursive_forecast_service.forecast(
            store=req.Store,
            dept=req.Dept,
            weeks=1
        )
    )

    return ForecastWeekResponse(
        forecasts=[
            ForecastWeekItem(
                forecast_start_date=str(
                    today.date()
                ),
                Store=req.Store,
                Dept=req.Dept,
                predicted_units=predictions[0],
                model_version="production"
            )
        ]
    )
# ─────────────────────────────────────
# MULTI WEEK FORECAST
# ─────────────────────────────────────
@app.post(
    "/forecast",
    response_model=ForecastResponse
)
def forecast(req: ForecastRequest):

    today = (
        pd.Timestamp.today()
        .normalize()
    )

    predictions = (
        recursive_forecast_service.forecast(
            store=req.Store,
            dept=req.Dept,
            weeks=req.weeks
        )
    )

    items = []

    for i, prediction in enumerate(predictions):

        forecast_date = (
            today +
            pd.Timedelta(days=7 * i)
        )

        items.append(
            ForecastItem(
                forecast_start_date=str(
                    forecast_date.date()
                ),
                Store=req.Store,
                Dept=req.Dept,
                predicted_units=prediction,
                model_version="production"
            )
        )

    return ForecastResponse(
        forecasts=items
    )    
# ─────────────────────────────────────
# ASYNC FORECAST
# ─────────────────────────────────────

@app.post("/forecast/async")
def forecast_async(
    store: int,
    dept: int
):

    task = forecast_next_week_task.delay(
        store,
        dept
    )

    return {
        "task_id": task.id,
        "status": "submitted"
    }

# ─────────────────────────────────────
# TASK STATUS
# ─────────────────────────────────────
@app.get("/task/{task_id}")
def task_status(task_id: str):

    task_result = (
        celery_app.AsyncResult(task_id)
    )

    response = {
        "task_id": task_id,
        "status": task_result.status
    }

    if task_result.ready():

        response["result"] = (
            task_result.result
        )

    return response

#-------------------------
#metrics API 
#-----------------------
# @app.get("/metrics")
# def metrics():

#     return (
#         metrics_service
#         .get_metrics()
#     )

#________________________________
#Model Status
#________________________________
@app.get("/model/stats")
def model_stats():

    return (
        model_monitor
        .get_stats()
    )
#_____________________
#dashboard accuracy 
#_________________
@app.get(
    "/dashboard/accuracy",
    response_model=AccuracySummaryResponse
)
def dashboard_accuracy():

    data = (
        dashboard_service
        .get_accuracy_summary()
    )

    return AccuracySummaryResponse(
        **data
    )

#__________________________
#worest forecast api 
#___________________________
@app.get(
    "/dashboard/worst-forecasts"
)
def worst_forecasts():

    return (
        dashboard_service
        .get_worst_forecasts()
    )

#______________________
#Forecast insights API
#______________________
@app.get(
    "/dashboard/insights",
    response_model=ForecastInsightsResponse
)
def dashboard_insights():

    result = (
        dashboard_service
        .get_forecast_insights()
    )

    return ForecastInsightsResponse(
        summary=result["summary"],
        insights=result["insights"],
        stats=result["stats"]
    )

#___________________
#report API
#___________________

@app.post("/dashboard/report")
def generate_dashboard_report():

    return (
        report_service
        .generate_report()
    )

#___________________
# Health monitering API 
#_____________________
@app.get(
    "/monitoring/health",
    response_model=HealthMonitoringResponse
)
def monitoring_health():

    result = (
        monitoring_service
        .get_health_status()
    )

    return HealthMonitoringResponse(

        api=result["api"],

        database=result["database"],

        scheduler=result["scheduler"],

        forecast_job=result["forecast_job"],

        accuracy_job=result["accuracy_job"],

        report_job=result["report_job"]

    )
#_______________________________
#matrics monitering API  
#_______________________________

# @app.get(
#     "/monitoring/metrics",
#     response_model=MetricsResponse
# )
# def monitoring_metrics():

#     metrics = (
#         monitoring_service
#         .get_metrics()
#     )

#     return MetricsResponse(
#         metrics=metrics
#     )
# _______________________
#prometheus edpoint 
#________________________

@app.get("/metrics")
def prometheus_metrics():

    return Response(
        generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )
#_____________________
#job history API  
#_____________________

@app.get(
    "/dashboard/job-history",
    response_model=JobExecutionHistoryResponse
)
def dashboard_job_history():

    rows = (
        dashboard_service
        .get_job_history()
    )

    return JobExecutionHistoryResponse(

        executions=[

            JobExecutionItem(

                job_name=row["job_name"],

                started_at=str(
                    row["started_at"]
                ),

                finished_at=(
                    str(row["finished_at"])
                    if row["finished_at"]
                    else None
                ),

                duration_seconds=row["duration_seconds"],

                status=row["status"],

                error_message=row["error_message"]

            )

            for row in rows

        ]

    )

from fastapi import Request
from fastapi.responses import JSONResponse


@app.exception_handler(Exception)
async def global_exception_handler(
    request: Request,
    exc: Exception
):

    error_logger.exception(
        f"Unhandled Exception: {str(exc)}"
    )

    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal Server Error"
        }
    )