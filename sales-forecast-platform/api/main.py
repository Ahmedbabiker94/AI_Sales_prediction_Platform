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

import sys
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import date
from src.db import init_db, log_prediction, log_forecast
# import mlflow.sklearn
import mlflow.pyfunc
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import timedelta
from api.schemas import ForecastRequest, ForecastResponse, ForecastItem

# Ensure src/ is importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
SALES_CSV = ROOT / "data" / "walmart_cleaned.csv"

from features import FEATURE_COLS, add_calendar_features, encode_categoricals
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
RUN_ID ="a262e5b434f643958cbf3d938d2200eb"
MODEL_URI = "runs:/a262e5b434f643958cbf3d938d2200eb/model"
MODEL_VERSION = "run-artifact"
_model = None


def _load_model():
    global _model
    try:
        tracking_path =(ROOT/ "mlruns").resolve()
        tracking_uri = tracking_path.as_uri()
        print(f"ROOT ={ROOT}")
        print(f"Tracking Path ={tracking_path}")
        print(f"Tracking URI: {tracking_uri}")
        print(f"Model URI: {MODEL_URI}")

        mlflow.set_tracking_uri(tracking_uri)
        _model = mlflow.pyfunc.load_model(MODEL_URI)

        print(f"✅ Model loaded from {MODEL_URI}")
    except Exception as e:
        import traceback
        print("⚠️ Could not load model")
        print(f"Error: {e}")
        traceback.print_exc()
        _model = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _load_model()
    yield


app = FastAPI(
    title="Sales Forecast API",
    description="XGBoost-based daily sales forecasting service backed by MLflow.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _build_feature_row(req: PredictRequest) -> dict:
    raw_df = pd.DataFrame([{
        "Store": req.Store,
        "Dept": req.Dept,
        "Type": req.Type,
        "Size": req.Size,
        "Temperature": req.Temperature,
        "Fuel_Price": req.Fuel_Price,
        "CPI": req.CPI,
        "Unemployment": req.Unemployment,
        "MarkDown1": req.MarkDown1,
        "MarkDown2": req.MarkDown2,
        "MarkDown3": req.MarkDown3,
        "MarkDown4": req.MarkDown4,
        "MarkDown5": req.MarkDown5,
        "IsHoliday": req.IsHoliday,
        "Date": pd.to_datetime(req.Date)
    }])
    
    eng_df = add_calendar_features(raw_df)
    eng_df = encode_categoricals(eng_df)
    row = eng_df.iloc[0].to_dict()

    row["lag_1"] = req.lag_1 or 0.0
    row["lag_2"] = req.lag_2 or 0.0
    row["lag_4"] = req.lag_4 or 0.0
    row["lag_52"] = req.lag_52 or 0.0
    row["rolling_mean_4"] = req.rolling_mean_4 or 0.0
    row["rolling_mean_12"] = req.rolling_mean_12 or 0.0
    row["rolling_std_4"] = req.rolling_std_4 or 0.0
    row["sales_trend"] = req.sales_trend or 0.0
    
    return row
    #last known raw helper
def _get_last_known_row(store: int, dept: int):
    df = pd.read_csv(SALES_CSV)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")

    filtered = df[(df["Store"] == store) & (df["Dept"] == dept)].copy()
    if filtered.empty:
        return None

    filtered = filtered.sort_values("Date")
    return filtered.iloc[-1]

#build the forcast raw helper

def _build_forecast_row(last_row, future_date):
    raw_df = pd.DataFrame([{
        "Store": int(last_row["Store"]),
        "Dept": int(last_row["Dept"]),
        "Type": str(last_row["Type"]) if "Type" in last_row else "A",
        "Size": float(last_row["Size"]),
        "Temperature": float(last_row["Temperature"]),
        "Fuel_Price": float(last_row["Fuel_Price"]),
        "CPI": float(last_row["CPI"]),
        "Unemployment": float(last_row["Unemployment"]),
        "MarkDown1": float(last_row.get("MarkDown1", 0.0)),
        "MarkDown2": float(last_row.get("MarkDown2", 0.0)),
        "MarkDown3": float(last_row.get("MarkDown3", 0.0)),
        "MarkDown4": float(last_row.get("MarkDown4", 0.0)),
        "MarkDown5": float(last_row.get("MarkDown5", 0.0)),
        "IsHoliday": bool(last_row["IsHoliday"]),
        "Date": pd.to_datetime(future_date),
    }])

    eng_df = add_calendar_features(raw_df)
    eng_df = encode_categoricals(eng_df)
    row = eng_df.iloc[0].to_dict()

    # MVP placeholders from latest known values
    row["lag_1"] = float(last_row.get("Weekly_Sales", 0.0))
    row["lag_2"] = float(last_row.get("Weekly_Sales", 0.0))
    row["lag_4"] = float(last_row.get("Weekly_Sales", 0.0))
    row["lag_52"] = float(last_row.get("Weekly_Sales", 0.0))
    row["rolling_mean_4"] = float(last_row.get("Weekly_Sales", 0.0))
    row["rolling_mean_12"] = float(last_row.get("Weekly_Sales", 0.0))
    row["rolling_std_4"] = 0.0
    row["sales_trend"] = 1.0

    return row

def _predict_future_week(store: int, dept: int, future_start_date: pd.Timestamp):
    """
    Predict one future week starting from a real-world date.
    Uses the latest historical row only as a seed for non-date features and lag placeholders.
    """
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not loaded. Run training first.")

    last_row = _get_last_known_row(store, dept)
    if last_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No historical data found for Store={store}, Dept={dept}"
        )

    # Build one future row
    raw_df = pd.DataFrame([{
        "Store": int(last_row["Store"]),
        "Dept": int(last_row["Dept"]),
        "Type": str(last_row["Type"]) if "Type" in last_row else "A",
        "Size": float(last_row["Size"]),
        "Temperature": float(last_row["Temperature"]),
        "Fuel_Price": float(last_row["Fuel_Price"]),
        "CPI": float(last_row["CPI"]),
        "Unemployment": float(last_row["Unemployment"]),
        "MarkDown1": float(last_row.get("MarkDown1", 0.0)),
        "MarkDown2": float(last_row.get("MarkDown2", 0.0)),
        "MarkDown3": float(last_row.get("MarkDown3", 0.0)),
        "MarkDown4": float(last_row.get("MarkDown4", 0.0)),
        "MarkDown5": float(last_row.get("MarkDown5", 0.0)),
        "IsHoliday": bool(last_row["IsHoliday"]),
        "Date": pd.to_datetime(future_start_date),
    }])

    eng_df = add_calendar_features(raw_df)
    eng_df = encode_categoricals(eng_df)
    row = eng_df.iloc[0].to_dict()

    # Seed lag-like features from latest known weekly sales
    last_sales = float(last_row.get("Weekly_Sales", 0.0))
    row["lag_1"] = last_sales
    row["lag_2"] = last_sales
    row["lag_4"] = last_sales
    row["lag_52"] = last_sales
    row["rolling_mean_4"] = last_sales
    row["rolling_mean_12"] = last_sales
    row["rolling_std_4"] = 0.0
    row["sales_trend"] = 1.0

    X = pd.DataFrame([row])

    missing_cols = [col for col in FEATURE_COLS if col not in X.columns]
    for col in missing_cols:
        X[col] = 0.0

    X = X[FEATURE_COLS]

    pred = float(_model.predict(X)[0])
    pred = round(max(0, pred), 2)

    return pred
