
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

from celery.result import AsyncResult

from src.tasks import (
    forecast_next_week_task
)

from src.services.forecast_service import (
    ForecastService
)

from api.forecasting import (
    recursive_forecast
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
)

# ─────────────────────────────────────
# PATH SETUP
# ─────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(
    0,
    str(ROOT / "src")
)

# ─────────────────────────────────────
# APP SETUP
# ─────────────────────────────────────

app = FastAPI(
    title="AI Sales Forecast Platform",
    version="3.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────
# GLOBAL FORECAST SERVICE
# ─────────────────────────────────────

forecast_service = None

# ─────────────────────────────────────
# STARTUP
# ─────────────────────────────────────

@app.on_event("startup")
def startup_event():

    global forecast_service

    print(
        "Initializing Forecast Service..."
    )

    forecast_service = ForecastService(
        model_type="production"
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
        prediction=prediction,
        model_version="production"
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

    forecasts = recursive_forecast(
        predictor=forecast_service.predictor,
        req=req,
        start_date=today,
        weeks=1
    )

    items = []

    for item in forecasts:

        items.append(
            ForecastWeekItem(
                forecast_start_date=item[
                    "forecast_start_date"
                ],
                Store=item["Store"],
                Dept=item["Dept"],
                predicted_units=item[
                    "predicted_units"
                ],
                model_version=item[
                    "model_version"
                ]
            )
        )

    return ForecastWeekResponse(
        forecasts=items
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

    forecasts = recursive_forecast(
        predictor=forecast_service.predictor,
        req=req,
        start_date=today,
        weeks=req.weeks
    )

    items = []

    for item in forecasts:

        items.append(
            ForecastItem(
                forecast_start_date=item[
                    "forecast_start_date"
                ],
                Store=item["Store"],
                Dept=item["Dept"],
                predicted_units=item[
                    "predicted_units"
                ],
                model_version=item[
                    "model_version"
                ]
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

    task_result = AsyncResult(task_id)

    response = {
        "task_id": task_id,
        "status": task_result.status
    }

    if task_result.ready():

        response["result"] = (
            task_result.result
        )

    return response
