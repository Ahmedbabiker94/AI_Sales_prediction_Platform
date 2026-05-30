import pandas as pd

from src.celery_app import (
    celery_app
)

from src.services.forecast_service import (
    ForecastService
)

from src.logger import (
    log_forecast
)

# ─────────────────────────────────────
# GLOBAL SERVICE
# ─────────────────────────────────────

forecast_service = ForecastService(
    model_type="production"
)

# ─────────────────────────────────────
# TEST TASK
# ─────────────────────────────────────

@celery_app.task
def test_task(x, y):

    return x + y

# ─────────────────────────────────────
# FORECAST TASK
# ─────────────────────────────────────

@celery_app.task
def forecast_next_week_task(
    store,
    dept
):

    df = pd.DataFrame([
        {
            "Store": store,
            "Dept": dept
        }
    ])

    prediction = (
        forecast_service.forecast_single(df)
    )

    result = {
        "Store": store,
        "Dept": dept,
        "predicted_units": round(
            max(0, float(prediction)),
            2
        ),
        "model_version": "production"
    }

    log_forecast(
        forecast_date=str(
            pd.Timestamp.today().date()
        ),
        store=store,
        dept=dept,
        predicted_units=result[
            "predicted_units"
        ],
        model_version="production"
    )

    return result
