import pandas as pd
from datetime import timedelta

from src.features.feature_pipeline import prepare_inference_features
from src.ml.model_registry import (
    load_production_model
)

#MODEL_VERSION = "production_xgboost_v2"


def recursive_forecast(
    model,
    req,
    start_date,
    weeks=4
):

    forecasts = []

    last_sales = 20000

    for i in range(weeks):

        forecast_start = start_date + timedelta(days=7 * i)
        forecast_end = forecast_start + timedelta(days=6)

        future_row = {
            "Date": forecast_start,
            "Store": req.Store,
            "Dept": req.Dept,

            # categorical
            "Type": 1,

            # numeric
            "Size": 150000,
            "Temperature": 25.0,
            "Fuel_Price": 3.5,
            "CPI": 220.0,
            "Unemployment": 7.0,

            # markdowns
            "MarkDown1": 0.0,
            "MarkDown2": 0.0,
            "MarkDown3": 0.0,
            "MarkDown4": 0.0,
            "MarkDown5": 0.0,

            "IsHoliday": 0,

            # recursive history
            "Weekly_Sales": last_sales
        }

        future_df = pd.DataFrame([future_row])

        # IMPORTANT
        features = prepare_inference_features(future_df)

        prediction = float(model.predict(features)[0])

        prediction = round(prediction, 2)

        forecasts.append({
            "forecast_start_date": str(forecast_start.date()),
            "forecast_end_date": str(forecast_end.date()),
            "Store": req.Store,
            "Dept": req.Dept,
            "predicted_units": prediction,
            "model_version": MODEL_VERSION
        })

        last_sales = prediction

    return forecasts
