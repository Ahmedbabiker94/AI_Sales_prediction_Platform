from sqlalchemy import (
    Table,
    Column,
    Integer,
    Float,
    Date,
    DateTime,
    String,
    Boolean,
    MetaData,
    func
)


metadata = MetaData()


# ============================================================
# SALES HISTORY
# ============================================================

sales_history = Table(
    "sales_history",
    metadata,

    Column(
        "id",
        Integer,
        primary_key=True,
        autoincrement=True
    ),

    Column(
        "date",
        Date,
        nullable=False
    ),

    Column(
        "store",
        Integer,
        nullable=False
    ),

    Column(
        "dept",
        Integer,
        nullable=False
    ),

    Column(
        "weekly_sales",
        Float,
        nullable=True
    ),

    Column(
        "is_holiday",
        Boolean,
        default=False
    ),

    Column(
        "temperature",
        Float
    ),

    Column(
        "fuel_price",
        Float
    ),

    Column(
        "cpi",
        Float
    ),

    Column(
        "unemployment",
        Float
    )
)


# ============================================================
# STORE METADATA
# ============================================================

store_metadata = Table(
    "store_metadata",
    metadata,

    Column(
        "store",
        Integer,
        primary_key=True
    ),

    Column(
        "type",
        String(10),
        nullable=False
    ),

    Column(
        "size",
        Integer,
        nullable=False
    )
)


# ============================================================
# FORECAST PREDICTIONS
# ============================================================

forecast_predictions = Table(
    "forecast_predictions",
    metadata,

    Column(
        "id",
        Integer,
        primary_key=True,
        autoincrement=True
    ),

    Column(
        "store",
        Integer,
        nullable=False
    ),

    Column(
        "dept",
        Integer,
        nullable=False
    ),

    Column(
        "forecast_date",
        Date,
        nullable=False
    ),

    Column(
        "predicted_sales",
        Float,
        nullable=False
    ),

    Column(
        "model_version",
        String(100),
        nullable=False
    ),

    # PostgreSQL automatically records the insertion time.
    # ForecastRepository does not provide created_at explicitly.
    Column(
        "created_at",
        DateTime,
        nullable=False,
        server_default=func.now()
    )
)


# ============================================================
# FORECAST ACCURACY
# ============================================================

forecast_accuracy = Table(
    "forecast_accuracy",
    metadata,

    Column(
        "id",
        Integer,
        primary_key=True,
        autoincrement=True
    ),

    Column(
        "store",
        Integer,
        nullable=False
    ),

    Column(
        "dept",
        Integer,
        nullable=False
    ),

    Column(
        "forecast_date",
        Date,
        nullable=False
    ),

    Column(
        "predicted_sales",
        Float,
        nullable=False
    ),

    Column(
        "actual_sales",
        Float,
        nullable=False
    ),

    Column(
        "absolute_error",
        Float,
        nullable=False
    ),

    Column(
        "percentage_error",
        Float,
        nullable=False
    )
)


# ============================================================
# JOB EXECUTION HISTORY
# ============================================================

job_execution_history = Table(
    "job_execution_history",
    metadata,

    Column(
        "id",
        Integer,
        primary_key=True,
        autoincrement=True
    ),

    Column(
        "job_name",
        String(100),
        nullable=False
    ),

    Column(
        "started_at",
        DateTime,
        nullable=False
    ),

    Column(
        "finished_at",
        DateTime
    ),

    Column(
        "duration_seconds",
        Float
    ),

    Column(
        "status",
        String(50),
        nullable=False
    ),

    Column(
        "rows_processed",
        Integer,
        default=0
    ),

    Column(
        "error_message",
        String
    )
)


# ============================================================
# JOB STATUS
# ============================================================

job_status = Table(
    "job_status",
    metadata,

    Column(
        "job_name",
        String(100),
        primary_key=True
    ),

    Column(
        "last_run",
        DateTime
    ),

    Column(
        "status",
        String(50),
        nullable=False
    )
)


# ============================================================
# SCHEDULER LOCK
# ============================================================

scheduler_lock = Table(
    "scheduler_lock",
    metadata,

    Column(
        "scheduler_name",
        String(100),
        primary_key=True
    ),

    Column(
        "locked",
        Boolean,
        nullable=False,
        default=False
    ),

    Column(
        "locked_at",
        DateTime
    ),

    Column(
        "hostname",
        String(255)
    )
)