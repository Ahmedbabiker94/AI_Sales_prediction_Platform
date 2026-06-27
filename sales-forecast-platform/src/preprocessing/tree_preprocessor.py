from src.features.feature_pipeline import (
    prepare_features,
    prepare_inference_features
)

from src.preprocessing.base_preprocessor import (
    BasePreprocessor
)

import pandas as pd

from src.features.feature_pipeline import (
    prepare_features,
    prepare_inference_features
)

class TreePreprocessor(BasePreprocessor):

    def fit_transform(self, df):
        return prepare_features(df)

    def transform(self, df):

        df = df.copy()

        df["Date"] = pd.to_datetime(
            df["Date"]
        )

        return prepare_inference_features(df)