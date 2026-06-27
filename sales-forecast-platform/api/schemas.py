
from pydantic import BaseModel, Field
from typing import Optional, List


class PredictRequest(BaseModel):
    """Single-row prediction request."""
    Date: str = Field(..., example="2026-01-15", description="ISO date string YYYY-MM-DD")
    Store: int = Field(..., example=1)
    Dept: int = Field(..., example=1)
    Type: str = Field(..., example="A")
    Size: int = Field(..., example=151315)
    Temperature: float = Field(..., example=60.0)
    Fuel_Price: float = Field(..., example=3.5)
    CPI: float = Field(..., example=215.0)
    Unemployment: float = Field(..., example=7.5)
    MarkDown1: float = Field(0.0)
    MarkDown2: float = Field(0.0)
    MarkDown3: float = Field(0.0)
    MarkDown4: float = Field(0.0)
    MarkDown5: float = Field(0.0)
    IsHoliday: bool = Field(False)

    # Optional engineered lags (if supplied by caller, skip inference)
    lag_1: Optional[float] = None
    lag_2: Optional[float] = None
    lag_4: Optional[float] = None
    lag_52: Optional[float] = None
    rolling_mean_4: Optional[float] = None
    rolling_mean_12: Optional[float] = None
    rolling_std_4: Optional[float] = None
    sales_trend: Optional[float] = None


class PredictResponse(BaseModel):
    predicted_units: float
    model_version: str
    Store: int
    Dept: int
    Date: str


class BatchPredictRequest(BaseModel):
    rows: list[PredictRequest]


class BatchPredictResponse(BaseModel):
    predictions: list[PredictResponse]
    total_rows: int


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    model_version: str

# =========================
# Forecasting Schemas
# =========================

class ForecastRequest(BaseModel):
    Store: int = Field(..., example=1)
    Dept: int = Field(..., example=1)
    periods: int = Field(7, example=7, description="Number of future periods to forecast")


class ForecastItem(BaseModel):
    Date: str
    Store: int
    Dept: int
    predicted_units: float
    model_version: str


class ForecastResponse(BaseModel):
    forecasts: List[ForecastItem]
    total_periods: int

# =========================
# Weekly Forecast Schemas
# =========================


class ForecastWeekRequest(BaseModel):
    Store: int
    Dept: int


class ForecastWeekItem(BaseModel):
    forecast_start_date: str
    Store: int
    Dept: int
    predicted_units: float
    model_version: str

class ForecastWeekResponse(BaseModel):
    forecasts: list[ForecastWeekItem]


# =========================
# MULTI-WEEK FORECAST
# =========================

class ForecastRequest(BaseModel):
    Store: int
    Dept: int
    weeks: int = Field(
        4,
        ge=1,
        le=52,
        description="Number of future weeks"
    )


class ForecastItem(BaseModel):
    forecast_start_date: str
    Store: int
    Dept: int
    predicted_units: float
    model_version: str


class ForecastResponse(BaseModel):
    forecasts: list[ForecastItem]

class AccuracySummaryResponse(BaseModel):

    avg_absolute_error: float

    avg_percentage_error: float

    total_forecasts: int

class WorstForecastItem(BaseModel):

    store: int
    dept: int
    forecast_date: str

    actual_sales: float
    predicted_sales: float

    percentage_error: float

class ForecastInsightsResponse(BaseModel):

    summary: str

    insights: list[str]

    stats: dict

class HealthMonitoringResponse(BaseModel):

    api: str

    database: str

    forecast_job: str

    accuracy_job: str

    report_job: str

class MetricsResponse(BaseModel):

    metrics: dict
