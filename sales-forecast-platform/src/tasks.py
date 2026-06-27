from src.celery_app import celery_app

from src.services.recursive_forecast_service import (
    RecursiveForecastService
)

from src.core.logger import (
    get_logger
)

logger = get_logger("celery")

forecast_service = (
    RecursiveForecastService()
)


@celery_app.task
def test_task(x, y):

    return x + y


@celery_app.task
def forecast_next_week_task(
    store,
    dept
):

    logger.info(
        f"Async forecast started | Store={store} | Dept={dept}"
    )

    predictions = (
        forecast_service.forecast(
            store=store,
            dept=dept,
            weeks=1
        )
    )

    prediction = float(
        predictions[0]
    )

    result = {

        "Store": store,

        "Dept": dept,

        "predicted_units": round(
            max(
                0,
                prediction
            ),
            2
        ),

        "model_version": "production"
    }

    logger.info(
        f"Async forecast completed | Store={store} | Dept={dept}"
    )

    return result