# ────────────────────────────────────────────────────────────────────────────
# Endpoints
# ────────────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["Monitoring"])
def health():
    return HealthResponse(
        status="ok",
        model_loaded=_model is not None,
        model_version=MODEL_VERSION,
    )


@app.post("/predict", response_model=PredictResponse, tags=["Predictions"])
def predict(req: PredictRequest):
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not loaded. Run training first.")

    row = _build_feature_row(req)

    # 1) Create dataframe first
    X = pd.DataFrame([row])

    # 2) Add missing columns
    missing_cols = [col for col in FEATURE_COLS if col not in X.columns]
    for col in missing_cols:
        X[col] = 0.0

    # 3) Keep correct column order
    X = X[FEATURE_COLS]

    pred = float(_model.predict(X)[0])
    pred = round(max(0,pred),2)

    log_prediction(
        store = req.Store,
        dept = req.Dept,
        input_date = req.Date,
        predicted_units = pred,
        model_version = MODEL_VERSION
    )

    return PredictResponse(
        predicted_units= pred,
        model_version=MODEL_VERSION,
        Store=req.Store,
        Dept=req.Dept,
        Date=req.Date,
    )


@app.post("/batch-predict", response_model=BatchPredictResponse, tags=["Predictions"])
def batch_predict(req: BatchPredictRequest):
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not loaded. Run training first.")

    rows = [_build_feature_row(r) for r in req.rows]

    # 1) Create dataframe first
    X = pd.DataFrame(rows)

    # 2) Add missing columns
    missing_cols = [col for col in FEATURE_COLS if col not in X.columns]
    for col in missing_cols:
        X[col] = 0.0

    # 3) Keep correct column order
    X = X[FEATURE_COLS]

    preds = _model.predict(X)

    results = []
    for r, p in zip(req.rows, preds):
        pred_value = round(max(0, float(p)), 2)

        log_prediction(
            store=r.Store,
            dept=r.Dept,
            input_date=r.Date,
            predicted_units=pred_value,
            model_version=MODEL_VERSION
        )
        results.append(
            PredictResponse(
                predicted_units=pred_value,
                model_version=MODEL_VERSION,
                Store=r.Store,
                Dept=r.Dept,
                Date=r.Date,
            )
        )

    return BatchPredictResponse(
        predictions=results,
        total_rows=len(results)
    )

