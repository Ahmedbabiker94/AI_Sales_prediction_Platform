from src.core.logger import (
    get_logger
)

logger = get_logger(
    "forecast"
)

logger.info(
    "Forecast service started"
)

logger.error(
    "Example error"
)