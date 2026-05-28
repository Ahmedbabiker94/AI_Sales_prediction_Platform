import pandas as pd

from src.pipelines.data_validation import validate_dataframe


df = pd.read_csv("data/walmart_cleaned.csv")

validate_dataframe(df)
