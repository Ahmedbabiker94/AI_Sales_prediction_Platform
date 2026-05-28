
"""
main.py
-------
FastAPI prediction service.
Loads the Production model from MLflow registry at startup.

Endpoints:
  GET  /health          — liveness check + model info
  POST /predict         — single-row prediction
  POST /batch-predict   — batch prediction

Run:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
"""


from pathlib import Path
import sys

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from celery.result import AsyncResult

from src.tasks import (
    forecast_next_week_task
)

from src.ml.model_registry import (
    load_production_model
)

from src.features.feature_pipeline import (
    FEATURE_COLS,
    build_prediction_features
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
    ForecastMonthRequest,
    ForecastMonthResponse,
)

# ─────────────────────────────────────────────────────
# PATH SETUP
# ─────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(
    0,
    str(ROOT / "src")
)

# ─────────────────────────────────────────────────────
# APP SETUP
# ─────────────────────────────────────────────────────

app = FastAPI(
    title="AI Sales Forecast Platform",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────
# MODEL INFO
# ─────────────────────────────────────────────────────

MODEL_VERSION = "production_xgboost_v2"

_model = None

# ─────────────────────────────────────────────────────
# STARTUP EVENT
# ─────────────────────────────────────────────────────

@app.on_event("startup")
def startup_event():

    global _model

    try:

        print(
            "Loading production model..."
        )

        _model = load_production_model()

        print(
            "Production model loaded successfully."
        )

    except Exception as e:

        print(
            f"Model loading failed: {e}"
        )

        _model = None

# ─────────────────────────────────────────────────────
# MODEL ACCESS HELPER
# ─────────────────────────────────────────────────────

def get_model():

    global _model

    if _model is None:

        raise HTTPException(
            status_code=503,
            detail="Model not loaded."
        )

    return _model

# ─────────────────────────────────────────────────────
# HEALTH ENDPOINT
# ─────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
def health():

    model_loaded = _model is not None

    return HealthResponse(
        status="ok" if model_loaded else "model_not_loaded",
        model_loaded=model_loaded,
        model_version=MODEL_VERSION
    )

# ─────────────────────────────────────────────────────
# SINGLE PREDICTION
# ─────────────────────────────────────────────────────

@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):

    model = get_model()

    features = build_prediction_features(req)

    X = pd.DataFrame([features])

    missing_cols = [
        col for col in FEATURE_COLS
        if col not in X.columns
    ]

    for col in missing_cols:
        X[col] = 0.0

    X = X[FEATURE_COLS]

    prediction = float(
        model.predict(X)[0]
    )

    prediction = round(
        max(0, prediction),
        2
    )

    return PredictResponse(
        prediction=prediction,
        model_version=MODEL_VERSION
    )

# ─────────────────────────────────────────────────────
# BATCH PREDICTION
# ─────────────────────────────────────────────────────

@app.post(
    "/batch_predict",
    response_model=BatchPredictResponse
)
def batch_predict(req: BatchPredictRequest):

    model = get_model()

    rows = []

    for item in req.items:

        features = build_prediction_features(
            item
        )

        rows.append(features)

    X = pd.DataFrame(rows)

    missing_cols = [
        col for col in FEATURE_COLS
        if col not in X.columns
    ]

    for col in missing_cols:
        X[col] = 0.0

    X = X[FEATURE_COLS]

    preds = model.predict(X)

    predictions = [
        round(max(0, float(p)), 2)
        for p in preds
    ]

    return BatchPredictResponse(
        predictions=predictions,
        model_version=MODEL_VERSION
    )

# ─────────────────────────────────────────────────────
# FORECAST NEXT WEEK
# ─────────────────────────────────────────────────────

@app.post(
    "/forecast/week",
    response_model=ForecastWeekResponse
)
def forecast_week(req: ForecastWeekRequest):

    model = get_model()

    today = (
        pd.Timestamp.today()
        .normalize()
    )

    forecasts = recursive_forecast(
        model=model,
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

# ─────────────────────────────────────────────────────
# FORECAST MULTIPLE WEEKS
# ─────────────────────────────────────────────────────

@app.post(
    "/forecast",
    response_model=ForecastResponse
)
def forecast(req: ForecastRequest):

    model = get_model()

    today = (
        pd.Timestamp.today()
        .normalize()
    )

    forecasts = recursive_forecast(
        model=model,
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

# ─────────────────────────────────────────────────────
# ASYNC FORECAST ENDPOINT
# ─────────────────────────────────────────────────────

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

# ─────────────────────────────────────────────────────
# TASK STATUS ENDPOINT
# ─────────────────────────────────────────────────────

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
