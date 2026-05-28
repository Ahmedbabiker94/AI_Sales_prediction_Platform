
from src.db import SessionLocal

from src.models import Prediction, ForecastLog


def log_prediction(
    input_date,
    store,
    dept,
    predicted_units,
    model_version
):

    db = SessionLocal()

    try:

        row = Prediction(
            input_date=input_date,
            store=store,
            dept=dept,
            predicted_units=predicted_units,
            model_version=model_version
        )

        db.add(row)

        db.commit()

    finally:

        db.close()


def log_forecast(
    forecast_date,
    store,
    dept,
    predicted_units,
    model_version
):

    db = SessionLocal()

    try:

        row = ForecastLog(
            forecast_date=forecast_date,
            store=store,
            dept=dept,
            predicted_units=predicted_units,
            model_version=model_version
        )

        db.add(row)

        db.commit()

    finally:

        db.close()
