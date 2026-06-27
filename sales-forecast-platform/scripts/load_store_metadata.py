import pandas as pd

from src.database.db import engine

df = pd.read_csv("data/walmart_cleaned.csv")

stores = (
    df[
        ["Store", "Type", "Size"]
    ]
    .drop_duplicates()
)

stores.columns = [
    "store",
    "type",
    "size"
]

stores.to_sql(
    "store_metadata",
    engine,
    if_exists="append",
    index=False
)

print(
    f"Inserted {len(stores)} stores"
)