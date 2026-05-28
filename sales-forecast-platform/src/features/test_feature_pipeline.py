import pandas as pd

from src.features.feature_pipeline import (
    run_feature_pipeline
)

df = pd.read_csv(
    "data/walmart_cleaned.csv"
)

df = run_feature_pipeline(df)

print(df.head())

print(df.columns)
