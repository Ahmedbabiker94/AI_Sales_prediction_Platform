from src.celery_app import celery_app

import logging
from src.runtime.bootstrap import Runtime, require_runtime

logger = logging.getLogger(__name__)
_runtime = Runtime()


def configure_runtime(runtime):
    """Protected worker startup configuration; never accept factories in task data."""
    global _runtime
    _runtime = require_runtime(runtime)


@celery_app.task(name="src.tasks.test_task")
def test_task(x, y):

    return x + y


@celery_app.task(name="src.tasks.forecast_next_week_task")
def forecast_next_week_task(
    store,
    dept
):

    forecast_service = _runtime.get("recursive_forecast")

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