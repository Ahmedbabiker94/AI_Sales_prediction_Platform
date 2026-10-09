from __future__ import annotations

from pydantic import Field
from enum import Enum

from .common import ContractModel


class ForecastFrequency(str, Enum):
    """Supported forecast output frequencies for the sales forecasting platform."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class ForecastConfig(ContractModel):
    """Configuration that controls the requested forecast horizon.

    The horizon is expressed in units of ``forecast_frequency``. For example,
    ``weekly + 8`` means the next 8 weekly forecast periods.
    """

    forecast_frequency: ForecastFrequency
    forecast_horizon: int = Field(ge=1)