# @app.post("/forecast-next-7-days", response_model=ForecastResponse, tags=["Forecasting"])
# def forecast_next_7_days(req: ForecastRequest):
#     if _model is None:
#         raise HTTPException(status_code=503, detail="Model not loaded. Run training first.")

#     last_row = _get_last_known_row(req.Store, req.Dept)
#     if last_row is None:
#         raise HTTPException(
#             status_code=404,
#             detail=f"No historical data found for Store={req.Store}, Dept={req.Dept}"
#         )

#     last_date = pd.to_datetime(last_row["Date"])
#     forecasts = []

#     for i in range(1, req.periods + 1):
#         future_date = last_date + timedelta(days=7 * i)  # weekly steps
#         row = _build_forecast_row(last_row, future_date)

#         X = pd.DataFrame([row])

#         missing_cols = [col for col in FEATURE_COLS if col not in X.columns]
#         for col in missing_cols:
#             X[col] = 0.0

#         X = X[FEATURE_COLS]

#         pred = float(_model.predict(X)[0])
#         pred = round(max(0, pred), 2)

#         # log to SQLite
#         log_forecast(
#             forecast_date=str(future_date.date()),
#             store=req.Store,
#             dept=req.Dept,
#             predicted_units=pred,
#             model_version=MODEL_VERSION
#             )

#         forecasts.append(
#             ForecastItem(
#                 Date=str(future_date.date()),
#                 Store=req.Store,
#                 Dept=req.Dept,
#                 predicted_units=pred,
#                 model_version=MODEL_VERSION,
#             )
#         )
#     return ForecastResponse(
#         forecasts=forecasts,
#         total_periods=len(forecasts)
#     )

@app.post("/forecast-next-week", response_model=ForecastWeekResponse, tags=["Forecasting"])
def forecast_next_week(req: ForecastWeekRequest):
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not loaded. Run training first.")

    today = pd.Timestamp.today().normalize()

    # Start of next week forecast window
    forecast_start = today
    forecast_end = forecast_start + timedelta(days=6)

    pred = _predict_future_week(req.Store, req.Dept, forecast_start)

    log_forecast(
        forecast_date=str(forecast_start.date()),
        store=req.Store,
        dept=req.Dept,
        predicted_units=pred,
        model_version=MODEL_VERSION
    )

    return ForecastWeekResponse(
        forecast=ForecastWeekItem(
            forecast_start_date=str(forecast_start.date()),
            forecast_end_date=str(forecast_end.date()),
            Store=req.Store,
            Dept=req.Dept,
            predicted_units=pred,
            model_version=MODEL_VERSION,
        )
    )

#endpoint for next month 

@app.post("/forecast-next-4-weeks", response_model=ForecastMonthResponse, tags=["Forecasting"])
def forecast_next_4_weeks(req: ForecastMonthRequest):
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not loaded. Run training first.")

    today = pd.Timestamp.today().normalize()
    forecasts = []

    for i in range(req.weeks):
        forecast_start = today + timedelta(days=7 * i)
        forecast_end = forecast_start + timedelta(days=6)

        pred = _predict_future_week(req.Store, req.Dept, forecast_start)

        log_forecast(
            forecast_date=str(forecast_start.date()),
            store=req.Store,
            dept=req.Dept,
            predicted_units=pred,
            model_version=MODEL_VERSION
        )

        forecasts.append(
            ForecastWeekItem(
                forecast_start_date=str(forecast_start.date()),
                forecast_end_date=str(forecast_end.date()),
                Store=req.Store,
                Dept=req.Dept,
                predicted_units=pred,
                model_version=MODEL_VERSION,
            )
        )

    return ForecastMonthResponse(
        forecasts=forecasts,
        total_weeks=len(forecasts)
    )