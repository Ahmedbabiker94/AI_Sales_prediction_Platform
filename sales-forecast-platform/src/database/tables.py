from sqlalchemy import Table, Column, Integer, Float, Date, MetaData, Boolean

metadata = MetaData()

sales_history = Table(
    "sales_history",
    metadata,

    Column("id", Integer, primary_key=True, autoincrement=True),

    Column("date", Date, nullable=False),
    Column("store", Integer, nullable=False),
    Column("dept", Integer, nullable=False),

    Column("weekly_sales", Float, nullable=True),

    Column("is_holiday", Boolean, default=False),

    # optional context features (important later)
    Column("temperature", Float),
    Column("fuel_price", Float),
    Column("cpi", Float),
    Column("unemployment", Float),
)