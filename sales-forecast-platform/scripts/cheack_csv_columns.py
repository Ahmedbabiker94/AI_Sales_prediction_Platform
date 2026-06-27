# scripts/check_csv_columns.py

import pandas as pd

df = pd.read_csv(
    "data/walmart_cleaned.csv"
)

print(df.columns.tolist())
print(df.head())