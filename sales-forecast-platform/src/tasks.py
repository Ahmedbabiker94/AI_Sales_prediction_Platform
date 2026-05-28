import pandas as pd

from src.celery_app import (
    celery_app
)

from api.forecasting import (
    recursive_forecast
)

from src.logger import (
    log_forecast
)

from src.ml.model_registry import (
    load_production_model
)

# ─────────────────────────────────────────────────────
# GLOBAL MODEL
# ─────────────────────────────────────────────────────

model = load_production_model()

# ─────────────────────────────────────────────────────
# TEST TASK
# ─────────────────────────────────────────────────────

@celery_app.task
def test_task(x, y):

    return x + y

# ─────────────────────────────────────────────────────
# FORECAST TASK
# ─────────────────────────────────────────────────────

@celery_app.task
def forecast_next_week_task(
    store,
    dept
):

    class TempRequest:

        Store = store
        Dept = dept

    today = (
        pd.Timestamp.today()
        .normalize()
    )

    forecasts = recursive_forecast(
        model=model,
        req=TempRequest,
        start_date=today,
        weeks=1
    )

    item = forecasts[0]

    log_forecast(
        forecast_date=item[
            "forecast_start_date"
        ],
        store=item["Store"],
        dept=item["Dept"],
        predicted_units=item[
            "predicted_units"
        ],
        model_version=item[
            "model_version"
        ]
    )

    return item
