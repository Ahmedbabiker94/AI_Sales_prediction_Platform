import pandas as pd

from sqlalchemy import text

from src.database.db import engine

df = pd.read_csv("data\walmart_cleaned.csv")

df["Date"] = pd.to_datetime(df["Date"])

df["IsHoliday"] = (
    df["IsHoliday"]
    .astype(bool)
)

df = df.rename(
    columns={
        "Date": "date",
        "Store": "store",
        "Dept": "dept",
        "Weekly_Sales": "weekly_sales",
        "IsHoliday": "is_holiday",
        "Temperature": "temperature",
        "Fuel_Price": "fuel_price",
        "CPI": "cpi",
        "Unemployment": "unemployment"
    }
)

columns_to_keep = [
    "date",
    "store",
    "dept",
    "weekly_sales",
    "is_holiday",
    "temperature",
    "fuel_price",
    "cpi",
    "unemployment"
]

df = df[columns_to_keep]

df.to_sql(
    "sales_history",
    engine,
    if_exists="append",
    index=False
)

print(
    f"Inserted {len(df)} rows"
)